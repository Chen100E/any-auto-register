"""MoonMath (zro.moonmath.ai) 注册协议核心实现。

站点是 Next.js + Clerk，注册为单因子 email_code（无密码）：

  1. POST {CLERK}/v1/client/sign_ups                      建 sign_up，带 Turnstile token
  2. POST {CLERK}/v1/client/sign_ups/{id}/prepare_verification   strategy=email_code
  3. POST {CLERK}/v1/client/sign_ups/{id}/attempt_verification   strategy=email_code + code
  4. GET  {CLERK}/v1/client                                拿 session_id / user_id
  5. POST {CLERK}/v1/client/sessions/{sid}/tokens          签 __session JWT
  6. POST {APP}/api/api-keys                               建 API key

Clerk 的响应结构在不同版本间不一致（有时平铺、有时包一层 response），
下面统一用 _json() + _signup_of() 兼容。
"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

CLERK = "https://clerk.zro.moonmath.ai"
ACCOUNTS = "https://accounts.zro.moonmath.ai"
APP = "https://zro.moonmath.ai"

CLERK_JS_VERSION = "6"

#: 站点在 402 时给的新号门槛文案
PLAN_REQUIRED_MARKER = "active plan or usage pack"

#: Turnstile sitekey 会随前端构建变化，运行时从页面里抓
_SITEKEY_RE = re.compile(r"0x4AAAAAA[A-Za-z0-9_-]{8,}")


class PlanRequiredError(RuntimeError):
    """账号建成了，但站点不给建 key（需要订阅或流量包）。"""


class MoonMathRegister:
    """MoonMath 协议注册。"""

    def __init__(
        self,
        *,
        executor,
        log_fn: Callable[[str], None] = print,
        sitekey: str = "",
        key_alias: str = "",
    ):
        self.ex = executor
        self.log = log_fn
        self.sitekey = sitekey
        self.key_alias = key_alias
        self.widget_type = "smart"

    # ------------------------------------------------------------- 小工具
    @staticmethod
    def _json(resp) -> dict:
        try:
            data = resp.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _signup_of(payload: dict) -> dict:
        """从各种响应形态里挖出 sign_up 对象。"""
        for key in ("sign_up", "signUp"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        response = payload.get("response")
        if isinstance(response, dict):
            if isinstance(response.get("sign_up"), dict):
                return response["sign_up"]
            obj = str(response.get("object", ""))
            if obj.startswith("sign_up"):
                return response
            # 有的版本 response 直接就是 sign_up，只是没带 object
            if not obj and response.get("id"):
                return response
        if str(payload.get("object", "")).startswith("sign_up"):
            return payload
        return {}

    @staticmethod
    def _client_of(payload: dict) -> dict:
        client = payload.get("client")
        if isinstance(client, dict):
            return client
        response = payload.get("response")
        if isinstance(response, dict):
            if isinstance(response.get("client"), dict):
                return response["client"]
            if response.get("object") == "client":
                return response
        if payload.get("object") == "client":
            return payload
        return {}

    def _url(self, path: str) -> str:
        return f"{CLERK}{path}{'&' if '?' in path else '?'}_clerk_js_version={CLERK_JS_VERSION}"

    def _form_headers(self) -> dict:
        return {
            "content-type": "application/x-www-form-urlencoded",
            "origin": ACCOUNTS,
            "referer": f"{ACCOUNTS}/",
        }

    # ------------------------------------------------------- sitekey 发现
    def environment(self) -> dict:
        """Clerk 的 environment 里带着 Turnstile 的公开 key。"""
        resp = self.ex.get(self._url("/v1/environment"))
        return self._json(resp)

    def discover_sitekeys(self) -> list[str]:
        """按优先级返回候选 sitekey。

        注册页是 SPA，HTML 里没有 sitekey；权威来源是 Clerk 的 environment，
        它给出 captcha_public_key（smart/可见）和 captcha_public_key_invisible
        两把，签名策略不同，都可能被 sign_up 接受，所以两个都试。
        """
        if self.sitekey:
            return [self.sitekey]

        keys: list[str] = []
        try:
            display = self.environment().get("display_config") or {}
            for field in ("captcha_public_key", "captcha_public_key_invisible"):
                value = str(display.get(field) or "").strip()
                if value and value not in keys:
                    keys.append(value)
            self.widget_type = str(display.get("captcha_widget_type") or "smart")
        except Exception as exc:  # noqa: BLE001
            self.log(f"读取 Clerk environment 失败: {exc}")

        if not keys:
            for url in (f"{ACCOUNTS}/sign-up", f"{ACCOUNTS}/sign-in", APP):
                try:
                    resp = self.ex.get(url, headers={"accept": "text/html"})
                    for match in _SITEKEY_RE.finditer(resp.text or ""):
                        value = match.group(0)
                        if value not in keys:
                            keys.append(value)
                    if keys:
                        break
                except Exception as exc:  # noqa: BLE001
                    self.log(f"抓 sitekey 失败（{url}）: {exc}")

        if not keys:
            raise RuntimeError("未能发现 Turnstile sitekey，请在配置里显式提供")
        self.log(f"Turnstile sitekey 候选: {len(keys)} 个")
        return keys

    def discover_sitekey(self) -> str:
        return self.discover_sitekeys()[0]

    # ------------------------------------------------------------- 步骤 1
    def step1_create_signup(self, email: str, captcha_token: str) -> str:
        """建 sign_up，返回 sign_up id。"""
        self.log(f"创建 sign_up: {email}")
        resp = self.ex.post(
            self._url("/v1/client/sign_ups"),
            headers=self._form_headers(),
            data={
                "email_address": email,
                "legal_accepted": "true",
                "captcha_token": captcha_token,
                "captcha_widget_type": self.widget_type,
            },
        )
        payload = self._json(resp)
        signup = self._signup_of(payload)
        signup_id = str(signup.get("id") or "")

        if resp.status_code not in (200, 201) or not signup_id:
            message = self._error_text(payload, resp)
            raise RuntimeError(f"创建 sign_up 失败: {message}")
        return signup_id

    # ------------------------------------------------------------- 步骤 2
    def step2_prepare_email_code(self, signup_id: str) -> None:
        self.log("请求邮箱验证码...")
        resp = self.ex.post(
            self._url(f"/v1/client/sign_ups/{signup_id}/prepare_verification"),
            headers=self._form_headers(),
            data={"strategy": "email_code"},
        )
        payload = self._json(resp)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"发送验证码失败: {self._error_text(payload, resp)}")

    # ------------------------------------------------------------- 步骤 3
    def step3_verify_code(self, signup_id: str, code: str) -> dict:
        """提交验证码，返回 client 对象（含 session）。"""
        self.log("提交验证码...")
        resp = self.ex.post(
            self._url(f"/v1/client/sign_ups/{signup_id}/attempt_verification"),
            headers=self._form_headers(),
            data={"strategy": "email_code", "code": code},
        )
        payload = self._json(resp)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"验证码校验失败: {self._error_text(payload, resp)}")

        signup = self._signup_of(payload)
        status = str(signup.get("status") or "")
        if status and status not in {"complete", "missing_requirements"}:
            raise RuntimeError(f"sign_up 状态异常: {status}")
        return payload

    # ------------------------------------------------------------- 步骤 4
    def step4_identity(self, payload: dict) -> dict:
        """拿 session_id / user_id；响应没带就反查一次 /v1/client。"""
        client = self._client_of(payload)
        sessions = client.get("sessions") or []
        session = sessions[-1] if sessions else {}
        session_id = str(session.get("id") or client.get("created_session_id") or "")
        user_id = str((session.get("user") or {}).get("id") or client.get("created_user_id") or "")

        if not session_id or not user_id:
            resp = self.ex.get(self._url("/v1/client"))
            client = self._client_of(self._json(resp))
            sessions = client.get("sessions") or []
            session_id = session_id or str(client.get("last_active_session_id") or "")
            if not session_id and sessions:
                session_id = str(sessions[-1].get("id") or "")
            for item in sessions:
                if str(item.get("id")) == session_id:
                    user_id = user_id or str((item.get("user") or {}).get("id") or "")
                    break

        if not session_id:
            raise RuntimeError("注册成功但拿不到 session_id，无法继续建 key")
        return {"session_id": session_id, "user_id": user_id}

    # ------------------------------------------------------------- 步骤 5
    def step5_session_jwt(self, session_id: str) -> str:
        resp = self.ex.post(
            self._url(f"/v1/client/sessions/{session_id}/tokens"),
            headers={"content-type": "application/x-www-form-urlencoded"},
            data={},
        )
        payload = self._json(resp)
        jwt = str(payload.get("jwt") or "")
        if not jwt:
            response = payload.get("response")
            if isinstance(response, dict):
                jwt = str(response.get("jwt") or "")
        if not jwt:
            raise RuntimeError(f"签发 __session JWT 失败: {self._error_text(payload, resp)}")
        return jwt

    # ------------------------------------------------------------- 步骤 6
    def step6_create_api_key(self, jwt: str, alias: str = "") -> dict:
        """用 __session + __client 建 API key。"""
        alias = alias or self.key_alias or "cfreg"
        cookies = {}
        try:
            cookies = self.ex.get_cookies() or {}
        except Exception:  # noqa: BLE001
            cookies = {}

        cookie_parts = [f"__session={jwt}"]
        for name in ("__client", "__client_uat"):
            value = cookies.get(name)
            if value:
                cookie_parts.append(f"{name}={value}")

        resp = self.ex.post(
            f"{APP}/api/api-keys",
            headers={
                "content-type": "application/json",
                "accept": "application/json",
                "cookie": "; ".join(cookie_parts),
                "origin": APP,
                "referer": f"{APP}/overview",
            },
            data=json.dumps({"alias": alias, "allowedRegions": ["eu", "us"]}),
        )
        payload = self._json(resp)

        if resp.status_code == 402 or PLAN_REQUIRED_MARKER in (resp.text or ""):
            raise PlanRequiredError(
                payload.get("error")
                or "站点要求先订阅或购买流量包才能创建 API key"
            )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"创建 API key 失败: HTTP {resp.status_code} {self._error_text(payload, resp)}"
            )

        token = str(payload.get("token") or "")
        if not token or "..." in token:
            raise RuntimeError("创建 API key 返回的是掩码串，不是真实 key")
        return payload

    # ------------------------------------------------------------- 错误文案
    @staticmethod
    def _error_text(payload: dict, resp) -> str:
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                return str(first.get("long_message") or first.get("message") or first)
            return str(first)
        for key in ("error", "message", "long_message"):
            value = payload.get(key)
            if value:
                return str(value)
        return (resp.text or "")[:200] or f"HTTP {resp.status_code}"
