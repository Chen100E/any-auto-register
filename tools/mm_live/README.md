# mm_live 运维脚本

配合 `providers/captcha/cfsolve.py` 与 `cfworker_admin_api` 邮箱的批量注册/导出工具。
这些脚本原先散落在容器 `/app/working/mm_live/tools/`，现纳入版本控制。

| 脚本 | 用途 |
| --- | --- |
| `run_all.py` | 批量注册入口。按 `MAIL_PROVIDER` / `MAIL_DOMAIN` / `CAPTCHA_SOLVER` / `DEFAULT_PW` / `OTP_TIMEOUT` 环境变量驱动；成功后自动导出账号 |
| `export_all_accounts.py` | 合并 DB 账号与 `results.jsonl` 凭据，导出 json/csv/env/md 四份 |
| `setup_cfworker.py` | 把 `cfworker_admin_api` 设为默认邮箱 provider |
| `update_tempmail_endpoint.py` | 切换自建邮箱服务端点 |
| `patch_cfworker_proxy.py` | `CFWorkerMailbox` 关闭环境代理继承（补丁脚本，已落地为代码改动） |
| `patch_code_extract.py` | 新增 `extract_verification_code()`（补丁脚本，已落地为代码改动） |
| `make_constants_configurable.py` | 把平台常量包裹为 `os.environ.get(...) or <原值>`（补丁脚本） |

## 用法

```bash
cd /app/working/auto_registers/any-auto-register
DEFAULT_PW=<密码> OTP_TIMEOUT=150 PYTHONPATH=.:$PYTHONPATH \
  python3 tools/mm_live/run_all.py moonmath blink
```

密码只从环境变量读，不要写进仓库或日志。
