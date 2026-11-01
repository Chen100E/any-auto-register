"""MoonMath 协议邮箱注册 worker。"""
from __future__ import annotations

from typing import Callable, Optional

from platforms.moonmath.core import MoonMathRegister, PlanRequiredError

SIGNUP_PAGE = "https://accounts.zro.moonmath.ai/sign-up"


class MoonMathProtocolMailboxWorker:
    def __init__(self, *, executor, log_fn: Callable[[str], None] = print,
                 sitekey: str = "", key_alias: str = ""):
        self.client = MoonMathRegister(
            executor=executor,
            log_fn=log_fn,
            sitekey=sitekey,
            key_alias=key_alias,
        )
        self.log = log_fn

    def run(
        self,
        *,
        email: str,
        password: str = "",
        otp_callback: Optional[Callable[[], str]] = None,
        captcha_solver=None,
    ) -> dict:
        if captcha_solver is None:
            raise RuntimeError("MoonMath 注册需要验证码 provider（推荐 cfsolve）")

        # 1) 解 Turnstile —— 两个 sitekey 都可能被接受，依次试
        sitekeys = self.client.discover_sitekeys()
        signup_id = ""
        last_error: Exception | None = None
        for index, sitekey in enumerate(sitekeys, start=1):
            try:
                self.log(f"解 Turnstile（sitekey {index}/{len(sitekeys)}）...")
                captcha_token = str(
                    captcha_solver.solve_turnstile(SIGNUP_PAGE, sitekey) or ""
                ).strip()
                if not captcha_token:
                    raise RuntimeError("Turnstile 未返回 token")
                self.log(f"Turnstile token: {captcha_token[:24]}...（{len(captcha_token)} 字符）")

                # 2) 建 sign_up
                signup_id = self.client.step1_create_signup(email, captcha_token)
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self.log(f"sitekey {index} 失败: {exc}")
        if not signup_id:
            raise RuntimeError(f"创建 sign_up 全部失败: {last_error}")

        # 3) 发验证码 + 收验证码
        self.client.step2_prepare_email_code(signup_id)
        code = otp_callback() if otp_callback else input("OTP: ")
        code = str(code or "").strip()
        if not code:
            raise RuntimeError("未获取到验证码")

        # 4) 提交验证码
        payload = self.client.step3_verify_code(signup_id, code)
        identity = self.client.step4_identity(payload)
        self.log(f"session={identity['session_id']} user={identity['user_id']}")

        result = {
            "email": email,
            "password": password or "",
            "user_id": identity["user_id"],
            "session_id": identity["session_id"],
            "signup_id": signup_id,
            "api_key": "",
            "plan_required": False,
        }

        # 5) 建 key —— 站点对新号可能直接 402
        try:
            jwt = self.client.step5_session_jwt(identity["session_id"])
            created = self.client.step6_create_api_key(jwt)
            result["api_key"] = str(created.get("token") or "")
            result["key_alias"] = str(created.get("alias") or "")
            self.log(f"API Key: {result['api_key'][:10]}...")
        except PlanRequiredError as exc:
            result["plan_required"] = True
            result["plan_required_reason"] = str(exc)
            self.log(f"账号已建成，但站点不给建 key: {exc}")
        except Exception as exc:  # noqa: BLE001
            result["key_error"] = str(exc)
            self.log(f"建 key 失败: {exc}")

        return result
