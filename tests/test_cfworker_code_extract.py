"""CFWorkerMailbox 验证码提取 + 代理隔离回归。"""
from __future__ import annotations

import os

import pytest

from core.base_mailbox import CFWorkerMailbox, extract_verification_code


class TestExtractVerificationCode:
    def test_subject_code_wins_over_boundary_digits(self):
        """真实事故：MIME boundary 里的 435752 被当成验证码，真码 677406 在 Subject。"""
        raw = (
            "Subject: 677406 is your verification code\r\n"
            "Content-Type: multipart/alternative; boundary=435752e02cbe861819b763403dc7e9\r\n"
            "\r\n"
            "--435752e02cbe861819b763403dc7e9\r\n"
            "Content-Type: text/plain\r\n"
            "\r\n"
            "Your code is 677406\r\n"
        )
        assert extract_verification_code(raw) == "677406"

    def test_body_fallback_ignores_boundary(self):
        raw = (
            "Subject: Welcome to the service\r\n"
            "Content-Type: multipart/alternative; boundary=435752e02cbe861819b763403dc7e9\r\n"
            "\r\n"
            "--435752e02cbe861819b763403dc7e9\r\n"
            "\r\n"
            "Verification code: 112233\r\n"
        )
        assert extract_verification_code(raw) == "112233"

    def test_keyword_proximity_beats_bare_number(self):
        raw = "Subject: hi\r\n\r\nOrder 999888 shipped. Your verification code is 123456"
        assert extract_verification_code(raw) == "123456"

    def test_timestamps_are_stripped(self):
        raw = "Subject: hi\r\n\r\ncreated at 1793530388, code 445566"
        assert extract_verification_code(raw) == "445566"

    def test_email_addresses_are_stripped(self):
        raw = "Subject: hi\r\n\r\nno-reply123456@example.com\r\ncode 778899"
        assert extract_verification_code(raw) == "778899"

    def test_custom_pattern_wins_after_subject(self):
        raw = "Subject: hi\r\n\r\nuse ABC-424242 to confirm"
        assert extract_verification_code(raw, r"ABC-(\d{6})") == "424242"

    def test_returns_empty_when_no_code(self):
        assert extract_verification_code("Subject: hi\r\n\r\nnothing here") == ""

    def test_trae_span_format_handled_upstream(self):
        """<span> 分支在 wait_for_code 里，这里只确认 helper 不误伤。"""
        raw = "Subject: hi\r\n\r\n<span>135790</span>"
        assert extract_verification_code(raw) == "135790"


class TestProxyIsolation:
    def test_session_does_not_inherit_env_proxy(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9/")
        box = CFWorkerMailbox(api_url="https://example.invalid", admin_token="x")
        assert box._session.trust_env is False
        assert not box._session.proxies

    def test_explicit_proxy_is_honoured(self):
        box = CFWorkerMailbox(api_url="https://example.invalid", admin_token="x", proxy="http://127.0.0.1:8080")
        assert box._session.proxies.get("https") == "http://127.0.0.1:8080"

    def test_headers_carry_admin_token(self):
        box = CFWorkerMailbox(api_url="https://example.invalid", admin_token="tok123")
        assert box._headers()["x-admin-auth"] == "tok123"
