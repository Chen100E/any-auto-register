"""修 CFWorkerMailbox 的验证码提取：优先 Subject，剥离 MIME boundary/base64 噪声。

原实现取正文里第一个 6 位数字，会被 multipart boundary（如
`boundary=435752e02cbe...`）命中的 435752 骗过，导致「验证码错误」。
"""
from pathlib import Path

PATH = Path("/app/working/auto_registers/any-auto-register/core/base_mailbox.py")
text = PATH.read_text(encoding="utf-8")

HELPER = '''_CODE_SIX_RE = re.compile(r"(?<!\\d)(\\d{6})(?!\\d)")
_CODE_BOUNDARY_RE = re.compile(r"^--[\\w=.-]+.*$", re.M)
_CODE_HEADER_RE = re.compile(r"^[A-Za-z-]+:.*$", re.M)
_CODE_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=]{40,}$", re.M)
_CODE_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}")
_CODE_LONGNUM_RE = re.compile(r"\\d{9,}")
_CODE_KEYWORD_RE = re.compile(
    r"(?:code|verification|verify|otp|passcode|验证码|校验码|确认码)[^0-9]{0,30}(\\d{6})",
    re.I,
)


def extract_verification_code(raw: str, code_pattern: str | None = None) -> str:
    """从原始邮件里提验证码。

    顺序：Subject 的 6 位码 -> 剥离 MIME 噪声后按关键词邻近 -> 兜底第一个独立 6 位码。
    """
    import re as _re

    subject_match = _re.search(r"^Subject:\\s*(.*)$", raw, _re.M)
    if subject_match:
        hit = _CODE_SIX_RE.search(subject_match.group(1))
        if hit:
            return hit.group(1)

    body = raw
    split = raw.find("\\r\\n\\r\\n")
    if split != -1:
        body = raw[split:]
    body = _CODE_BOUNDARY_RE.sub(" ", body)
    body = _CODE_HEADER_RE.sub(" ", body)
    body = _CODE_BASE64_RE.sub(" ", body)
    body = _CODE_EMAIL_RE.sub(" ", body)
    body = _CODE_LONGNUM_RE.sub(" ", body)
    body = _re.sub(r"m=\\+\\d+\\.\\d+", " ", body)
    body = _re.sub(r"\\bt=\\d+\\b", " ", body)

    if code_pattern:
        hit = _re.search(code_pattern, body)
        if hit:
            return hit.group(1) if hit.groups() else hit.group(0)

    hit = _CODE_KEYWORD_RE.search(body)
    if hit:
        return hit.group(1)

    hit = _CODE_SIX_RE.search(body)
    return hit.group(1) if hit else ""


class CFWorkerMailbox(BaseMailbox):'''

anchor = "class CFWorkerMailbox(BaseMailbox):"
assert anchor in text, "类锚点未找到"
assert "def extract_verification_code" not in text, "helper 已存在"
text = text.replace(anchor, HELPER, 1)

OLD = '''                    raw = str(mail.get("raw", ""))
                    # 1. 优先匹配 <span>XXXXXX</span> （Trae 邮件格式）
                    code_m = re.search(r'<span[^>]*>\\s*(\\d{6})\\s*</span>', raw)
                    if code_m:
                        return code_m.group(1)
                    # 2. 跳过 MIME header，只搜 body 部分，避免匹配时间戳
                    body_start = raw.find('\\r\\n\\r\\n')
                    search_text = raw[body_start:] if body_start != -1 else raw
                    search_text = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}', '', search_text)
                    # 排除时间戳模式 m=+XXXXXX. 和 t=XXXXXXXXXX
                    search_text = re.sub(r'm=\\+\\d+\\.\\d+', '', search_text)
                    search_text = re.sub(r'\\bt=\\d+\\b', '', search_text)
                    m = re.search(code_pattern or r'(?<!#)(?<!\\d)(\\d{6})(?!\\d)', search_text)
                    if m:
                        return m.group(1) if m.groups() else m.group(0)'''

NEW = '''                    raw = str(mail.get("raw", ""))
                    # 优先 <span>XXXXXX</span>（Trae 邮件格式）
                    code_m = re.search(r'<span[^>]*>\\s*(\\d{6})\\s*</span>', raw)
                    if code_m:
                        return code_m.group(1)
                    code = extract_verification_code(raw, code_pattern)
                    if code:
                        return code'''

assert OLD in text, "wait_for_code 提取块锚点未找到"
text = text.replace(OLD, NEW, 1)

PATH.write_text(text, encoding="utf-8")
print("✅ CFWorkerMailbox 验证码提取已修")
