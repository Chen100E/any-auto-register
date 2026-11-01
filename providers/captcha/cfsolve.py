"""cfsolve — 协议层 Turnstile 求解器（无需浏览器）。

cfsolve 用一个固定的设备指纹在协议层回答 Cloudflare 下发的挑战程序，直接
产出 widget token 或整页盾的 cf_clearance。相比浏览器方案它不吃 CPU、不受
无头检测影响，但对 api.js 的构建版本敏感 —— 遇到不可解的构建时换一次构建
即可，所以这里内置重试。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile

from core.base_captcha import BaseCaptcha
from providers.registry import register_provider

#: 未配置时的兜底路径（ci13 上的部署位置）
DEFAULT_BINARY = "/app/working/cfsolve/run_challenge.sh"
DEFAULT_FINGERPRINT = "/app/working/cfsolve/profiles/safari-26-ios.json"

#: 命中这些错误说明是 api.js 构建不可解，重试会落到别的构建上
RETRYABLE_MARKERS = (
    "does not build the key transform",
    "unexpected response",
    "failed to load",
)

_CLEARANCE_RE = re.compile(r"cf_clearance=([A-Za-z0-9_\-.]+)")


class CfsolveError(RuntimeError):
    """cfsolve 执行失败。"""


@register_provider("captcha", "cfsolve")
class CfsolveCaptcha(BaseCaptcha):
    """调用本地 cfsolve 二进制解 Turnstile。"""

    def __init__(
        self,
        binary: str = "",
        fingerprint: str = "",
        timeout: int = 240,
        retries: int = 4,
        retry_delay: float = 2.0,
        proxy: str = "",
        blank_page: bool = True,
    ):
        self.binary = binary or DEFAULT_BINARY
        self.fingerprint = fingerprint or DEFAULT_FINGERPRINT
        self.timeout = max(30, int(timeout))
        self.retries = max(1, int(retries))
        self.retry_delay = max(0.0, float(retry_delay))
        self.proxy = proxy
        # 目标页自己挂着整页盾时，直接在空白页上渲染 widget 更稳
        self.blank_page = blank_page

    # ------------------------------------------------------------------ 构造
    @classmethod
    def from_config(cls, config: dict) -> "CfsolveCaptcha":
        config = config or {}

        def _str(key: str, default: str = "") -> str:
            value = config.get(key)
            return str(value).strip() if value not in (None, "") else default

        def _int(key: str, default: int) -> int:
            try:
                return int(str(config.get(key) or default).strip())
            except (TypeError, ValueError):
                return default

        def _float(key: str, default: float) -> float:
            try:
                return float(str(config.get(key) or default).strip())
            except (TypeError, ValueError):
                return default

        blank_raw = _str("cfsolve_blank_page", "1").lower()
        return cls(
            binary=_str("cfsolve_bin"),
            fingerprint=_str("cfsolve_fingerprint"),
            timeout=_int("cfsolve_timeout", 240),
            retries=_int("cfsolve_retries", 4),
            retry_delay=_float("cfsolve_retry_delay", 2.0),
            proxy=_str("cfsolve_proxy"),
            blank_page=blank_raw not in {"0", "false", "no", "off"},
        )

    # ------------------------------------------------------------------ 对外
    def solve_turnstile(self, page_url: str, site_key: str) -> str:
        """返回 widget token。"""
        if not site_key:
            raise CfsolveError("cfsolve 需要 site_key 才能解 widget")
        return self._solve(page_url, site_key, want_clearance=False)

    def solve_page(self, page_url: str) -> str:
        """解整页 5 秒盾，返回 cf_clearance 的值。"""
        return self._solve(page_url, "", want_clearance=True)

    def solve_image(self, image_b64: str) -> str:
        raise NotImplementedError("cfsolve 不支持图片验证码")

    # ------------------------------------------------------------------ 内部
    def _binary_argv(self) -> list[str]:
        binary = self.binary or DEFAULT_BINARY
        if not binary:
            raise CfsolveError("未配置 cfsolve 可执行文件")
        resolved = shutil.which(binary) if "/" not in binary else binary
        if not resolved:
            raise CfsolveError(f"找不到 cfsolve 可执行文件: {binary}")
        return [resolved]

    def _solve(self, page_url: str, site_key: str, *, want_clearance: bool) -> str:
        import time

        errors: list[str] = []
        for attempt in range(1, self.retries + 1):
            try:
                return self._run_once(page_url, site_key, want_clearance=want_clearance)
            except CfsolveError as exc:
                message = str(exc)
                errors.append(f"[{attempt}] {message}")
                retryable = any(m in message.lower() for m in RETRYABLE_MARKERS)
                if attempt >= self.retries or not retryable:
                    raise
                time.sleep(self.retry_delay)
        raise CfsolveError("；".join(errors))

    def _run_once(self, page_url: str, site_key: str, *, want_clearance: bool) -> str:
        outdir = tempfile.mkdtemp(prefix="cfsolve_")
        cmd = self._binary_argv() + [
            "--page", page_url, outdir,
            "--solve",
            "--fingerprint", self.fingerprint,
        ]
        if site_key:
            cmd += ["--sitekey", site_key]
            if self.blank_page:
                cmd.append("--blank-page")
        if self.proxy:
            cmd += ["--proxy", self.proxy]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raise CfsolveError(f"cfsolve 超时（{self.timeout}s）")
        except OSError as exc:
            raise CfsolveError(f"cfsolve 启动失败: {exc}")
        finally:
            shutil.rmtree(outdir, ignore_errors=True)

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        if proc.returncode != 0:
            detail = ""
            for line in reversed(stderr.splitlines()):
                if line.strip():
                    detail = line.strip()
                    break
            raise CfsolveError(detail or f"cfsolve 退出码 {proc.returncode}")

        token = self._parse(stdout, want_clearance=want_clearance)
        if not token:
            raise CfsolveError(f"cfsolve 未返回 token: {(stdout or stderr)[:200]}")
        return token

    @staticmethod
    def _parse(stdout: str, *, want_clearance: bool = False) -> str:
        """cfsolve 把结果打在 stdout 单行，进度全走 stderr。"""
        match = _CLEARANCE_RE.search(stdout)
        if match:
            return match.group(1)
        if want_clearance:
            return ""
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line or " " in line:
                continue
            return line
        return ""
