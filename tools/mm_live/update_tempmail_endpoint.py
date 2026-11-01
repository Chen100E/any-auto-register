"""更新 cfworker_admin_api 的 API 地址（换自定义域名用）。地址从文件读。"""
import json

from core.db import init_db

init_db()

from infrastructure.provider_settings_repository import ProviderSettingsRepository

API_URL = open("/tmp/.tmu").read().strip()
settings = ProviderSettingsRepository()
row = settings.get_by_key("mailbox", "cfworker_admin_api")
assert row, "cfworker_admin_api 设置不存在"

config = json.loads(row.config_json or "{}")
old = config.get("cfworker_api_url")
config["cfworker_api_url"] = API_URL
settings.save(
    setting_id=row.id,
    provider_type="mailbox",
    provider_key="cfworker_admin_api",
    display_name=row.display_name,
    auth_mode=row.auth_mode or "",
    enabled=bool(row.enabled),
    is_default=bool(row.is_default),
    config=config,
    auth=json.loads(row.auth_json or "{}"),
    metadata=json.loads(row.metadata_json or "{}"),
)
print(f"API 地址: {old} -> {API_URL}")

from core.base_mailbox import create_mailbox

box = create_mailbox("cfworker_admin_api")
acct = box.get_email()
print("建箱验证:", acct.email)
