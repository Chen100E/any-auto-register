"""站点常量可配置化：extra > 环境变量 > 默认值。"""
from __future__ import annotations

import os

import pytest

from core.registration.helpers import resolve_flag, resolve_setting


class TestResolveSetting:
    def test_extra_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("DEMO_KEY", "from-env")
        assert resolve_setting({"demo_key": "from-extra"}, ("demo_key",), "dflt", env=("DEMO_KEY",)) == "from-extra"

    def test_env_wins_over_default(self, monkeypatch):
        monkeypatch.setenv("DEMO_KEY", "from-env")
        assert resolve_setting({}, ("demo_key",), "dflt", env=("DEMO_KEY",)) == "from-env"

    def test_default_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("DEMO_KEY", raising=False)
        assert resolve_setting(None, ("demo_key",), "dflt", env=("DEMO_KEY",)) == "dflt"

    def test_blank_values_are_ignored(self, monkeypatch):
        monkeypatch.setenv("DEMO_KEY", "   ")
        assert resolve_setting({"demo_key": ""}, ("demo_key",), "dflt", env=("DEMO_KEY",)) == "dflt"

    def test_multiple_keys_in_order(self):
        assert resolve_setting({"b": "second"}, ("a", "b"), "dflt") == "second"

    def test_cast_applied(self):
        assert resolve_setting({"n": "42"}, ("n",), 0, cast=int) == 42


class TestResolveFlag:
    @pytest.mark.parametrize("raw,expected", [
        ("1", True), ("true", True), ("yes", True), ("on", True),
        ("0", False), ("false", False), ("no", False), ("off", False),
    ])
    def test_string_forms(self, raw, expected):
        assert resolve_flag({"flag": raw}, ("flag",), not expected) is expected

    def test_bool_passthrough(self):
        assert resolve_flag({"flag": False}, ("flag",), True) is False

    def test_default(self):
        assert resolve_flag({}, ("flag",), True) is True


class TestModuleConstantsOverridable:
    """环境变量应能覆盖写死的站点常量，且默认值保持不变。

    用子进程验证：reload 会替换模块对象，污染同进程内其他测试对模块级常量的引用。
    """

    @staticmethod
    def _read(env: dict[str, str], expr: str) -> str:
        import subprocess
        import sys

        full_env = {**os.environ, **env}
        result = subprocess.run(
            [sys.executable, "-c", expr],
            capture_output=True, text=True, env=full_env, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def test_blink_firebase_key_env_override(self):
        expr = "import platforms.blink.core as m; print(m.FIREBASE_API_KEY)"
        default = self._read({}, expr)
        overridden = self._read({"BLINK_FIREBASE_API_KEY": "override-key"}, expr)
        assert overridden == "override-key"
        assert default != "override-key"

    def test_tavily_sitekey_env_override(self):
        expr = "import platforms.tavily.core as m; print(m.TURNSTILE_SITEKEY)"
        default = self._read({}, expr)
        overridden = self._read({"TAVILY_TURNSTILE_SITEKEY": "0xOVERRIDE"}, expr)
        assert overridden == "0xOVERRIDE"
        assert default != "0xOVERRIDE"

    def test_moonmath_base_env_override(self):
        expr = "import platforms.moonmath.core as m; print(m.APP)"
        default = self._read({}, expr)
        overridden = self._read({"MOONMATH_APP_BASE": "https://override.test"}, expr)
        assert overridden == "https://override.test"
        assert default != "https://override.test"


class TestWorkerSitekeyInjection:
    def test_blink_worker_uses_passed_sitekey(self):
        from platforms.blink.protocol_mailbox import BlinkProtocolMailboxWorker

        worker = BlinkProtocolMailboxWorker(sitekey="0xBLINK", signup_page="https://blink.new/sign-up")
        assert worker.sitekey == "0xBLINK"

    def test_blink_worker_falls_back_to_default(self):
        from platforms.blink.protocol_mailbox import BLINK_TURNSTILE_SITEKEY, BlinkProtocolMailboxWorker

        assert BlinkProtocolMailboxWorker().sitekey == BLINK_TURNSTILE_SITEKEY

    def test_anything_worker_uses_passed_sitekey(self):
        from platforms.anything.protocol_mailbox import AnythingProtocolMailboxWorker

        worker = AnythingProtocolMailboxWorker(sitekey="0xANY")
        assert worker.sitekey == "0xANY"

    def test_anything_worker_falls_back_to_default(self):
        from platforms.anything.protocol_mailbox import ANYTHING_TURNSTILE_SITEKEY, AnythingProtocolMailboxWorker

        assert AnythingProtocolMailboxWorker().sitekey == ANYTHING_TURNSTILE_SITEKEY
