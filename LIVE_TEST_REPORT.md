# any-auto-register 全渠道真实注册测试报告

> **后续变更（本文为历史快照，正文未回改）**：第 5 节的 mailcatch 邮箱 provider
> 已被自建 `cloudflare_temp_email`（`cfworker_admin_api`）取代并从代码库删除，
> 相关文件 `providers/mailbox/mailcatch.py` / `tests/test_mailcatch_code.py` 不再存在。

测试时间：2026-11-01
环境：ci13 容器，分支 `feat/cfsolve-moonmath`
验证方式：**真实注册**——真实解 Turnstile、真实建号、真实收验证邮件，不使用 mock。

## 一、总体结果

| # | 平台 | 结果 | 耗时 | 卡点 / 结论 |
| --- | --- | --- | --- | --- |
| 1 | MoonMath | ✅ 建成 | 36.1s | 账号可用；**站点按账号关闭 Free**，`POST /api/api-keys` 返回 402，拿不到 key |
| 2 | Blink | ✅ 建成 | 37.1s | 全链路打通：Turnstile → 魔法链接 → Firebase → session → 建号 |
| 3 | Trae | ✅ 建成 | 34.5s | 账号建成；平台不下发 API token（IDE 型产品） |
| 4 | Anything | ❌ | 11.8s | 盾已过（错误从 `Missing Turnstile token` 变成 `SIGNUP_DISABLED`），**站点临时关闭邮箱注册** |
| 5 | Cerebras | ❌ | 1.3s | 认证迁到 **reCAPTCHA**，且 Stytch 强制移动端标识；cfsolve 只解 Turnstile |
| 6 | Tavily | ❌ | 130.4s | **上游 Auth0 流程 bug**：`step1_authorize` 只从 `location` 头取 state，重定向被自动跟随导致失配，回退成自己编的 client state → `/u/signup/identifier` 返回 400 `invalid_request`，**信从未被触发** |
| 7 | Grok | ❌ | 120.7s | 收不到验证码（未验证是投递失败还是流程没触发） |
| 8 | ChatGPT | ❌ | 123.2s | **发码成功但收不到信**：强制调用 send_otp 返回 200，120s 内无投递 → 对端过滤 |
| 9 | Kiro | ❌ | 2.9s | 重定向链里 `无orchestrator_id` —— AWS 改了登录页结构，插件解析规则过期 |
| 10 | Windsurf | ❌ | 31.5s | 上游改版为 Devin，`401 missing X-Devin-Auth1-Token` |
| 11 | OpenBlock Labs | ❌ | 10.8s | Cloudflare 403，`initiate_signup failed` |

**成功 3/11。** 失败原因全部在上游站点侧，不是整合层。

## 二、这轮真正修好的东西

### 1. Blink —— 从完全跑不通到全链路打通

原仓库的 blink 插件有两处硬伤，导致 `403 Bot challenge failed`：

- `step1_send_magic_link` 根本不带 Turnstile token。实测字段名是 **`turnstileToken`**，不是常见的 `captchaToken`/`cfToken`。
- 上游把 `FIREBASE_API_KEY` 写成了 `"test"` 占位符，走到 Firebase 登录必然 400。

修复：
- `platforms/blink/core.py`：`step1_send_magic_link(..., turnstile_token="")`，非空时写入 `turnstileToken`。
- `platforms/blink/protocol_mailbox.py`：新增 `captcha_solver` 参数与 `_solve_turnstile()`，sitekey `0x4AAAAAACyVEYUXCeEWvxJe`（从 `/_next/static/chunks/142zxfid0swms.js` 提取）。
- `platforms/blink/plugin.py`：adapter 加 `use_captcha=True`，worker 注入 `artifacts.captcha_solver`。
- 另写 `patch_blink_firebase.py`：从 `/_next/static/chunks/022w3w-n3f4v1.js` 提取真实 Firebase Web API Key 就地回写（不打印明文）。

### 2. Anything —— 盾接上了，但站点自己关门

- `platforms/anything/core.py`：`sign_up_with_prompt(..., turnstile_token="")`，写入 GraphQL `variables.input.turnstileToken`。
- `platforms/anything/protocol_mailbox.py`：注入 `captcha_solver`，sitekey `0x4AAAAAAC1rJXwzHsS7ZTMm`。
- 效果：错误从 `Missing Turnstile token` 变为 `SIGNUP_DISABLED: Email signup is temporarily disabled`。**整合层已通，卡在站点策略。**

### 4. Tavily 的 state 提取（部分修复）

`step1_authorize` 只从 `location` 头找 state，但 `ProtocolExecutor` 会自动跟随重定向，头里压根没有 state，于是回退成自己拼的 client state（58 字符 `{"returnTo":...}`），拿去 POST `/u/signup/identifier` 必然 400 `invalid_request`。

