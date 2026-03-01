"""
ASGI config for lex_app project.
"""

import os

# MUST be set before importing anything that may touch Django settings/apps
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")

import atexit
import logging
import signal

from django.core.asgi import get_asgi_application

# This initializes Django (calls django.setup() internally)
django_asgi_app = get_asgi_application()

# Only import Channels + your routing AFTER Django is ready
from asgiref.sync import async_to_sync
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

from lex.lex_app import routing
from lex.lex_app.fast_health import health_asgi_app, is_fast_health_path

_shutdown_logger = logging.getLogger("lex.shutdown")


# ─── Graceful-shutdown support ──────────────────────────────────────
#
# Problem: When a user presses Ctrl-C, uvicorn performs a graceful
# shutdown — it waits for in-flight HTTP requests (including running
# calculations) to finish.  The calculation completes normally and
# saves SUCCESS to the DB.  By the time the process exits the records
# are no longer IN_PROGRESS, so the startup-reset logic finds nothing
# to mark as ABORTED.
#
# Solution: We must set the ``_shutting_down`` flag on
# ActiveCalculationStateStore the INSTANT the termination signal
# arrives, so that ``execute_calculation_sync``'s finally block
# (which may still run during the graceful-shutdown window) saves
# ABORTED instead of SUCCESS.
#
# Challenge: uvicorn installs its own signal handlers AFTER loading
# the ASGI module, overwriting any handlers we set at module level.
#
# Approach: We register an ASGI **lifespan** handler.  The lifespan
# ``startup`` event fires AFTER uvicorn installs its handlers.
# At that point we wrap uvicorn's handlers with our own that set the
# flag first, then forward to uvicorn's original handler.
# ────────────────────────────────────────────────────────────────────

def _install_shutdown_signal_wrappers():
    """
    Wrap the current SIGINT / SIGTERM handlers (installed by uvicorn)
    so that we can set the shutdown flag the moment a signal arrives.
    """
    from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous = signal.getsignal(sig)

        def _wrapper(signum, frame, _prev=previous):
            _shutdown_logger.info(
                "Shutdown signal %s received – setting abort flag", signum,
            )
            ActiveCalculationStateStore.set_shutting_down()

            # Forward to the previous handler (uvicorn's handle_exit).
            if callable(_prev) and _prev not in (signal.SIG_DFL, signal.SIG_IGN):
                _prev(signum, frame)
            elif _prev == signal.SIG_DFL:
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)

        signal.signal(sig, _wrapper)

    _shutdown_logger.debug("Shutdown signal wrappers installed")


async def _lifespan(scope, receive, send):
    """Minimal ASGI lifespan handler."""
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            # Uvicorn's signal handlers are in place now — wrap them.
            _install_shutdown_signal_wrappers()
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


async def http_application(scope, receive, send):
    path = scope.get("path", "")
    if scope.get("type") == "http" and is_fast_health_path(path):
        await health_asgi_app(scope, receive, send)
        return
    await django_asgi_app(scope, receive, send)


application = ProtocolTypeRouter(
    {
        "http": http_application,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(
                URLRouter(routing.websocket_urlpatterns())
            )
        ),
        "lifespan": _lifespan,
    }
)


# ─── atexit: belt-and-suspenders DB sweep ───────────────────────────

def on_server_shutdown(*args, **kwargs):
    """atexit handler – sweep any remaining IN_PROGRESS rows."""
    # Ensure the flag is set (covers edge cases where atexit fires
    # without a signal, e.g. sys.exit()).
    try:
        from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
        ActiveCalculationStateStore.set_shutting_down()
    except Exception:
        pass

    # Mark any remaining IN_PROGRESS rows as ABORTED.
    try:
        from django.apps import apps
        from lex.core.models.CalculationModel import CalculationModel

        for model in apps.get_models():
            if not issubclass(model, CalculationModel) or model._meta.abstract:
                continue
            try:
                updated = model.objects.filter(
                    is_calculated=CalculationModel.IN_PROGRESS,
                ).update(is_calculated=CalculationModel.ABORTED)
                if updated:
                    _shutdown_logger.info(
                        "Shutdown: marked %d %s row(s) as ABORTED",
                        updated, model.__name__,
                    )
            except Exception as exc:
                _shutdown_logger.warning(
                    "Shutdown: failed to abort %s rows: %s",
                    model.__name__, exc,
                )

        ActiveCalculationStateStore.clear_all()
    except Exception as exc:
        _shutdown_logger.warning("Shutdown abort sweep failed: %s", exc, exc_info=True)

    # Disconnect all WebSocket consumers.
    try:
        from lex.api.consumers.BackendHealthConsumer import BackendHealthConsumer
        from lex.api.consumers.CalculationLogConsumer import CalculationLogConsumer
        from lex.api.consumers.CalculationsConsumer import CalculationsConsumer
        from lex.api.consumers.UpdateCalculationStatusConsumer import UpdateCalculationStatusConsumer

        async_to_sync(BackendHealthConsumer.disconnect_all)()
        async_to_sync(CalculationLogConsumer.disconnect_all)()
        async_to_sync(CalculationsConsumer.disconnect_all)()
        async_to_sync(UpdateCalculationStatusConsumer.disconnect_all)()
    except Exception as exc:
        _shutdown_logger.warning("WS consumer disconnect failed: %s", exc)


atexit.register(on_server_shutdown)
