"""合并 DB 账号与 results.jsonl 的凭据，导出成可直接用的文件。

DB 只存 platform/email/password/user_id；token、api_key、session 等在
results.jsonl 里，所以两边按 (platform, email) 合并。
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from core.db import init_db

init_db()

from sqlmodel import Session, select

from core.db import AccountModel, engine

RESULTS = Path("/app/working/mm_live/results.jsonl")
OUT_DIR = Path("/app/working/mm_live/accounts")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_results() -> list[dict]:
    if not RESULTS.exists():
        return []
    rows = []
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


results = load_results()
by_key: dict[tuple[str, str], dict] = {}
for r in results:
    if not r.get("ok"):
        continue
    key = (r.get("platform", ""), r.get("email", ""))
    # 同一账号多次跑，保留最新一条
    prev = by_key.get(key)
    if prev is None or r.get("elapsed_s", 0) <= prev.get("elapsed_s", 0):
        by_key[key] = r

with Session(engine) as session:
    db_rows = session.exec(select(AccountModel)).all()

accounts = []
for row in db_rows:
    key = (row.platform, row.email)
    hit = by_key.get(key, {})
    extra = hit.get("extra") or {}
    accounts.append({
        "platform": row.platform,
        "email": row.email,
        "password": row.password or "",
        "user_id": row.user_id or "",
        "token": hit.get("token", ""),
        "api_key": extra.get("api_key", ""),
        "session_id": extra.get("session_id", ""),
        "key_alias": extra.get("key_alias", ""),
        "status": hit.get("status", ""),
        "elapsed_s": hit.get("elapsed_s"),
        "db_id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "in_results": bool(hit),
    })

# results 里成功但库里没有的（落库失败或跨库），也一并留下
db_keys = {(a["platform"], a["email"]) for a in accounts}
for key, hit in by_key.items():
    if key in db_keys:
        continue
    extra = hit.get("extra") or {}
    accounts.append({
        "platform": hit.get("platform", ""),
        "email": hit.get("email", ""),
        "password": hit.get("password", ""),
        "user_id": hit.get("user_id", ""),
        "token": hit.get("token", ""),
        "api_key": extra.get("api_key", ""),
        "session_id": extra.get("session_id", ""),
        "key_alias": extra.get("key_alias", ""),
        "status": hit.get("status", ""),
        "elapsed_s": hit.get("elapsed_s"),
        "db_id": None,
        "created_at": "",
        "in_results": True,
    })

accounts.sort(key=lambda a: (a["platform"], a["email"]))

stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
(OUT_DIR / "accounts.json").write_text(
    json.dumps({"exported_at": stamp, "count": len(accounts), "accounts": accounts},
               ensure_ascii=False, indent=2),
    encoding="utf-8",
)

fields = ["platform", "email", "password", "user_id", "token", "api_key",
          "session_id", "key_alias", "status", "elapsed_s", "db_id", "created_at"]
with (OUT_DIR / "accounts.csv").open("w", newline="", encoding="utf-8") as fh:
    writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(accounts)

env_lines = [f"# 导出时间 {stamp}  共 {len(accounts)} 个账号"]
for a in accounts:
    slug = a["platform"].upper()
    idx = sum(1 for x in accounts[:accounts.index(a)] if x["platform"] == a["platform"]) + 1
    env_lines.append(f"{slug}_{idx}_EMAIL={a['email']}")
    if a["password"]:
        env_lines.append(f"{slug}_{idx}_PASSWORD={a['password']}")
    if a["token"]:
        env_lines.append(f"{slug}_{idx}_TOKEN={a['token']}")
    if a["api_key"]:
        env_lines.append(f"{slug}_{idx}_API_KEY={a['api_key']}")
(OUT_DIR / "accounts.env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")

md = [f"# 已注册账号（{len(accounts)} 个）", "", f"导出时间：{stamp}", ""]
current = None
for a in accounts:
    if a["platform"] != current:
        current = a["platform"]
        if md and md[-1] != "":
            md.append("")
        md.append(f"## {current}")
        md.append("")
        md.append("| 邮箱 | user_id | 凭据 | 耗时 |")
        md.append("| --- | --- | --- | --- |")
    cred = []
    if a["api_key"]:
        cred.append(f"api_key `{a['api_key']}`")
    if a["token"] and a["token"] != a["api_key"]:
        cred.append(f"token `{a['token'][:28]}…`")
    if a["session_id"]:
        cred.append(f"session `{a['session_id']}`")
    if a["password"]:
        cred.append(f"password `{a['password']}`")
    md.append(f"| {a['email']} | {a['user_id'] or '—'} | {' / '.join(cred) or '—'} | "
              f"{a['elapsed_s'] or '—'}s |")
md.append("")
(OUT_DIR / "accounts.md").write_text("\n".join(md), encoding="utf-8")

print(f"导出 {len(accounts)} 个账号 -> {OUT_DIR}")
for a in accounts:
    flags = []
    if a["token"]:
        flags.append("token")
    if a["api_key"]:
        flags.append("api_key")
    if not a["in_results"]:
        flags.append("仅DB")
    print(f"  {a['platform']:10} {a['email']:34} {'/'.join(flags) or '-'}")
