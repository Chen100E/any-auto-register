"""把平台里写死的站点常量改成「环境变量可覆盖，原值作默认」。

只做行级包裹，不打印、不重写字面值，避免占位符/密钥被手抄出错。
幂等：已包裹过则跳过。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path("/app/working/auto_registers/any-auto-register")

# (文件, 常量名, 环境变量名)
TARGETS = [
    ("platforms/blink/core.py", "FIREBASE_API_KEY", "BLINK_FIREBASE_API_KEY"),
    ("platforms/tavily/core.py", "AUTH0_CLIENT_ID", "TAVILY_AUTH0_CLIENT_ID"),
    ("platforms/tavily/core.py", "AUTH0_BASE", "TAVILY_AUTH0_BASE"),
    ("platforms/tavily/core.py", "APP_BASE", "TAVILY_APP_BASE"),
    ("platforms/tavily/core.py", "REDIRECT_URI", "TAVILY_REDIRECT_URI"),
    ("platforms/tavily/core.py", "TURNSTILE_SITEKEY", "TAVILY_TURNSTILE_SITEKEY"),
    ("platforms/moonmath/core.py", "CLERK", "MOONMATH_CLERK_BASE"),
    ("platforms/moonmath/core.py", "ACCOUNTS", "MOONMATH_ACCOUNTS_BASE"),
    ("platforms/moonmath/core.py", "APP", "MOONMATH_APP_BASE"),
    ("platforms/moonmath/core.py", "CLERK_JS_VERSION", "MOONMATH_CLERK_JS_VERSION"),
    ("providers/captcha/cfsolve.py", "DEFAULT_BINARY", "CFSOLVE_BIN"),
    ("providers/captcha/cfsolve.py", "DEFAULT_FINGERPRINT", "CFSOLVE_FP"),
]


def ensure_import_os(text: str) -> str:
    if re.search(r"^import os$", text, re.M):
        return text
    if re.search(r"^from __future__ import annotations$", text, re.M):
        return text.replace(
            "from __future__ import annotations",
            "from __future__ import annotations\n\nimport os",
            1,
        )
    return "import os\n" + text


changed = 0
for rel, const, env in TARGETS:
    path = ROOT / rel
    if not path.exists():
        print(f"跳过（不存在）: {rel}")
        continue
    text = path.read_text(encoding="utf-8")
    if f'os.environ.get("{env}"' in text:
        print(f"已配置: {const}")
        continue
    pattern = re.compile(rf'^({const}\s*=\s*)(".*?")\s*$', re.M)
    match = pattern.search(text)
    if not match:
        print(f"⚠️ 未找到常量行: {const} in {rel}")
        continue
    replacement = (
        f'{match.group(1)}os.environ.get("{env}", "").strip() or {match.group(2)}'
    )
    text = text[: match.start()] + replacement + text[match.end():]
    text = ensure_import_os(text)
    path.write_text(text, encoding="utf-8")
    changed += 1
    print(f"✅ {const} -> 可用 {env} 覆盖")

print(f"\n共修改 {changed} 处")
