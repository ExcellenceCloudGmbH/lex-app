"""
Tests for ``WebSocketNotifier`` — the WebSocket notification layer for
the calculation log system.

The ``WebSocketNotifier`` is called by ``OneModelEntry.update()`` every
time a calculation is triggered, and by the Celery callback pipeline on
completion. It must **never** raise — a broken WebSocket channel must not
prevent calculations from running. This graceful-degradation contract is
the primary thing these tests verify.

Coverage targets:
    1. ``send_calculation_update`` — success, no channel layer, exception
    2. ``send_calculation_notification`` — success, custom group, failure
    3. ``is_websocket_available`` — available, unavailable, exception

All tests use ``SimpleTestCase`` with mocked channel layer — no Redis or
ASGI server needed.

How to run::

    lex test lex.audit_logging.tests.test_websocket_notifier \\
        --verbosity=2 --noinput
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings"
)
if not apps.ready:
    django.setup()

from django.test import SimpleTestCase

from lex.audit_logging.utils.WebSocketNotifier import WebSocketNotifier


# ═══════════════════════════════════════════════════════════════════════════
#  1. send_calculation_update
# ═══════════════════════════════════════════════════════════════════════════

class SendCalculationUpdateTests(SimpleTestCase):
    """
    ``send_calculation_update`` sends a message to the 'calculations' group
    and returns True on success. It must degrade gracefully when the channel
    layer is unavailable or throws.
    """

    @patch("lex.audit_logging.utils.WebSocketNotifier.sync_channel_group_send")
    @patch("lex.audit_logging.utils.WebSocketNotifier.get_channel_layer")
    def test_sends_message_and_returns_true(self, mock_get_layer, mock_send):
        """Happy path: channel layer available, send succeeds."""
        mock_get_layer.return_value = MagicMock()  # truthy channel layer

        result = WebSocketNotifier.send_calculation_update(
            calculation_record="investorcashflow_42",
            calculation_id="calc-uuid-123",
        )

        self.assertTrue(result)
        mock_send.assert_called_once()
        # Verify it sends to the 'calculations' group
        args = mock_send.call_args
        self.assertEqual(args[0][0], "calculations")
        # Verify message structure
        message = args[0][1]
        self.assertEqual(message["type"], "calculation_id")
        self.assertEqual(message["payload"]["calculation_record"], "investorcashflow_42")
        self.assertEqual(message["payload"]["calculation_id"], "calc-uuid-123")

    @patch("lex.audit_logging.utils.WebSocketNotifier.get_channel_layer")
    def test_no_channel_layer_returns_false(self, mock_get_layer):
        """When no channel layer is configured (e.g., local dev), return False."""
        mock_get_layer.return_value = None

        result = WebSocketNotifier.send_calculation_update(
            calculation_record="model_1",
            calculation_id="calc-1",
        )

        self.assertFalse(result)

    @patch("lex.audit_logging.utils.WebSocketNotifier.sync_channel_group_send")
    @patch("lex.audit_logging.utils.WebSocketNotifier.get_channel_layer")
    def test_send_exception_returns_false_not_raises(self, mock_get_layer, mock_send):
        """If the channel send raises (Redis down, etc.), we return False — never raise."""
        mock_get_layer.return_value = MagicMock()
        mock_send.side_effect = ConnectionError("Redis connection refused")

        result = WebSocketNotifier.send_calculation_update(
            calculation_record="model_1",
            calculation_id="calc-1",
        )

        self.assertFalse(result)

    @patch("lex.audit_logging.utils.WebSocketNotifier.sync_channel_group_send")
    @patch("lex.audit_logging.utils.WebSocketNotifier.get_channel_layer")
    def test_get_channel_layer_exception_returns_false(self, mock_get_layer, mock_send):
        """Even ``get_channel_layer`` itself might throw — must be caught."""
        mock_get_layer.side_effect = RuntimeError("channels not installed")

        result = WebSocketNotifier.send_calculation_update(
            calculation_record="model_1",
            calculation_id="calc-1",
        )

        self.assertFalse(result)
        mock_send.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
#  2. send_calculation_notification
# ═══════════════════════════════════════════════════════════════════════════

class SendCalculationNotificationTests(SimpleTestCase):
    """
    ``send_calculation_notification`` sends a general notification to any
    WebSocket group. Used for status broadcasts (IN_PROGRESS → CALCULATED).
    """

    @patch("lex.audit_logging.utils.WebSocketNotifier.sync_channel_group_send")
    @patch("lex.audit_logging.utils.WebSocketNotifier.get_channel_layer")
    def test_sends_to_default_group(self, mock_get_layer, mock_send):
        """Default group is 'calculations'."""
        mock_get_layer.return_value = MagicMock()

        payload = {"status": "CALCULATED", "record_id": "42"}
        result = WebSocketNotifier.send_calculation_notification(payload)

        self.assertTrue(result)
        args = mock_send.call_args
        self.assertEqual(args[0][0], "calculations")
        self.assertEqual(args[0][1]["type"], "calculation_notification")
        self.assertEqual(args[0][1]["payload"], payload)

    @patch("lex.audit_logging.utils.WebSocketNotifier.sync_channel_group_send")
    @patch("lex.audit_logging.utils.WebSocketNotifier.get_channel_layer")
    def test_sends_to_custom_group(self, mock_get_layer, mock_send):
        """Callers can specify a different group for targeted notifications."""
        mock_get_layer.return_value = MagicMock()

        payload = {"event": "batch_complete"}
        result = WebSocketNotifier.send_calculation_notification(payload, group="batch_updates")

        self.assertTrue(result)
        args = mock_send.call_args
        self.assertEqual(args[0][0], "batch_updates")

    @patch("lex.audit_logging.utils.WebSocketNotifier.get_channel_layer")
    def test_no_channel_layer_returns_false(self, mock_get_layer):
        mock_get_layer.return_value = None

        result = WebSocketNotifier.send_calculation_notification({"x": 1})

        self.assertFalse(result)

    @patch("lex.audit_logging.utils.WebSocketNotifier.sync_channel_group_send")
    @patch("lex.audit_logging.utils.WebSocketNotifier.get_channel_layer")
    def test_exception_returns_false(self, mock_get_layer, mock_send):
        """Graceful degradation on send failure."""
        mock_get_layer.return_value = MagicMock()
        mock_send.side_effect = OSError("network unreachable")

        result = WebSocketNotifier.send_calculation_notification({"x": 1})

        self.assertFalse(result)


# ═══════════════════════════════════════════════════════════════════════════
#  3. is_websocket_available
# ═══════════════════════════════════════════════════════════════════════════

class IsWebsocketAvailableTests(SimpleTestCase):
    """
    ``is_websocket_available`` is a health-check used by monitoring
    dashboards and the Streamlit integration to decide whether to
    subscribe to real-time updates.
    """

    @patch("lex.audit_logging.utils.WebSocketNotifier.get_channel_layer")
    def test_returns_true_when_layer_exists(self, mock_get_layer):
        mock_get_layer.return_value = MagicMock()
        self.assertTrue(WebSocketNotifier.is_websocket_available())

    @patch("lex.audit_logging.utils.WebSocketNotifier.get_channel_layer")
    def test_returns_false_when_no_layer(self, mock_get_layer):
        mock_get_layer.return_value = None
        self.assertFalse(WebSocketNotifier.is_websocket_available())

    @patch("lex.audit_logging.utils.WebSocketNotifier.get_channel_layer")
    def test_returns_false_on_exception(self, mock_get_layer):
        """If channels isn't installed or misconfigured, return False."""
        mock_get_layer.side_effect = ImportError("channels not installed")
        self.assertFalse(WebSocketNotifier.is_websocket_available())
