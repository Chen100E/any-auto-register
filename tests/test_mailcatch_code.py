"""mailcatch 验证码提取：不能被邮件里的年份/订单号带偏。"""
from __future__ import annotations

import unittest

from providers.mailbox.mailcatch import MailCatchMailbox


class TestExtractCode(unittest.TestCase):
    def _x(self, mail, keyword="", pattern=None):
        return MailCatchMailbox._extract_code(mail, keyword, pattern)

    def test_prefers_six_digit_code_over_worker_otp(self):
        mail = {
            "subject": "Sign in to Blink",
            "body": "Your code is 481920. © 2026 Blink",
            "otp": "2026",
        }
        self.assertEqual(self._x(mail), "481920")

    def test_uses_worker_otp_when_six_digits(self):
        mail = {"subject": "code", "body": "", "otp": "116531"}
        self.assertEqual(self._x(mail), "116531")

    def test_rejects_non_numeric_worker_otp(self):
        mail = {"subject": "hi", "body": "no digits here", "otp": "abc"}
        self.assertEqual(self._x(mail), "")

    def test_falls_back_to_worker_otp_when_no_six_digit(self):
        mail = {"subject": "hi", "body": "use 1234 to verify", "otp": "1234"}
        self.assertEqual(self._x(mail), "1234")

    def test_keyword_filter_still_applies(self):
        mail = {"subject": "other", "body": "123456", "otp": "123456"}
        self.assertEqual(self._x(mail, keyword="anything"), "")

    def test_explicit_pattern_wins(self):
        mail = {"subject": "x", "body": "code: 987654", "otp": "2026"}
        self.assertEqual(self._x(mail, pattern=r"code:\s*(\d{6})"), "987654")


if __name__ == "__main__":
    unittest.main()
