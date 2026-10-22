"""The hourly heartbeat lex-app sends from the web process.

``start()`` fires one heartbeat immediately and then one every 3600 seconds
from a daemon thread, but only in the web process: Celery workers never call
this (``running_in_uvicorn()`` gates it) and never send a heartbeat. The
payload's ``usage_enabled`` rides along either way, which is how the
Collection dashboard on the controller side tells an opted-out instance
(heartbeats, ``usage_enabled: false``) from a silent one (no heartbeats at
all).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict

from lex._version import __version__
from lex.core.services.usage_config import usage_metrics_enabled
from lex.lex_app.fleet_metrics import emitter

logger = logging.getLogger(__name__)

#: Seconds between heartbeats after the first one.
HEARTBEAT_INTERVAL_SECONDS = 3600.0

_started = False
_start_lock = threading.Lock()


def build_payload() -> Dict[str, Any]:
    """The heartbeat event body. ``process`` is always "web"."""
    return {
        "type": "heartbeat",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "lex_app_version": __version__,
        "commit_sha": os.getenv("COMMIT_SHA", ""),
        "usage_enabled": usage_metrics_enabled(),
        "process": "web",
    }


def start() -> bool:
    """Schedule the heartbeat: once now, then every hour, from a daemon thread.

    A no-op outside the web process or when the emitter is not :func:`enabled
    <lex.lex_app.fleet_metrics.emitter.enabled>` -- Celery workers never reach
    this at all in practice, since nothing calls ``start()`` there, but the
    guard keeps the contract true even if that ever changes. Returns whether a
    thread was scheduled (also False on the second call in a process -- one
    daemon thread per process, like the emitter's drain thread).
    """
    # Imported here, not at module scope: lex.lex_app.apps imports this module
    # from ready() to call start(), so an eager top-level import the other way
    # would be a circular import.
    from lex.lex_app.apps import running_in_uvicorn

    global _started

    if not running_in_uvicorn() or not emitter.enabled():
        return False

    with _start_lock:
        if _started:
            return False
        _started = True

    thread = threading.Thread(target=_run, name="fleet-metrics-heartbeat", daemon=True)
    thread.start()
    return True


def _run() -> None:
    """The daemon thread body: beat now, then every HEARTBEAT_INTERVAL_SECONDS."""
    while True:
        with emitter.guard("heartbeat.emit"):
            emitter.emit(build_payload())
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


def _reset_for_tests() -> None:
    """Reset the once-per-process guard so tests can exercise start() again."""
    global _started
    with _start_lock:
        _started = False
