"""CFWorkerMailbox 关闭 requests 的环境代理继承。

sandbox 里 HTTPS_PROXY 指向一个只支持 CONNECT 的出口，走代理会 426 Upgrade Required；
临时邮箱请求应当直连（或走显式传入的 proxy）。
"""
from pathlib import Path

PATH = Path("/app/working/auto_registers/any-auto-register/core/base_mailbox.py")
text = PATH.read_text(encoding="utf-8")

OLD_INIT = '''        self.proxy = {"http": proxy, "https": proxy} if proxy else None
        self._token = None

    def _headers(self) -> dict:'''
NEW_INIT = '''        self.proxy = {"http": proxy, "https": proxy} if proxy else None
        self._token = None
        self._session = self._new_session()

    def _new_session(self):
        import requests
        session = requests.Session()
        # 不继承 HTTP(S)_PROXY 环境变量：sandbox 的出口代理只支持 CONNECT，会 426
        session.trust_env = False
        if self.proxy:
            session.proxies.update(self.proxy)
        return session

    def _headers(self) -> dict:'''

assert OLD_INIT in text, "init 锚点未找到"
text = text.replace(OLD_INIT, NEW_INIT, 1)

OLD_POST = '''        import requests, random, string
        name = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        payload = {"enablePrefix": True, "name": name}
        if self.domain:
            payload["domain"] = self.domain
        r = requests.post(f"{self.api}/admin/new_address",
            json=payload, headers=self._headers(),
            proxies=self.proxy, timeout=15)'''
NEW_POST = '''        import random, string
        name = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        payload = {"enablePrefix": True, "name": name}
        if self.domain:
            payload["domain"] = self.domain
        r = self._session.post(f"{self.api}/admin/new_address",
            json=payload, headers=self._headers(), timeout=15)'''
assert OLD_POST in text, "get_email 锚点未找到"
text = text.replace(OLD_POST, NEW_POST, 1)

OLD_GET = '''        import requests
        r = requests.get(f"{self.api}/admin/mails",
            params={"limit": 20, "offset": 0, "address": email},
            headers=self._headers(), proxies=self.proxy, timeout=10)'''
NEW_GET = '''        r = self._session.get(f"{self.api}/admin/mails",
            params={"limit": 20, "offset": 0, "address": email},
            headers=self._headers(), timeout=10)'''
assert OLD_GET in text, "_get_mails 锚点未找到"
text = text.replace(OLD_GET, NEW_GET, 1)

PATH.write_text(text, encoding="utf-8")
print("CFWorkerMailbox 已改用 trust_env=False 的 session")
