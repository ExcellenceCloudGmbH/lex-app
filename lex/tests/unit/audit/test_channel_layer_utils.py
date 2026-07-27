"""
Unit tests for ``lex.utilities.channel_layer._is_celery_worker``.

**What this tests (customer-visible behaviour)**

``_is_celery_worker`` determines the runtime strategy for WebSocket
broadcasts: Celery workers use a persistent background event loop,
while the ASGI server uses ``async_to_sync``.

**Why it matters**

If ``_is_celery_worker`` returns the wrong answer, Celery workers
create one event loop per broadcast, exhausting file descriptors and
crashing long calculations with ``OSError: Too many open files``.

**Methodology**

Pure environment-variable check — no DB.

Run::

    python manage.py test lex.tests.test_channel_layer_utils
"""

from unittest.mock import patch

from django.test import SimpleTestCase
from lex.utilities.channel_layer import _is_celery_worker


class TestIsCeleryWorker(SimpleTestCase):
    """Prove ``_is_celery_worker`` reads the env var correctly."""

    @patch.dict("os.environ", {"IS_RUNNING_IN_CELERY": "true"})
    def test_true_lowercase(self):
        self.assertTrue(_is_celery_worker())

    @patch.dict("os.environ", {"IS_RUNNING_IN_CELERY": "True"})
    def test_true_titlecase(self):
        self.assertTrue(_is_celery_worker())

    @patch.dict("os.environ", {"IS_RUNNING_IN_CELERY": "TRUE"})
    def test_true_uppercase(self):
        self.assertTrue(_is_celery_worker())

    @patch.dict("os.environ", {"IS_RUNNING_IN_CELERY": "false"})
    def test_false_value(self):
        self.assertFalse(_is_celery_worker())

    @patch.dict("os.environ", {"IS_RUNNING_IN_CELERY": ""})
    def test_empty_string(self):
        self.assertFalse(_is_celery_worker())

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_env_var(self):
        self.assertFalse(_is_celery_worker())

    @patch.dict("os.environ", {"IS_RUNNING_IN_CELERY": "yes"})
    def test_non_true_string(self):
        self.assertFalse(_is_celery_worker())
