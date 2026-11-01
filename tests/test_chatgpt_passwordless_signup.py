"""OpenAI 注册页面分类：passwordless 新注册不能被当成"已注册账号"。"""

from platforms.chatgpt.register import classify_signup_page


def _resp(page_type, payload=None, session=None):
    return {
        "page": {"type": page_type, "payload": payload or {}},
        "oai-client-auth-session": session or {},
    }


def test_plain_password_signup():
    """老流程：直接要求设密码。"""
    v = classify_signup_page(_resp("create_account_password"))
    assert v["page_type"] == "create_account_password"
    assert v["is_existing_account"] is False
    assert v["passwordless_signup"] is False


def test_passwordless_signup_is_not_existing_account():
    """本次 bug：email_otp_verification + passwordless_signup 是新注册。"""
    v = classify_signup_page(_resp(
        "email_otp_verification",
        {"email_verification_mode": "passwordless_signup"},
        {"passwordless_signup_from_default_redirect": True},
    ))
    assert v["verify_mode"] == "passwordless_signup"
    assert v["passwordless_signup"] is True
    assert v["is_existing_account"] is False


def test_passwordless_detected_from_session_only():
    """payload 没带 mode 时，靠 session 里的 redirect 标记兜底。"""
    v = classify_signup_page(_resp(
        "email_otp_verification", {}, {"passwordless_signup_from_default_redirect": True}))
    assert v["passwordless_signup"] is True
    assert v["is_existing_account"] is False


def test_real_existing_account_still_detected():
    """真正的老账号登录（非 passwordless）仍要识别成已注册。"""
    v = classify_signup_page(_resp(
        "email_otp_verification", {"email_verification_mode": "email_otp"},
        {"email_verification_mode": "email_otp"}))
    assert v["passwordless_signup"] is False
    assert v["is_existing_account"] is True


def test_empty_payload_is_safe():
    v = classify_signup_page({})
    assert v["page_type"] == ""
    assert v["is_existing_account"] is False
    assert v["passwordless_signup"] is False
