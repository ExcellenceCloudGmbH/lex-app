"""
Cluster 1v: `lex ai-faq` hosted FAQ browser launcher.

Intent
------

`lex ai-faq` should open the hosted FAQ page directly, without spinning up
an in-process local HTTP server. This keeps FAQ updates decoupled from a
local package rebuild and gives operators a single canonical URL.

Scenario numbering continues after 1u (1.171-1.175), so this batch starts at
1.176.
"""

from __future__ import annotations

from unittest import TestCase, mock

import pytest

from lex.tools import ai_faq

pytestmark = pytest.mark.init


class TestCluster01v_AiFaqHostedUrlLauncher(TestCase):
    """Cluster 1v: keep the ai-faq command bound to the hosted page contract."""

    @mock.patch("lex.tools.ai_faq.webbrowser.open", return_value=True)
    def test_1_176_launches_default_hosted_faq_url(self, open_mock):
        """Scenario 1.176: default launch targets the hosted FAQ URL.

        Given: no override environment variable
        When: the FAQ launcher runs
        Then: it opens the default hosted URL and reports it
        """
        reports: list[str] = []

        with mock.patch.dict("os.environ", {}, clear=False):
            ai_faq.launch_ai_faq(reporter=reports.append)

        open_mock.assert_called_once_with(
            ai_faq.DEFAULT_FAQ_PAGE_URL,
            new=1,
            autoraise=True,
        )
        self.assertIn(
            f"Opening LEX AI FAQ: {ai_faq.DEFAULT_FAQ_PAGE_URL}",
            reports,
            "launcher should report the hosted FAQ URL it is opening",
        )

    @mock.patch("lex.tools.ai_faq.webbrowser.open", return_value=True)
    def test_1_177_respects_hosted_url_override_environment_variable(self, open_mock):
        """Scenario 1.177: `LEX_AI_FAQ_URL` overrides the hosted target URL.

        Given: an explicit hosted URL override in the environment
        When: the FAQ launcher runs
        Then: the override URL is opened instead of the default
        """
        reports: list[str] = []
        override = "https://example.test/custom-faq/"

        with mock.patch.dict("os.environ", {"LEX_AI_FAQ_URL": override}, clear=False):
            ai_faq.launch_ai_faq(reporter=reports.append)

        open_mock.assert_called_once_with(override, new=1, autoraise=True)
        self.assertIn(
            f"Opening LEX AI FAQ: {override}",
            reports,
            "launcher should report the environment-overridden URL",
        )

    @mock.patch("lex.tools.ai_faq.webbrowser.open", return_value=False)
    def test_1_178_reports_manual_open_instructions_when_browser_open_fails(self, open_mock):
        """Scenario 1.178: launcher reports manual fallback when auto-open fails.

        Given: the browser open call fails to launch a tab
        When: the FAQ launcher runs
        Then: it reports clear manual-open guidance to the operator
        """
        reports: list[str] = []

        ai_faq.launch_ai_faq(reporter=reports.append)

        open_mock.assert_called_once()
        self.assertIn(
            "The browser could not be opened automatically. Paste the URL above into any browser.",
            reports,
            "launcher must provide manual fallback instructions on open failure",
        )
