"""cfsolve captcha provider 测试。"""
from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from providers.captcha.cfsolve import CfsolveCaptcha, CfsolveError


def _proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["cfsolve"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class TestParse:
    def test_widget_token_is_last_standalone_line(self):
        out = "loading\nsolving...\n0.abcDEF123_xyz-token\n"
        assert CfsolveCaptcha._parse(out) == "0.abcDEF123_xyz-token"

    def test_skips_lines_with_spaces(self):
        out = "done in 3s\nTOKEN123\n"
        assert CfsolveCaptcha._parse(out) == "TOKEN123"

    def test_clearance_is_extracted(self):
        out = "cf_clearance=abc123def456\n"
        assert CfsolveCaptcha._parse(out, want_clearance=True) == "abc123def456"

    def test_clearance_missing_returns_empty(self):
        assert CfsolveCaptcha._parse("nothing here\n", want_clearance=True) == ""

    def test_empty_stdout(self):
        assert CfsolveCaptcha._parse("") == ""


class TestFromConfig:
    def test_defaults(self):
        cap = CfsolveCaptcha.from_config({})
        assert cap.timeout == 240
        assert cap.retries == 4
        assert cap.blank_page is True
        assert cap.binary.endswith("run_challenge.sh")

    def test_overrides(self):
        cap = CfsolveCaptcha.from_config({
            "cfsolve_bin": "/opt/cfsolve/run.sh",
            "cfsolve_fingerprint": "/opt/fp.json",
            "cfsolve_timeout": "90",
            "cfsolve_retries": "2",
            "cfsolve_blank_page": "false",
            "cfsolve_proxy": "socks5h://127.0.0.1:1080",
        })
        assert cap.binary == "/opt/cfsolve/run.sh"
        assert cap.fingerprint == "/opt/fp.json"
        assert cap.timeout == 90
        assert cap.retries == 2
        assert cap.blank_page is False
        assert cap.proxy == "socks5h://127.0.0.1:1080"

    def test_bad_numbers_fall_back(self):
        cap = CfsolveCaptcha.from_config({"cfsolve_timeout": "abc", "cfsolve_retries": ""})
        assert cap.timeout == 240
        assert cap.retries == 4


class TestSolve:
    def test_solve_turnstile_returns_token(self):
        cap = CfsolveCaptcha(binary="/bin/true", fingerprint="/fp.json")
        with mock.patch("subprocess.run", return_value=_proc(stdout="TOK_123\n")):
            assert cap.solve_turnstile("https://a.test/sign-up", "0xKEY") == "TOK_123"

    def test_missing_sitekey_raises(self):
        cap = CfsolveCaptcha(binary="/bin/true")
        with pytest.raises(CfsolveError):
            cap.solve_turnstile("https://a.test", "")

    def test_nonzero_exit_reports_last_stderr_line(self):
        cap = CfsolveCaptcha(binary="/bin/true", retries=1)
        with mock.patch("subprocess.run", return_value=_proc(
                returncode=1, stderr="warning\nError: boom happened\n")):
            with pytest.raises(CfsolveError, match="boom happened"):
                cap.solve_turnstile("https://a.test", "0xKEY")

    def test_retries_on_retryable_error(self):
        cap = CfsolveCaptcha(binary="/bin/true", retries=3, retry_delay=0)
        err = _proc(returncode=1, stderr="Error: the initial VM program does not build the key transform\n")
        ok = _proc(stdout="TOKEN_OK\n")
        with mock.patch("subprocess.run", side_effect=[err, ok]) as run:
            assert cap.solve_turnstile("https://a.test", "0xKEY") == "TOKEN_OK"
            assert run.call_count == 2

    def test_no_retry_on_fatal_error(self):
        cap = CfsolveCaptcha(binary="/bin/true", retries=3, retry_delay=0)
        err = _proc(returncode=1, stderr="Error: something fatal\n")
        with mock.patch("subprocess.run", return_value=err) as run:
            with pytest.raises(CfsolveError):
                cap.solve_turnstile("https://a.test", "0xKEY")
            assert run.call_count == 1

    def test_timeout_raises(self):
        cap = CfsolveCaptcha(binary="/bin/true", retries=1)
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            with pytest.raises(CfsolveError, match="超时"):
                cap.solve_turnstile("https://a.test", "0xKEY")

    def test_solve_page_returns_clearance(self):
        cap = CfsolveCaptcha(binary="/bin/true")
        with mock.patch("subprocess.run", return_value=_proc(stdout="cf_clearance=CLEAR99\n")):
            assert cap.solve_page("https://a.test") == "CLEAR99"

    def test_image_not_supported(self):
        with pytest.raises(NotImplementedError):
            CfsolveCaptcha(binary="/bin/true").solve_image("b64")

    def test_command_includes_sitekey_and_blank_page(self):
        cap = CfsolveCaptcha(binary="/bin/true", fingerprint="/fp.json")
        with mock.patch("subprocess.run", return_value=_proc(stdout="TOK\n")) as run:
            cap.solve_turnstile("https://a.test/sign-up", "0xKEY")
            cmd = run.call_args[0][0]
            assert "--sitekey" in cmd and "0xKEY" in cmd
            assert "--blank-page" in cmd
            assert "--fingerprint" in cmd and "/fp.json" in cmd
            assert "--solve" in cmd
