"""验证码提取不能把 CSS 颜色当成验证码（Trae 邮件模板踩过）。"""

from core.base_mailbox import extract_verification_code

# Trae 的真实模板形态：真码在 span 文本里，色值 #111314 先出现
TRAE_RAW = """Subject: Trae Email Verification
MIME-Version: 1.0
Content-Type: text/html; charset=utf-8

<html><head><style>.a{color:#111314}</style></head>
<body>
<p style="text-align:center;color:#111314;margin:0 auto">Your verification code</p>
<p style="text-align:center;color:#737780">This code expires in 10 minutes.</p>
<div style="background:#f4f6f8;border:1px solid #d5d8e0;padding:8px 31px;font-size:36px;color:#111314;letter-spacing:18px" >873412</span>
<p style="color:#737780">If you didn't request this, ignore it.</p>
</body></html>
"""


def test_css_hex_is_not_a_code():
    assert extract_verification_code(TRAE_RAW) == "873412"


def test_plain_text_code_still_works():
    raw = "Subject: Verify\n\nYour verification code is 481920\n"
    assert extract_verification_code(raw) == "481920"


def test_subject_code_wins():
    raw = "Subject: 552211 is your code\n\nbody 111111\n"
    assert extract_verification_code(raw) == "552211"
