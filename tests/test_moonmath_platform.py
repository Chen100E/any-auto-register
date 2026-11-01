"""MoonMath 平台插件测试。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from platforms.moonmath.core import MoonMathRegister, PlanRequiredError


def _resp(status=200, payload=None, text=""):
    return SimpleNamespace(
        status_code=status,
        json=lambda: payload if payload is not None else {},
        text=text or "",
    )


class TestSignupParsing:
    def test_flat_shape(self):
        payload = {"response": {"id": "signup_1", "object": "sign_up"}, "client": {}}
        assert MoonMathRegister._signup_of(payload)["id"] == "signup_1"

    def test_nested_sign_up_key(self):
        assert MoonMathRegister._signup_of({"sign_up": {"id": "s2"}})["id"] == "s2"

    def test_bare_object(self):
        assert MoonMathRegister._signup_of({"object": "sign_up", "id": "s3"})["id"] == "s3"

    def test_missing_returns_empty(self):
        assert MoonMathRegister._signup_of({"nope": 1}) == {}

    def test_client_flat(self):
        assert MoonMathRegister._client_of({"client": {"object": "client"}})["object"] == "client"

    def test_client_bare(self):
        assert MoonMathRegister._client_of({"object": "client"})["object"] == "client"

    def test_client_nested(self):
        payload = {"response": {"object": "client", "sessions": []}}
        assert MoonMathRegister._client_of(payload)["sessions"] == []


class TestSitekey:
    def test_discover_from_page(self):
        ex = mock.Mock()
        ex.get.return_value = _resp(text='<div data-sitekey="0x4AAAAAAABkMYinukE8nzY"></div>')
        client = MoonMathRegister(executor=ex, log_fn=lambda m: None)
        assert client.discover_sitekey() == "0x4AAAAAAABkMYinukE8nzY"

    def test_discover_from_environment_prefers_public_key(self):
        ex = mock.Mock()
        ex.get.return_value = _resp(payload={"display_config": {
            "captcha_public_key": "0x4AAAAAAAVISIBLE",
            "captcha_public_key_invisible": "0x4AAAAAAAINVIS",
            "captcha_widget_type": "smart",
        }})
        client = MoonMathRegister(executor=ex, log_fn=lambda m: None)
        keys = client.discover_sitekeys()
        assert keys == ["0x4AAAAAAAVISIBLE", "0x4AAAAAAAINVIS"]
        assert client.widget_type == "smart"

    def test_environment_hits_environment_endpoint(self):
        ex = mock.Mock()
        ex.get.return_value = _resp(payload={"display_config": {"captcha_public_key": "0x4AAAAAAAX"}})
        MoonMathRegister(executor=ex, log_fn=lambda m: None).discover_sitekeys()
        assert "/v1/environment" in ex.get.call_args[0][0]

    def test_configured_sitekey_wins(self):
        ex = mock.Mock()
        client = MoonMathRegister(executor=ex, log_fn=lambda m: None, sitekey="0x4AAAAAAAZZZ")
        assert client.discover_sitekey() == "0x4AAAAAAAZZZ"
        ex.get.assert_not_called()

    def test_discovery_failure_raises(self):
        ex = mock.Mock()
        ex.get.return_value = _resp(text="<html>no widget</html>")
        client = MoonMathRegister(executor=ex, log_fn=lambda m: None)
        with pytest.raises(RuntimeError, match="sitekey"):
            client.discover_sitekey()


class TestFlow:
    def _client(self):
        return MoonMathRegister(executor=mock.Mock(), log_fn=lambda m: None)

    def test_create_signup_ok(self):
        client = self._client()
        client.ex.post.return_value = _resp(payload={"response": {"id": "signup_9"}})
        assert client.step1_create_signup("a@example.com", "TOK") == "signup_9"

    def test_create_signup_blocked_reports_message(self):
        client = self._client()
        client.ex.post.return_value = _resp(
            status=403,
            payload={"errors": [{"long_message": "Email address blocked"}]},
        )
        with pytest.raises(RuntimeError, match="Email address blocked"):
            client.step1_create_signup("a@example.com", "TOK")

    def test_prepare_verification_ok(self):
        client = self._client()
        client.ex.post.return_value = _resp(payload={"response": {}})
        client.step2_prepare_email_code("signup_9")
        url = client.ex.post.call_args[0][0]
        assert "/prepare_verification" in url
        assert client.ex.post.call_args[1]["data"]["strategy"] == "email_code"

    def test_verify_code_ok(self):
        client = self._client()
        client.ex.post.return_value = _resp(payload={
            "response": {"object": "sign_up", "status": "complete", "id": "signup_9"},
        })
        payload = client.step3_verify_code("signup_9", "123456")
        assert payload["response"]["status"] == "complete"

    def test_verify_bad_code(self):
        client = self._client()
        client.ex.post.return_value = _resp(
            status=422,
            payload={"errors": [{"long_message": "Incorrect code"}]},
        )
        with pytest.raises(RuntimeError, match="Incorrect code"):
            client.step3_verify_code("signup_9", "000000")

    def test_identity_from_payload(self):
        client = self._client()
        payload = {"client": {
            "sessions": [{"id": "sess_1", "user": {"id": "user_1"}}],
        }}
        assert client.step4_identity(payload) == {"session_id": "sess_1", "user_id": "user_1"}

    def test_identity_falls_back_to_client_endpoint(self):
        client = self._client()
        client.ex.get.return_value = _resp(payload={"response": {
            "object": "client",
            "last_active_session_id": "sess_2",
            "sessions": [{"id": "sess_2", "user": {"id": "user_2"}}],
        }})
        assert client.step4_identity({}) == {"session_id": "sess_2", "user_id": "user_2"}

    def test_identity_missing_session_raises(self):
        client = self._client()
        client.ex.get.return_value = _resp(payload={"response": {"sessions": []}})
        with pytest.raises(RuntimeError, match="session_id"):
            client.step4_identity({})

    def test_jwt_flat_shape(self):
        client = self._client()
        client.ex.post.return_value = _resp(payload={"object": "token", "jwt": "eyJ.x.y"})
        assert client.step5_session_jwt("sess_1") == "eyJ.x.y"

    def test_jwt_nested_shape(self):
        client = self._client()
        client.ex.post.return_value = _resp(payload={"response": {"jwt": "eyJ.a.b"}})
        assert client.step5_session_jwt("sess_1") == "eyJ.a.b"

    def test_api_key_ok(self):
        client = self._client()
        client.ex.get_cookies.return_value = {"__client": "c", "__client_uat": "1"}
        client.ex.post.return_value = _resp(payload={"token": "sk-" + "a" * 22})
        created = client.step6_create_api_key("jwt", alias="test")
        assert created["token"].startswith("sk-")
        headers = client.ex.post.call_args[1]["headers"]
        assert "__session=jwt" in headers["cookie"]
        assert "__client=c" in headers["cookie"]

    def test_api_key_plan_required(self):
        client = self._client()
        client.ex.post.return_value = _resp(
            status=402,
            payload={"error": "Add an active plan or usage pack before creating API keys."},
            text="Add an active plan or usage pack",
        )
        with pytest.raises(PlanRequiredError, match="plan or usage pack"):
            client.step6_create_api_key("jwt")

    def test_api_key_masked_token_rejected(self):
        client = self._client()
        client.ex.post.return_value = _resp(payload={"token": "sk-kp6" + "..." + "NbTw"})
        with pytest.raises(RuntimeError, match="掩码"):
            client.step6_create_api_key("jwt")


class TestPlatformPlugin:
    def _platform(self, extra=None):
        from platforms.moonmath.plugin import MoonMathPlatform
        from core.base_platform import RegisterConfig

        return MoonMathPlatform(
            config=RegisterConfig(executor_type="protocol", extra=extra or {}),
        )

    def test_metadata(self):
        p = self._platform()
        assert p.name == "moonmath"
        assert p.display_name == "MoonMath"
        assert p.supported_executors == ["protocol"]
        assert "cfsolve" in p.protocol_captcha_order
        assert p.protocol_captcha_order[0] == "cfsolve"

    def test_map_result_ready(self):
        p = self._platform()
        result = p._map_result({
            "email": "a@example.com",
            "user_id": "user_1",
            "api_key": "sk-" + "b" * 22,
            "session_id": "sess_1",
        })
        assert result.email == "a@example.com"
        assert result.extra["api_key"].startswith("sk-")
        assert result.extra["plan_required"] is False

    def test_map_result_plan_required_keeps_account(self):
        p = self._platform()
        result = p._map_result({
            "email": "a@example.com",
            "user_id": "user_1",
            "plan_required": True,
            "plan_required_reason": "需要订阅",
        })
        assert result.email == "a@example.com"
        assert result.extra["plan_required"] is True
        assert result.extra["api_key"] == ""

    def test_adapter_uses_captcha_and_executor(self):
        adapter = self._platform().build_protocol_mailbox_adapter()
        assert adapter.use_captcha is True
        assert adapter.use_executor is True
        assert adapter.otp_spec is not None

    def test_password_recorded_when_site_enables_policy(self):
        p = self._platform({"default_password": "pw-123"})
        assert p._prepare_registration_password(None) == "pw-123"

    def test_check_valid_without_key(self):
        from core.base_platform import Account

        p = self._platform()
        account = Account(platform="moonmath", email="a@example.com", password="")
        assert p.check_valid(account) is False
