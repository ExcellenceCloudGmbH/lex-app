"""
Synchronous helper for sending messages to Django Channels groups.

In Celery workers (and other sync contexts without an ASGI event loop),
the standard pattern of ``async_to_sync(channel_layer.group_send)(...)``
creates a **new event loop** (and socket pair) on every call.  During long
calculations with many log messages this exhausts the OS file-descriptor
limit (``OSError: [Errno 24] Too many open files``).

This module solves the problem by choosing the right strategy based on
the runtime context:

* **ASGI server** (non-Celery) — uses ``async_to_sync`` directly, which
  integrates safely with the running ASGI event loop and the
  ``InMemoryChannelLayer`` (whose internal ``asyncio.Queue`` objects are
  **not** thread-safe).

* **Celery worker** — maintains a **single background thread** with a
  persistent event loop.  All ``group_send`` calls are dispatched to that
  loop via ``asyncio.run_coroutine_threadsafe``, so only one event loop
  (and one set of file descriptors) is ever created per worker process.
"""

import asyncio
import logging
import os
import threading

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

# Timeout (seconds) for waiting on the background loop to start and for
# individual group_send operations.
_SEND_TIMEOUT = 10


def _is_celery_worker() -> bool:
    """Return True when the current process is a Celery worker."""
    return os.getenv("IS_RUNNING_IN_CELERY", "").lower() == "true"


class _ChannelLayerSender:
    """
    Singleton that owns a daemon thread running a persistent event loop.
    All channel-layer sends from Celery workers go through here.
    """

    _instance = None
    _init_lock = threading.Lock()

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    # -- singleton access --------------------------------------------------

    @classmethod
    def get(cls) -> "_ChannelLayerSender":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # -- public API --------------------------------------------------------

    def group_send(self, group: str, message: dict) -> None:
        """Send *message* to *group* on the default channel layer."""
        channel_layer = get_channel_layer()
        if channel_layer is None:
            logger.debug("No channel layer configured — skipping group_send")
            return

        self._ensure_loop()

        future = asyncio.run_coroutine_threadsafe(
            channel_layer.group_send(group, message),
            self._loop,
        )
        try:
            future.result(timeout=_SEND_TIMEOUT)
        except Exception:
            # Never let a WebSocket notification failure crash a calculation.
            logger.warning(
                "channel_layer.group_send(%s, …) failed", group, exc_info=True
            )

    # -- internals ---------------------------------------------------------

    def _ensure_loop(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._ready.clear()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="channel-layer-sender",
        )
        self._thread.start()
        if not self._ready.wait(timeout=_SEND_TIMEOUT):
            logger.error("Background event-loop thread did not start in time")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()


# ── public function ──────────────────────────────────────────────────────

def sync_channel_group_send(group: str, message: dict) -> None:
    """
    Send *message* to a Django Channels *group* from synchronous code.

    Safe to call from the ASGI server, Celery workers, management commands,
    signal handlers, or any other non-async context.

    Strategy:
    - **Celery workers** use a persistent background event loop to avoid
      creating (and leaking) a new event loop + socket pair on every call.
    - **Everything else** (ASGI server) uses ``async_to_sync`` which
      correctly integrates with the running event loop and keeps
      ``InMemoryChannelLayer``'s non-thread-safe queues on a single loop.
    """
    if _is_celery_worker():
        _ChannelLayerSender.get().group_send(group, message)
    else:
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        try:
            async_to_sync(channel_layer.group_send)(group, message)
        except Exception:
            logger.warning(
                "channel_layer.group_send(%s, …) failed", group, exc_info=True
            )
