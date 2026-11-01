"""验证 blink / anything 的 cfsolve Turnstile 接线。

不打网络：直接替换底层请求方法，检查提交体里是否带上了求解出来的 token。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from platforms.anything.core import AnythingClient
from platforms.blink.core import BlinkRegister
from platforms.blink.protocol_mailbox import (
    BLINK_TURNSTILE_SITEKEY,
    BlinkProtocolMailboxWorker,
)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.text = "{}"
        self._payload = payload if payload is not None else {"success": True}

    def json(self):
        return self._payload


class _FakeCaptcha:
    def __init__(self, token="solved-token"):
        self.token = token
        self.calls: list[tuple[str, str]] = []

    def solve_turnstile(self, page_url, sitekey):
        self.calls.append((page_url, sitekey))
        return self.token


class TestBlinkTurnstileWiring(unittest.TestCase):
    def test_step1_sends_turnstile_token(self):
        reg = BlinkRegister()
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return _FakeResponse(200, {"success": True})

        with patch.object(reg.s, "post", side_effect=fake_post):
            ok = reg.step1_send_magic_link("a@b.com", turnstile_token="tok-123")

        self.assertTrue(ok)
        self.assertEqual(captured["json"]["turnstileToken"], "tok-123")
        self.assertEqual(captured["json"]["email"], "a@b.com")

    def test_step1_omits_token_when_empty(self):
        reg = BlinkRegister()
        captured = {}

        def fake_post(url, **kwargs):
            captured["json"] = kwargs.get("json")
            return _FakeResponse(200, {"success": True})

        with patch.object(reg.s, "post", side_effect=fake_post):
            reg.step1_send_magic_link("a@b.com")

        self.assertNotIn("turnstileToken", captured["json"])

    def test_worker_solves_before_sending(self):
        captcha = _FakeCaptcha("blink-tok")
        worker = BlinkProtocolMailboxWorker(log_fn=lambda *_: None, captcha_solver=captcha)
        seen = {}

        def fake_send(email, *, redirect_url="/", turnstile_token=""):
            seen["email"] = email
            seen["token"] = turnstile_token
            return True

        with patch.object(worker.client, "step1_send_magic_link", side_effect=fake_send):
            with patch.object(worker.client, "step2_redeem_magic_link", side_effect=RuntimeError("stop")):
                with self.assertRaises(RuntimeError):
                    worker.run(email="a@b.com", link_callback=lambda: "https://x/y?magic_token=abc")

        self.assertEqual(seen["token"], "blink-tok")
        self.assertEqual(captcha.calls[0][1], BLINK_TURNSTILE_SITEKEY)
        self.assertIn("/sign-up", captcha.calls[0][0])

    def test_worker_without_solver_sends_empty(self):
        worker = BlinkProtocolMailboxWorker(log_fn=lambda *_: None, captcha_solver=None)
        seen = {}

        def fake_send(email, *, redirect_url="/", turnstile_token=""):
            seen["token"] = turnstile_token
            return True

        with patch.object(worker.client, "step1_send_magic_link", side_effect=fake_send):
            with patch.object(worker.client, "step2_redeem_magic_link", side_effect=RuntimeError("stop")):
                with self.assertRaises(RuntimeError):
                    worker.run(email="a@b.com", link_callback=lambda: "https://x/y")

        self.assertEqual(seen["token"], "")


class TestAnythingTurnstileWiring(unittest.TestCase):
    def test_signup_includes_turnstile_token(self):
        client = AnythingClient(log_fn=lambda *_: None)
        captured = {}

        def fake_graphql(payload, **kwargs):
            captured["payload"] = payload
            return {"data": {"signUpAndStartAgent": {"success": True, "accessToken": "t"}}}

        with patch.object(client, "_graphql", side_effect=fake_graphql):
            client.sign_up_with_prompt(email="a@b.com", turnstile_token="any-tok")

        self.assertEqual(
            captured["payload"]["variables"]["input"]["turnstileToken"], "any-tok"
        )

    def test_signup_omits_token_when_empty(self):
        client = AnythingClient(log_fn=lambda *_: None)
        captured = {}

        def fake_graphql(payload, **kwargs):
            captured["payload"] = payload
            return {"data": {"signUpAndStartAgent": {"success": True}}}

        with patch.object(client, "_graphql", side_effect=fake_graphql):
            client.sign_up_with_prompt(email="a@b.com")

        self.assertNotIn("turnstileToken", captured["payload"]["variables"]["input"])

    def test_worker_solves_turnstile(self):
        from platforms.anything.protocol_mailbox import (
            ANYTHING_TURNSTILE_SITEKEY,
            AnythingProtocolMailboxWorker,
        )

        captcha = _FakeCaptcha("anything-tok")
        worker = AnythingProtocolMailboxWorker(log_fn=lambda *_: None, captcha_solver=captcha)
        seen = {}

        def fake_signup(**kwargs):
            seen.update(kwargs)
            return {"success": True}

        with patch.object(worker.client, "sign_up_with_prompt", side_effect=fake_signup):
            with self.assertRaises(RuntimeError):
                worker.run(email="a@b.com", link_callback=None)

        self.assertEqual(seen["turnstile_token"], "anything-tok")
        self.assertEqual(captcha.calls[0][1], ANYTHING_TURNSTILE_SITEKEY)


if __name__ == "__main__":
    unittest.main()
