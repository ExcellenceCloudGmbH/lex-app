"""
Tests for the Init management command's argument parsing and migration forwarding.
"""
from unittest import TestCase
from unittest.mock import patch, MagicMock, call
from io import StringIO

from lex.lex_app.management.commands.Init import Command


class ParseExtraArgsTest(TestCase):
    """Tests for Command._parse_extra_args()"""

    def test_empty_string(self):
        pos, opts = Command._parse_extra_args('')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {})

    def test_none_like_input(self):
        pos, opts = Command._parse_extra_args('   ')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {})

    def test_single_boolean_flag(self):
        pos, opts = Command._parse_extra_args('--merge')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {'merge': True})

    def test_hyphenated_flag_converted_to_underscore(self):
        pos, opts = Command._parse_extra_args('--run-syncdb')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {'run_syncdb': True})

    def test_single_positional(self):
        pos, opts = Command._parse_extra_args('myapp')
        self.assertEqual(pos, ['myapp'])
        self.assertEqual(opts, {})

    def test_multiple_positionals(self):
        pos, opts = Command._parse_extra_args('myapp 0001')
        self.assertEqual(pos, ['myapp', '0001'])
        self.assertEqual(opts, {})

    def test_flag_with_value(self):
        pos, opts = Command._parse_extra_args('--database default')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {'database': 'default'})

    def test_fake_with_app_and_migration(self):
        """--fake myapp 0001 → flag + two positionals"""
        pos, opts = Command._parse_extra_args('--fake myapp 0001')
        self.assertEqual(opts, {'fake': 'myapp'})
        self.assertEqual(pos, ['0001'])

    def test_merge_and_empty_with_app(self):
        pos, opts = Command._parse_extra_args('--merge --empty myapp')
        self.assertEqual(opts, {'merge': True, 'empty': 'myapp'})
        self.assertEqual(pos, [])

    def test_multiple_boolean_flags(self):
        pos, opts = Command._parse_extra_args('--fake --run-syncdb')
        self.assertEqual(pos, [])
        self.assertEqual(opts, {'fake': True, 'run_syncdb': True})

    def test_quoted_values_preserved(self):
        """shlex handles quoted strings correctly"""
        pos, opts = Command._parse_extra_args('--name "my migration"')
        self.assertEqual(opts, {'name': 'my migration'})

    def test_complex_combination(self):
        pos, opts = Command._parse_extra_args('--fake --database default myapp 0003')
        self.assertEqual(opts, {'fake': True, 'database': 'default'})
        self.assertEqual(pos, ['myapp', '0003'])


class ExecuteMigrationsForwardingTest(TestCase):
    """Tests that execute_migrations correctly forwards extra args to call_command."""

    def setUp(self):
        self.cmd = Command()
        self.cmd.stdout = StringIO()
        self.cmd.stderr = StringIO()

    @patch('lex.lex_app.management.commands.Init.call_command')
    def test_no_extra_args(self, mock_call):
        """Without extra args, call_command receives only the base kwargs."""
        mock_call.return_value = None

        # Patch check_unapplied_migrations to return False (no pending)
        with patch.object(self.cmd, 'check_unapplied_migrations', return_value=False):
            self.cmd.execute_migrations(verbosity=1, create_new=True)

        # makemigrations should have been called with base args only
        mock_call.assert_called_once_with(
            'makemigrations',
            verbosity=1,
            interactive=False,
            stdout=self.cmd.stdout,
            stderr=self.cmd.stderr,
            no_input=True,
        )

    @patch('lex.lex_app.management.commands.Init.call_command')
    def test_makemigrations_extra_merge(self, mock_call):
        """--makemigrations-args='--merge' forwards merge=True."""
        mock_call.return_value = None

        with patch.object(self.cmd, 'check_unapplied_migrations', return_value=False):
            self.cmd.execute_migrations(
                verbosity=1,
                create_new=True,
                makemigrations_extra='--merge',
            )

        mock_call.assert_called_once_with(
            'makemigrations',
            verbosity=1,
            interactive=False,
            stdout=self.cmd.stdout,
            stderr=self.cmd.stderr,
            no_input=True,
            merge=True,
        )

    @patch('lex.lex_app.management.commands.Init.call_command')
    def test_migrate_extra_run_syncdb(self, mock_call):
        """--migrate-args='--run-syncdb' forwards run_syncdb=True to migrate."""
        mock_call.return_value = None

        with patch.object(self.cmd, 'check_unapplied_migrations', return_value=True):
            self.cmd.execute_migrations(
                verbosity=1,
                create_new=False,
                migrate_extra='--run-syncdb',
            )

        mock_call.assert_called_once_with(
            'migrate',
            verbosity=1,
            interactive=False,
            stdout=self.cmd.stdout,
            stderr=self.cmd.stderr,
            run_syncdb=True,
        )

    @patch('lex.lex_app.management.commands.Init.call_command')
    def test_migrate_extra_fake_with_app(self, mock_call):
        """--migrate-args='--fake myapp 0001' forwards positional + fake flag."""
        mock_call.return_value = None

        with patch.object(self.cmd, 'check_unapplied_migrations', return_value=True):
            self.cmd.execute_migrations(
                verbosity=1,
                create_new=False,
                migrate_extra='--fake myapp 0001',
            )

        mock_call.assert_called_once_with(
            'migrate',
            '0001',
            verbosity=1,
            interactive=False,
            stdout=self.cmd.stdout,
            stderr=self.cmd.stderr,
            fake='myapp',
        )

    @patch('lex.lex_app.management.commands.Init.call_command')
    def test_both_extra_args_forwarded(self, mock_call):
        """Both makemigrations and migrate receive their respective extra args."""
        mock_call.return_value = None

        with patch.object(self.cmd, 'check_unapplied_migrations', return_value=True):
            self.cmd.execute_migrations(
                verbosity=2,
                create_new=True,
                makemigrations_extra='--empty myapp',
                migrate_extra='--run-syncdb',
            )

        self.assertEqual(mock_call.call_count, 2)

        # First call: makemigrations with --empty myapp
        mm_call = mock_call.call_args_list[0]
        self.assertEqual(mm_call, call(
            'makemigrations',
            verbosity=2,
            interactive=False,
            stdout=self.cmd.stdout,
            stderr=self.cmd.stderr,
            no_input=True,
            empty='myapp',
        ))

        # Second call: migrate with --run-syncdb
        mig_call = mock_call.call_args_list[1]
        self.assertEqual(mig_call, call(
            'migrate',
            verbosity=2,
            interactive=False,
            stdout=self.cmd.stdout,
            stderr=self.cmd.stderr,
            run_syncdb=True,
        ))

    @patch('lex.lex_app.management.commands.Init.call_command')
    def test_skip_makemigrations(self, mock_call):
        """create_new=False skips makemigrations entirely."""
        mock_call.return_value = None

        with patch.object(self.cmd, 'check_unapplied_migrations', return_value=True):
            self.cmd.execute_migrations(
                verbosity=1,
                create_new=False,
            )

        # Only migrate should be called
        mock_call.assert_called_once()
        self.assertEqual(mock_call.call_args_list[0][0][0], 'migrate')

    @patch('lex.lex_app.management.commands.Init.call_command')
    def test_migration_failure_returns_false(self, mock_call):
        """If call_command raises, execute_migrations returns False."""
        mock_call.side_effect = Exception('migration boom')

        result = self.cmd.execute_migrations(
            verbosity=1,
            create_new=True,
        )

        self.assertFalse(result)
