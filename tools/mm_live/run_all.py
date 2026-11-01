"""逐平台真实注册，结果追加到 results.jsonl。

用法: python3 run_all.py [platform ...]
不带参数则跑全部 12 个平台。
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

from core.db import init_db

init_db()

from core import registry as platform_registry
from core.base_platform import RegisterConfig
from infrastructure.provider_settings_repository import ProviderSettingsRepository
from providers.registry import create_provider, load_all

OUT = Path(os.getenv("RESULT_FILE", "/app/working/mm_live/results.jsonl"))
OUT.parent.mkdir(parents=True, exist_ok=True)

DEFAULT_PW = os.getenv("DEFAULT_PW", "")
OTP_TIMEOUT = int(os.getenv("OTP_TIMEOUT", "150"))
CAPTCHA_SOLVER = os.getenv("CAPTCHA_SOLVER", "cfsolve")
MAIL_DOMAIN = os.getenv("MAIL_DOMAIN", "").strip()


def resolve_mail_provider() -> str:
    """MAIL_PROVIDER 显式指定 -> 设置页的默认 mailbox -> mailcatch。"""
    explicit = os.getenv("MAIL_PROVIDER", "").strip()
    if explicit:
        return explicit
    default = ProviderSettingsRepository().get_default_provider_key("mailbox")
    return default or "mailcatch"


MAIL_PROVIDER = resolve_mail_provider()


def build_mailbox():
    """内置 provider 走 create_mailbox 工厂，自注册 provider 走 create_provider。"""
    from core.base_mailbox import MAILBOX_FACTORY_REGISTRY, create_mailbox

    overrides = {"cfworker_domain": MAIL_DOMAIN} if MAIL_DOMAIN else None
    if MAIL_PROVIDER in MAILBOX_FACTORY_REGISTRY:
        return create_mailbox(MAIL_PROVIDER, overrides)
    settings = ProviderSettingsRepository()
    merged = settings.resolve_runtime_settings("mailbox", MAIL_PROVIDER, {})
    return create_provider("mailbox", MAIL_PROVIDER, merged)


def export_accounts() -> None:
    """每批成功后落一份可读导出，避免账号只躺在库里。"""
    import subprocess

    script = "/app/working/mm_live/tools/export_all_accounts.py"
    try:
        proc = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=120)
        if proc.returncode == 0:
            last = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "已导出"
            print(f"\n{last}")
        else:
            print(f"\n(账号导出失败: {proc.stderr.strip()[:200]})")
    except Exception as exc:  # noqa: BLE001
        print(f"\n(账号导出失败: {type(exc).__name__}: {exc})")


def record(entry: dict) -> None:
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_one(name: str) -> dict:
    cls = platform_registry.get(name)
    executors = list(cls.supported_executors or [])
    executor = "protocol" if "protocol" in executors else (executors[0] if executors else "protocol")

    config = RegisterConfig(
        executor_type=executor,
        captcha_solver=CAPTCHA_SOLVER,
        proxy=None,
        extra={
            "identity_provider": "mailbox",
            "mail_provider": MAIL_PROVIDER,
            "otp_timeout": OTP_TIMEOUT,
            "default_pw": DEFAULT_PW,
            "moonmath_key_alias": "live-test",
        },
    )
    platform = cls(config=config, mailbox=build_mailbox())
    platform.set_logger(lambda msg: print(f"    {msg}", flush=True))

    started = time.time()
    try:
        account = platform.register()
        elapsed = round(time.time() - started, 1)
        # 落库，让桌面客户端/API 能看到这次真实注册的账号
        saved_id = None
        try:
            from sqlmodel import Session, select

            from core.db import AccountModel, engine, save_account

            save_account(account)
            with Session(engine) as s:
                row = s.exec(
                    select(AccountModel)
                    .where(AccountModel.platform == name)
                    .where(AccountModel.email == account.email)
                ).first()
                saved_id = getattr(row, "id", None) if row else None
        except Exception as exc:  # noqa: BLE001
            print(f"    (落库失败: {type(exc).__name__}: {exc})")
        entry = {
            "platform": name,
            "display_name": cls.display_name,
            "ok": True,
            "elapsed_s": elapsed,
            "email": account.email,
            "password": account.password,
            "user_id": account.user_id,
            "token": account.token,
            "status": getattr(account.status, "value", str(account.status)),
            "extra": account.extra,
            "db_account_id": saved_id,
        }
        print(f"  ✓ {name} 成功 {elapsed}s  {account.email}")
        return entry
    except Exception as exc:  # noqa: BLE001
        elapsed = round(time.time() - started, 1)
        identity = getattr(platform, "_last_identity", None)
        entry = {
            "platform": name,
            "display_name": cls.display_name,
            "ok": False,
            "elapsed_s": elapsed,
            "email": getattr(identity, "email", "") if identity else "",
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc()[-1500:],
        }
        print(f"  ✗ {name} 失败 {elapsed}s  {type(exc).__name__}: {exc}")
        return entry


def main() -> int:
    load_all()
    platform_registry.load_all()
    names = sys.argv[1:] or sorted(platform_registry._registry)

    print(f"目标平台: {names}\n")
    done = []
    for name in names:
        print(f"===== {name} =====")
        entry = run_one(name)
        record(entry)
        done.append(entry)
        time.sleep(2)

    ok = [e for e in done if e["ok"]]
    if ok:
        export_accounts()
    print(f"\n汇总: {len(ok)}/{len(done)} 成功")
    for e in done:
        mark = "✓" if e["ok"] else "✗"
        print(f"  {mark} {e['platform']:16} {e['elapsed_s']:>6}s  "
              f"{e.get('email') or e.get('error', '')[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
