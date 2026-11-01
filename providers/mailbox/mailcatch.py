"""MailCatch 邮箱 — Cloudflare Email Routing catch-all + Worker + KV。

自建收信栈：域名开 catch-all 把邮件交给 Worker，Worker 解析出验证码写 KV，
这里通过 Worker 的 HTTP 接口读。任何 `<随机>@<域名>` 都能收信，不需要预先建箱。

接口（Worker 侧）：
  GET /health
  GET /mail?to=<addr>&limit=N  -> {to, count, mails:[{ts,subject,from,body,otp}]}
  GET /otp?to=<addr>           -> {to, otp, subject, from, ts}
"""
from __future__ import annotations

import random
import re
import string
import time

from core.base_mailbox import BaseMailbox, MailboxAccount
from providers.registry import register_provider

#: 自建服务在本地/容器内直连更稳，不走外网代理
_NO_PROXY = {"http": None, "https": None}

_LINK_RE = re.compile(r"https?://[^\s<>\"')]+")


@register_provider("mailbox", "mailcatch")
class MailCatchMailbox(BaseMailbox):
    """Cloudflare Worker 自建 catch-all 收信。"""

    def __init__(
        self,
        api_url: str = "",
        domain: str = "",
        prefix: str = "mc",
        timeout: int = 150,
        poll_interval: float = 3.0,
        proxy: str = "",
    ):
        self.api = (api_url or "").rstrip("/")
        self.domain = (domain or "").strip()
        self.prefix = prefix or "mc"
        self.timeout = max(30, int(timeout))
        self.poll_interval = max(1.0, float(poll_interval))
        self.proxies = {"http": proxy, "https": proxy} if proxy else _NO_PROXY

    @classmethod
    def from_config(cls, config: dict) -> "MailCatchMailbox":
        config = config or {}

        def _str(key: str, default: str = "") -> str:
            value = config.get(key)
            return str(value).strip() if value not in (None, "") else default

        try:
            timeout = int(str(config.get("mailcatch_timeout") or 150))
        except (TypeError, ValueError):
            timeout = 150
        try:
            interval = float(str(config.get("mailcatch_poll_interval") or 3))
        except (TypeError, ValueError):
            interval = 3.0

        return cls(
            api_url=_str("mailcatch_url"),
            domain=_str("mailcatch_domain"),
            prefix=_str("mailcatch_prefix", "mc"),
            timeout=timeout,
            poll_interval=interval,
            proxy=_str("mailcatch_proxy"),
        )

    # ------------------------------------------------------------------ 建箱
    def get_email(self) -> MailboxAccount:
        if not self.api or not self.domain:
            raise RuntimeError("MailCatch 未配置 mailcatch_url / mailcatch_domain")
        tail = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        email = f"{self.prefix}{tail}@{self.domain}"
        return MailboxAccount(
            email=email,
            account_id=email,
            extra={
                "provider_resource": {
                    "provider_type": "mailbox",
                    "provider_name": "mailcatch",
                    "resource_type": "mailbox",
                    "resource_identifier": email,
                    "handle": email,
                    "display_name": email,
                    "metadata": {"email": email, "api_url": self.api, "domain": self.domain},
                },
            },
        )

    # ------------------------------------------------------------------ 读信
    def _mails(self, email: str, limit: int = 20) -> list[dict]:
        import requests

        resp = requests.get(
            f"{self.api}/mail",
            params={"to": email, "limit": limit},
            timeout=15,
            proxies=self.proxies,
        )
        data = resp.json() if resp.content else {}
        mails = data.get("mails") if isinstance(data, dict) else data
        return list(mails or [])

    def get_current_ids(self, account: MailboxAccount) -> set:
        try:
            return {str(m.get("ts", "")) for m in self._mails(account.email)}
        except Exception:  # noqa: BLE001
            return set()

    def wait_for_code(self, account: MailboxAccount, keyword: str = "",
                      timeout: int = 120, before_ids: set = None,
                      code_pattern: str = None) -> str:
        deadline = time.time() + (timeout or self.timeout)
        seen = {str(x) for x in (before_ids or set())}
        baseline_done = bool(seen)
        while time.time() < deadline:
            try:
                mails = self._mails(account.email)
                if not baseline_done:
                    # 首次轮询：把已有邮件当作旧信，只等新到的
                    seen = {str(m.get("ts", "")) for m in mails}
                    baseline_done = True
                else:
                    for mail in sorted(mails, key=lambda m: m.get("ts", 0), reverse=True):
                        mid = str(mail.get("ts", ""))
                        if not mid or mid in seen:
                            continue
                        seen.add(mid)
                        code = self._extract_code(mail, keyword, code_pattern)
                        if code:
                            return code
            except Exception:  # noqa: BLE001
                pass
            time.sleep(self.poll_interval)
        raise TimeoutError(f"MailCatch 等待验证码超时 ({timeout or self.timeout}s)")

    @staticmethod
    def _extract_code(mail: dict, keyword: str = "", code_pattern: str = None) -> str:
        subject = str(mail.get("subject") or "")
        body = str(mail.get("body") or "")
        hay = f"{subject} {body}"
        if keyword and keyword.lower() not in hay.lower():
            return ""
        if code_pattern:
            match = re.search(code_pattern, hay)
            if match:
                return match.group(1) if match.groups() else match.group(0)
            return ""
        # Worker 抽出来的 otp 不一定是验证码（邮件里的年份、订单号都可能被抓到），
        # 所以只把它当兜底：先按 6 位验证码在主题+正文里找，找不到再用它。
        otp = str(mail.get("otp") or "").strip()
        if otp.isdigit() and len(otp) == 6:
            return otp
        match = re.search(r"(?<!\d)(\d{6})(?!\d)", hay)
        if match:
            return match.group(1)
        if otp.isdigit() and 4 <= len(otp) <= 8:
            return otp
        return ""

    def wait_for_link(self, account: MailboxAccount, keyword: str = "",
                      timeout: int = 120, before_ids: set = None) -> str:
        deadline = time.time() + (timeout or self.timeout)
        seen = {str(x) for x in (before_ids or set())}
        baseline_done = bool(seen)
        while time.time() < deadline:
            try:
                mails = self._mails(account.email)
                if not baseline_done:
                    seen = {str(m.get("ts", "")) for m in mails}
                    baseline_done = True
                else:
                    fallback = ""
                    for mail in sorted(mails, key=lambda m: m.get("ts", 0), reverse=True):
                        mid = str(mail.get("ts", ""))
                        if not mid or mid in seen:
                            continue
                        seen.add(mid)
                        hay = f"{mail.get('subject', '')} {mail.get('body', '')}"
                        for link in _LINK_RE.findall(hay):
                            link = link.rstrip(".,);")
                            if keyword and keyword.lower() in link.lower():
                                return link
                            fallback = fallback or link
                    # 关键词只是偏好：链接里没有关键词时退回第一条，避免漏信
                    if fallback:
                        return fallback
            except Exception:  # noqa: BLE001
                pass
            time.sleep(self.poll_interval)
        raise TimeoutError(f"MailCatch 等待验证链接超时 ({timeout or self.timeout}s)")
