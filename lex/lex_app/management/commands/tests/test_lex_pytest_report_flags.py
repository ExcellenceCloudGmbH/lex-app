from unittest import TestCase

from lex.tools.test_groups import parse_lex_pytest_args


class LexPytestReportFlagParsingTests(TestCase):
    def test_report_flag_generates_pdf_only(self):
        parsed = parse_lex_pytest_args(["--report", "-q", "tests/test_example.py"])

        self.assertTrue(parsed.report)
        self.assertFalse(parsed.report_and_email)
        self.assertFalse(parsed.send_emails)
        self.assertEqual(parsed.forwarded, ["-q", "tests/test_example.py"])

    def test_report_and_email_is_distinct_from_report_only(self):
        parsed = parse_lex_pytest_args(
            ["--report-and-email", "--send-emails", "-k", "smoke"]
        )

        self.assertFalse(parsed.report)
        self.assertTrue(parsed.report_and_email)
        self.assertTrue(parsed.send_emails)
        self.assertEqual(parsed.forwarded, ["-k", "smoke"])
