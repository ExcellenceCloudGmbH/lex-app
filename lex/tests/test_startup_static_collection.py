from unittest import TestCase
from unittest.mock import MagicMock, patch

from lex.bin import lex as lex_cli


class CollectStaticOnStartTests(TestCase):
    @patch.dict("os.environ", {"DEPLOYMENT_ENVIRONMENT": "PROD"}, clear=False)
    @patch("lex.bin.lex._bootstrap_django")
    def test_collects_static_in_deployed_env(self, bootstrap_django):
        call_command = MagicMock()
        bootstrap_django.return_value = (MagicMock(), call_command)

        lex_cli._collect_static_if_deployed()

        call_command.assert_called_once_with("collectstatic", interactive=False, verbosity=0)

    @patch.dict("os.environ", {"DEPLOYMENT_ENVIRONMENT": ""}, clear=False)
    @patch("lex.bin.lex._bootstrap_django")
    def test_skips_collectstatic_without_deployment_env(self, bootstrap_django):
        lex_cli._collect_static_if_deployed()

        bootstrap_django.assert_not_called()
