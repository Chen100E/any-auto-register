"""MoonMath 平台插件 — Clerk email_code 注册 + API key 签发。

站点是 Next.js + Clerk，注册为单因子邮箱验证码（无密码），Turnstile 由
cfsolve 在协议层解掉，全程不需要浏览器。
"""
from core.base_platform import BasePlatform, Account, AccountStatus, RegisterConfig
from core.base_mailbox import BaseMailbox
from core.registration import OtpSpec, ProtocolMailboxAdapter, RegistrationResult
from core.registry import register


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-4:]}"


@register
class MoonMathPlatform(BasePlatform):
    name = "moonmath"
    display_name = "MoonMath"
    version = "1.0.0"
    supported_executors = ["protocol"]
    supported_identity_modes = ["mailbox"]
    # cfsolve 是本地协议层求解，优先于任何云端打码服务
    protocol_captcha_order = ("cfsolve", "yescaptcha_api", "twocaptcha_api", "manual")
    capabilities = ["create_api_key", "query_state"]

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox

    # ------------------------------------------------------------------ 注册
    def _prepare_registration_password(self, password: str | None) -> str | None:
        """站点当前密码策略关闭，但把配置的密码记录下来，策略开启后可直接用。"""
        if password:
            return password
        extra = (self.config.extra or {}) if self.config else {}
        return str(extra.get("default_password") or "").strip() or ""

    def _map_result(self, result: dict) -> RegistrationResult:
        status = AccountStatus.REGISTERED
        if result.get("plan_required"):
            # 账号是好的，只是拿不到 key，仍记为已注册
            status = AccountStatus.REGISTERED
        return RegistrationResult(
            email=result["email"],
            password=result.get("password", ""),
            user_id=result.get("user_id", ""),
            status=status,
            token=result.get("api_key", ""),
            extra={
                "api_key": result.get("api_key", ""),
                "key_alias": result.get("key_alias", ""),
                "session_id": result.get("session_id", ""),
                "signup_id": result.get("signup_id", ""),
                "plan_required": bool(result.get("plan_required")),
                "plan_required_reason": result.get("plan_required_reason", ""),
                "key_error": result.get("key_error", ""),
            },
        )

    def build_protocol_mailbox_adapter(self):
        extra = (self.config.extra or {}) if self.config else {}

        def _worker(ctx, artifacts):
            from platforms.moonmath.protocol_mailbox import MoonMathProtocolMailboxWorker

            return MoonMathProtocolMailboxWorker(
                executor=artifacts.executor,
                log_fn=ctx.log,
                sitekey=str(extra.get("moonmath_sitekey") or ""),
                key_alias=str(extra.get("moonmath_key_alias") or ""),
            )

        def _runner(worker, ctx, artifacts):
            return worker.run(
                email=ctx.identity.email,
                password=ctx.password or "",
                otp_callback=artifacts.otp_callback,
                captcha_solver=artifacts.captcha_solver,
            )

        return ProtocolMailboxAdapter(
            result_mapper=lambda ctx, result: self._map_result(result),
            worker_builder=_worker,
            register_runner=_runner,
            otp_spec=OtpSpec(wait_message="等待验证码邮件..."),
            use_captcha=True,
            use_executor=True,
        )

    # ------------------------------------------------------------------ 校验
    def check_valid(self, account: Account) -> bool:
        api_key = (account.extra or {}).get("api_key", "")
        if not api_key:
            return False
        try:
            from core.executors.protocol import ProtocolExecutor

            ex = ProtocolExecutor(proxy=self.config.proxy if self.config else None)
            resp = ex.get(
                "https://zro.moonmath.ai/v1/models",
                headers={
                    "authorization": f"Bearer {api_key}",
                    "accept": "application/json",
                },
            )
            return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ 动作
    def get_platform_actions(self) -> list:
        return [
            {"id": "create_api_key", "label": "创建 API Key", "params": []},
            {"id": "query_state", "label": "查询账号状态", "params": []},
        ]

    def _handle_create_api_key(self, account: Account, params: dict) -> dict:
        """用已存 session 再建一把 key（Free 计划只认最新一把）。"""
        session_id = (account.extra or {}).get("session_id", "")
        if not session_id:
            return {"ok": False, "error": "账号没有 session_id，无法重新签发 key"}

        from core.executors.protocol import ProtocolExecutor
        from platforms.moonmath.core import MoonMathRegister

        try:
            ex = ProtocolExecutor(proxy=self.config.proxy if self.config else None)
            client = MoonMathRegister(executor=ex, log_fn=self.log)
            jwt = client.step5_session_jwt(session_id)
            created = client.step6_create_api_key(jwt, alias=params.get("alias") or "")
            api_key = str(created.get("token") or "")
            account.extra = {**(account.extra or {}), "api_key": api_key}
            account.token = api_key
            return {"ok": True, "data": {"api_key": api_key, "preview": _mask_secret(api_key)}}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def _handle_query_state(self, account: Account, params: dict) -> dict:
        extra = account.extra or {}
        api_key = str(extra.get("api_key") or "")
        return {
            "ok": True,
            "data": {
                "valid": self.check_valid(account),
                "api_key_preview": _mask_secret(api_key),
                "plan_required": bool(extra.get("plan_required")),
                "user_id": account.user_id,
            },
        }
