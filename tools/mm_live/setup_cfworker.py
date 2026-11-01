"""把默认邮箱 provider 从 mailcatch 切到 cloudflare_temp_email（cfworker_admin_api）。"""
import json

from core.db import init_db

init_db()

from infrastructure.provider_settings_repository import ProviderSettingsRepository

API_URL = "https://temp-email.chenbaiyi-work.workers.dev"
DOMAIN = "ping1.pp.ua"
ADMIN_TOKEN = open("/root/.tempmail_admin").read().strip()

settings = ProviderSettingsRepository()
print("切换前默认 mailbox:", settings.get_default_provider_key("mailbox"))

# 1) 取消 mailcatch 的默认标记
for row in settings.list_by_type("mailbox"):
    if row.provider_key == "mailcatch" and row.is_default:
        settings.save(
            setting_id=row.id,
            provider_type="mailbox",
            provider_key="mailcatch",
            display_name=row.display_name,
            auth_mode=row.auth_mode or "",
            enabled=bool(row.enabled),
            is_default=False,
            config=json.loads(row.config_json or "{}"),
            auth=json.loads(row.auth_json or "{}"),
            metadata=json.loads(row.metadata_json or "{}"),
        )
        print("mailcatch 默认标记已取消")

# 2) 保存 cfworker_admin_api 并设为默认
existing = settings.get_by_key("mailbox", "cfworker_admin_api")
row = settings.save(
    setting_id=existing.id if existing else None,
    provider_type="mailbox",
    provider_key="cfworker_admin_api",
    display_name="Cloudflare Temp Email",
    auth_mode="",
    enabled=True,
    is_default=True,
    config={
        "cfworker_api_url": API_URL,
        "cfworker_admin_token": ADMIN_TOKEN,
        "cfworker_domain": DOMAIN,
        "cfworker_fingerprint": "",
    },
    auth={},
    metadata={},
)
print(f"cfworker_admin_api 已保存 id={row.id}")

print("\n切换后默认 mailbox:", settings.get_default_provider_key("mailbox"))

# 3) 实建一个邮箱验证
from core.base_mailbox import create_mailbox

box = create_mailbox("cfworker_admin_api", {}, None)
acct = box.get_email()
print("建箱结果:", acct.email, "| token 前缀:", (acct.account_id or "")[:12])

ids = box.get_current_ids(acct)
print("当前邮件数:", len(ids))
