# cfsolve 接入 + MoonMath 平台

本分支把 [cfsolve](https://github.com/) 作为**协议层验证码 provider** 接入，
并用它跑通 MoonMath（`zro.moonmath.ai`）的注册全链路。全程不需要浏览器。

## 为什么是 cfsolve

现有的 captcha provider 分两类：

| provider | 方式 | 代价 |
|---|---|---|
| `local_solver` | 本地起 Camoufox/patchright 服务点 widget | 吃 CPU，要装浏览器，无头易被识别 |
| `yescaptcha_api` / `twocaptcha_api` | 云端打码 | 按次付费，要外网 |
| `manual` | 人工输入 | 只能调试 |
| **`cfsolve`** | **协议层回答挑战程序** | **零浏览器、零费用、不占 CPU** |

cfsolve 用一个固定的设备指纹（默认 Safari-iOS）在协议层完成 Turnstile 的
probe 与交互 POST，直接产出 widget token 或整页盾的 `cf_clearance`。

它唯一的软肋是依赖 Cloudflare 当前下发的 `api.js` 构建：某些构建下挑战程序
的 key transform 解不出来（报 `does not build the key transform`）。这种失败
是**构建相关**的，换一次构建通常就过，所以 provider 内置重试（默认 4 次）。

## 新增文件

```
providers/captcha/cfsolve.py          cfsolve provider
platforms/moonmath/plugin.py          平台插件（注册表入口）
platforms/moonmath/core.py            Clerk 协议实现
platforms/moonmath/protocol_mailbox.py 注册 worker
tests/test_cfsolve_captcha.py         16 项
tests/test_moonmath_platform.py       32 项
```

改动两处：

- `core/base_captcha.py` —— 注册懒加载 + 工厂分支
- `infrastructure/provider_definitions_repository.py` —— 加 provider 种子条目

## cfsolve provider

```python
@register_provider("captcha", "cfsolve")
class CfsolveCaptcha(BaseCaptcha):
    def solve_turnstile(self, page_url, site_key) -> str:  # widget token
    def solve_page(self, page_url) -> str:                # cf_clearance
```

配置项（设置页可见）：

| 键 | 默认 | 说明 |
|---|---|---|
| `cfsolve_bin` | `/app/working/cfsolve/run_challenge.sh` | 可执行文件 |
| `cfsolve_fingerprint` | `.../profiles/safari-26-ios.json` | 设备指纹 |
| `cfsolve_timeout` | `240` | 单次超时（秒） |
| `cfsolve_retries` | `4` | 重试次数 |
| `cfsolve_blank_page` | 开 | 在空白页渲染 widget，绕开目标页自带的整页盾 |
| `cfsolve_proxy` | 空 | 可选代理 |

## MoonMath 平台

站点是 Next.js + Clerk，注册为**单因子邮箱验证码、无密码**：

```
POST {CLERK}/v1/client/sign_ups                              建 sign_up（带 Turnstile token）
POST {CLERK}/v1/client/sign_ups/{id}/prepare_verification    strategy=email_code
POST {CLERK}/v1/client/sign_ups/{id}/attempt_verification    strategy=email_code + code
GET  {CLERK}/v1/client                                       拿 session_id / user_id
POST {CLERK}/v1/client/sessions/{sid}/tokens                 签 __session JWT
POST {APP}/api/api-keys                                      建 API key
```

平台声明：

```python
supported_executors = ["protocol"]          # 纯协议，无浏览器
supported_identity_modes = ["mailbox"]
protocol_captcha_order = ("cfsolve", "yescaptcha_api", "twocaptcha_api", "manual")
capabilities = ["create_api_key", "query_state"]
```

`protocol_captcha_order` 把 `cfsolve` 放在首位：本地协议层优先于云端打码，
省掉每号一次的打码费用。

### sitekey 从哪来

注册页是 SPA，HTML 里没有 sitekey。权威来源是 Clerk 的
`GET /v1/environment`，它同时给出 `captcha_public_key` 和
`captcha_public_key_invisible` 两把（签名策略不同）。worker 会**依次尝试**，
哪个被 sign_up 接受就用哪个。

### 402 不是 bug

新注册的账号在调 `POST /api/api-keys` 时可能直接拿到：

```
HTTP 402 {"error":"Add an active plan or usage pack before creating API keys."}
```

控制台对应文案是 `Free isn't available for this account. Pick a plan to continue.`
定价页仍然挂着 Free 档，所以这是**按账号判定**的（域名 / 出口 IP 风控），
不是工具缺陷。旧账号实测 `Free不可用=False`，key 照常工作。

因此账号记录里保留两个字段：

- `plan_required` —— 账号建成了，但站点不给 key；重试无意义
- `key_error` —— 其他建 key 失败原因

账号本身仍然可用（session 有效），站点放开后可以直接用
`create_api_key` capability 补签。

## 测试

```bash
# 单元测试（48 项，全 mock）
python3 -m pytest tests/test_cfsolve_captcha.py tests/test_moonmath_platform.py -q

# 全量（新增 48 项，其余与上游基线一致）
python3 -m pytest -q
```

真实链路实测（ci13，2026-11）：

```
目标邮箱: <random>@ping1.pp.ua
captcha provider: CfsolveCaptcha
Turnstile sitekey 候选: 2 个
解 Turnstile（sitekey 1/2）...
Turnstile token: 0.xxxx…（794 字符）
创建 sign_up: ...
请求邮箱验证码...
收到验证码: 369138
提交验证码...
session=sess_3LUvbgApwoD5jVDxLmEqRsMznwv user=user_3LUvbglK0XJePPIps3hdqX476DX
账号已建成，但站点不给建 key: Add an active plan or usage pack before creating API keys.
耗时: 71.5s
```

## 用起来

1. 设置页 → 验证码 provider → 启用 **cfsolve（本地协议层）**，填可执行文件与指纹路径
2. 平台页 → 选 **MoonMath** → 执行器选 `protocol` → 配一个 mailbox provider
3. 建注册任务

`cfsolve` 无需任何 key，所以启用即用；填好路径后可以直接在
provider 的测试按钮里验证。

## 可配置项

站点改版时不用改源码。解析优先级统一为：

**`extra` 显式配置 → 环境变量 → 源码默认值**

`extra` 指注册任务里的 `extra` 字典（与 `resolve_timeout` 同一套约定），
新增的通用解析函数在 `core/registration/helpers.py`：

```python
from core.registration.helpers import resolve_setting, resolve_flag

sitekey = resolve_setting(extra, ("blink_sitekey",), DEFAULT, env=("BLINK_TURNSTILE_SITEKEY",))
enabled = resolve_flag(extra, ("blink_use_captcha",), True)
```

### 平台侧（`extra` 或环境变量）

| 变量 | 配置键 | 环境变量 | 说明 |
| --- | --- | --- | --- |
| blink Turnstile sitekey | `blink_sitekey` / `blink_turnstile_sitekey` | `BLINK_TURNSTILE_SITEKEY` | 魔法链接接口的盾 |
| blink 注册页 | `blink_signup_page` | `BLINK_SIGNUP_PAGE` | 解盾时加载的页面 |
| anything Turnstile sitekey | `anything_sitekey` / `anything_turnstile_sitekey` | `ANYTHING_TURNSTILE_SITEKEY` | 注册 GraphQL 的盾 |

### 模块常量（环境变量覆盖，原值作默认）

| 变量 | 环境变量 | 文件 |
| --- | --- | --- |
| Firebase Web API Key | `BLINK_FIREBASE_API_KEY` | `platforms/blink/core.py` |
| Auth0 client id | `TAVILY_AUTH0_CLIENT_ID` | `platforms/tavily/core.py` |
| Auth0 域 | `TAVILY_AUTH0_BASE` | `platforms/tavily/core.py` |
| Tavily 应用域 | `TAVILY_APP_BASE` | `platforms/tavily/core.py` |
| 回调地址 | `TAVILY_REDIRECT_URI` | `platforms/tavily/core.py` |
| Turnstile sitekey | `TAVILY_TURNSTILE_SITEKEY` | `platforms/tavily/core.py` |
| Clerk FAPI 域 | `MOONMATH_CLERK_BASE` | `platforms/moonmath/core.py` |
| Clerk 前端域 | `MOONMATH_ACCOUNTS_BASE` | `platforms/moonmath/core.py` |
| 业务域 | `MOONMATH_APP_BASE` | `platforms/moonmath/core.py` |
| Clerk JS 版本 | `MOONMATH_CLERK_JS_VERSION` | `platforms/moonmath/core.py` |
| cfsolve 可执行文件 | `CFSOLVE_BIN` | `providers/captcha/cfsolve.py` |
| cfsolve 指纹 | `CFSOLVE_FP` | `providers/captcha/cfsolve.py` |

cfsolve 的其余参数（`timeout` / `retries` / `api_url` / `domain` /
`prefix` / `poll_interval`）走 provider 设置页，落在
`ProviderSettingsRepository`，与上面两套等价。

批量驱动脚本 `run_all.py` 认 `DEFAULT_PW` / `OTP_TIMEOUT` / `RESULT_FILE`。

### 测试

`tests/test_configurable_constants.py`（23 项）：解析优先级、空值忽略、
布尔解析、三个平台的 worker sitekey 注入、以及用子进程验证环境变量确实能
覆盖模块常量（用 reload 会污染同进程内其他测试对模块级常量的引用，所以走子进程）。
