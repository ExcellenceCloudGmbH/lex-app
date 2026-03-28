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

    @patch("lex.bin.lex._install_dynamic_commands")
    @patch("lex.bin.lex.lex", autospec=True)
    @patch("sys.argv", ["lex", "init"])
    def test_main_bootstraps_django_for_init_management_command(self, lex_group, install_dynamic_commands):
        lex_group.return_value = 0

        lex_cli.main()

        install_dynamic_commands.assert_called_once_with()
