import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from click.testing import CliRunner

from lex.bin.lex import lex
from lex.tools.test_groups import LexTestConfig
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


class LexPytestReportGenerationTests(TestCase):
    def test_report_generation_requires_coverage_data(self):
        with TemporaryDirectory() as tmp_dir:
            config = self._build_config(Path(tmp_dir))
            plugin = SimpleNamespace(results={}, coverage_summary=None, run_duration=None)
            coverage_runner = Mock()
            coverage_runner.report.side_effect = RuntimeError("no data to report")
            coverage_module = ModuleType("coverage")
            coverage_module.Coverage = Mock(return_value=coverage_runner)
            write_pdf_report = Mock()
            runner = CliRunner()

            with patch.dict(sys.modules, {"coverage": coverage_module}):
                with (
                    patch("lex.bin.lex.os.chdir"),
                    patch("lex.bin.lex._bootstrap_django"),
                    patch("lex.bin.lex._has_explicit_pytest_target", return_value=False),
                    patch("lex.tools.test_groups.resolve_config", return_value=config),
                    patch("lex.tools.test_groups.LexGroupsPlugin", return_value=plugin),
                    patch("lex.tools.test_groups.write_pdf_report", write_pdf_report),
                    patch("pytest.main", return_value=0),
                ):
                    result = runner.invoke(lex, ["pytest", "--report"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Coverage data is required for Lex test report PDF/HTML artifacts.", result.output)
        self.assertIn("The report would otherwise show `n/a`.", result.output)
        coverage_runner.start.assert_called_once()
        coverage_runner.stop.assert_called_once()
        coverage_runner.html_report.assert_not_called()
        write_pdf_report.assert_not_called()

    def test_report_generation_writes_html_coverage_before_pdf(self):
        with TemporaryDirectory() as tmp_dir:
            config = self._build_config(Path(tmp_dir))
            plugin = SimpleNamespace(results={}, coverage_summary=None, run_duration=None)
            coverage_runner = Mock()
            coverage_runner.report.return_value = 83.2

            def _write_html_report(*, directory, ignore_errors):
                output_dir = Path(directory)
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "index.html").write_text("<html></html>", encoding="utf-8")

            coverage_runner.html_report.side_effect = _write_html_report
            coverage_module = ModuleType("coverage")
            coverage_module.Coverage = Mock(return_value=coverage_runner)
            write_pdf_report = Mock(
                return_value=config.report_dir / "lex-test-report-20260512-010203.pdf"
            )
            runner = CliRunner()

            with patch.dict(sys.modules, {"coverage": coverage_module}):
                with (
                    patch("lex.bin.lex.os.chdir"),
                    patch("lex.bin.lex._bootstrap_django"),
                    patch("lex.bin.lex._has_explicit_pytest_target", return_value=False),
                    patch("lex.tools.test_groups.resolve_config", return_value=config),
                    patch("lex.tools.test_groups.LexGroupsPlugin", return_value=plugin),
                    patch("lex.tools.test_groups.write_pdf_report", write_pdf_report),
                    patch("pytest.main", return_value=0) as pytest_main,
                ):
                    result = runner.invoke(lex, ["pytest", "--report", "-q"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(plugin.coverage_summary["display"], "83.2%")
        coverage_runner.start.assert_called_once()
        coverage_runner.stop.assert_called_once()
        coverage_runner.html_report.assert_called_once()
        self.assertEqual(
            Path(coverage_runner.html_report.call_args.kwargs["directory"]),
            config.coverage_html_dir,
        )
        self.assertEqual(
            coverage_runner.html_report.call_args.kwargs["ignore_errors"],
            True,
        )
        write_pdf_report.assert_called_once()
        pytest_main.assert_called_once_with(["Tests", "-q"], plugins=[plugin])
        self.assertIn("Lex test report:", result.output)
        self.assertIn(
            f"Lex test coverage HTML: {config.coverage_html_dir / 'index.html'}",
            result.output,
        )

    def _build_config(self, project_root: Path) -> LexTestConfig:
        return LexTestConfig(
            project_root=project_root,
            source="test",
            tests_entrypoint="Tests",
            report_output_dir="reports/workflow-run-123",
            global_receivers=[],
            email={"from_email": "", "from_name": "", "reply_to": "", "subject_prefix": ""},
            groups=[],
            group_assignments={},
        )