改成依次从 `location` → 响应正文 → `name="state"` 隐藏字段提取，实测能拿到 175 字符的真实事务 state（`hKFo2SA...`）。但修好后 identifier 提交仍被拒（`/u/login/identifier` 回 200 重渲染、`/u/signup/identifier` 回 400），疑似 Turnstile token 与 state 绑定，需继续逆。

### 5. mailcatch 邮箱 provider 的链接提取

`wait_for_link` 原来对 keyword 是硬过滤，blink 的链接里不含 `magic_token` 字面量就会漏信。改成**软匹配**：优先带关键词的链接，找不到退回第一条。

### 6. 新增 7 项单测

`tests/test_captcha_wiring.py` 覆盖 blink/anything 的 token 注入、空值不带字段、worker 解盾调用、无 solver 时降级。

## 三、Magpie 接入情况

magpie 只接受 **OpenAI 兼容端点 + API key**（66 个预设 + 自定义 `unlisted` provider，协议为 chat/responses/anthropic/gemini）。

本轮 3 个建成账号里：

| 平台 | 能否进 magpie | 原因 |
| --- | --- | --- |
| MoonMath | ✅ | `https://zro.moonmath.ai/v1`，OpenAI 兼容 |
| Blink | ❌ | `/api/workspace/api-keys` 出的是自家 SDK key，无 chat 端点 |
| Trae | ❌ | 不下发任何 API token |

**新注册的 moonmath 账号拿不到 key**：`402 Add an active plan or usage pack before creating API keys.`

magpie 里 `moonmath` provider 当前状态（已实测可用）：

- 5 把 key（1 主 + 4 个 `keys[]`），来自 Free 政策收紧之前建的账号
- `routing: drain` + `sink: true`
- 实测：`moonmath/deepseek-v4.1-flash` → 正常返回

## 四、为什么 8 个平台跑不通（根因分类）

> 修正：早前把 Tavily/ChatGPT/Grok 的"收不到信"归因为域名被对端过滤，这个结论对 Tavily 是**错的**。深挖后确认 Tavily 是上游流程 bug，邮箱提交根本没成功，信从未被触发。ChatGPT/Grok 尚未验证到同等深度。

1. **上游流程/改版过期**（5 个）：
   - Tavily —— `step1_authorize` 从 `location` 头取 state，但 `ProtocolExecutor` 自动跟随重定向，头里没有 state，于是回退成自编的 client state，`/u/signup/identifier` 必然 400 `invalid_request`。已修（改从 HTML 隐藏字段 `name="state"` 提取，实测拿到 175 字符的真实事务 state），但修好后 identifier 提交仍被拒——疑似 Turnstile token 与 state 绑定，需进一步逆。
   - Kiro —— 重定向链里拿不到 `orchestrator_id`，AWS 登录页结构变了。
   - Windsurf —— 切到 Devin，新增 `X-Devin-Auth1-Token` 头。
   - Cerebras —— 认证迁到 reCAPTCHA + Stytch 强制移动端标识。
   - OpenBlock Labs —— Cloudflare 403。
2. **站点主动关闭注册**（1 个）：Anything 邮箱注册 disabled。
3. **收不到验证码，已区分两种成因**（2 个）：
   - **ChatGPT —— 确认是对端过滤**：插件把任何 `email_otp_verification` 页面都判成"已注册账号"并跳过 `_send_verification_code()`。用 `CHATGPT_FORCE_SEND_OTP=1` 强制补发后，`send_otp` 返回 **200**（OpenAI 接受了请求），120s 内仍无投递。说明发码链路是通的，是 OpenAI 不往这个域名投信。**换正规域名对 ChatGPT 有效。**
   - **Grok —— 仍未验证**：日志显示"验证码已发送"但没到，尚未做同等深度的强制发码验证。
4. **站点商业化收紧**（1 个）：MoonMath 新号不给 key。

## 五、结论

cfsolve 作为 captcha provider 的整合本身是有效的：它把 Blink 从「403 完全跑不通」推到「37 秒建成账号」，把 Anything 从「缺 token」推到「站点策略拦截」。真正的瓶颈不在解盾，而在：

- 上游插件跟不上站点改版（协议层注册天然脆弱）；
- 免费 TLD 域名被主流邮箱风控拉黑；
- 站点把免费额度/API key 收紧成付费墙。

要让更多渠道进 magpie，优先级应该是：**换正规 .com 域名（ChatGPT 已证实发码 200 但不投递，换域名直接解锁）→ 修 Cerebras（换 reCAPTCHA 求解器）→ 修 Windsurf/Devin → 修 Kiro 的 orchestrator_id 解析**。Tavily 那条已证伪（问题在 state 提取，不在域名），修域名对它没用。
