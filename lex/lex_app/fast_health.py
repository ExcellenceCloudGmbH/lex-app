import json
from typing import Any, Awaitable, Callable, Dict


FAST_HEALTH_PATHS = frozenset({"/health", "/health/", "/api/health", "/api/health/"})
# Readiness is intentionally a SEPARATE path from the fast liveness `/health`.
# `/health` returns 200 without touching Django/Channels/DB and is appropriate
# for the *liveness* probe (is the process alive?). `/readiness` below actually
# verifies the database is reachable, so the pod is only marked Ready (and put
# into the Service / sent WebSocket traffic) once it can really serve. Point the
# Kubernetes *readiness* probe at `/readiness` (an IaC change in the lex-instance
# chart) to close the "Ready before it can serve -> /server-not-ready" gap.
READINESS_PATHS = frozenset({"/readiness", "/readiness/", "/api/readiness", "/api/readiness/"})
_HEALTH_BODY = json.dumps({"status": "Healthy :)"}).encode("utf-8")
_HEALTH_HEADERS = [
    (b"content-type", b"application/json"),
    (b"content-length", str(len(_HEALTH_BODY)).encode("ascii")),
    (b"cache-control", b"no-store"),
]


def is_fast_health_path(path: str) -> bool:
    return path in FAST_HEALTH_PATHS


def is_readiness_path(path: str) -> bool:
    return path in READINESS_PATHS


async def _drain_http_body(
    receive: Callable[[], Awaitable[Dict[str, Any]]],
) -> None:
    while True:
        message = await receive()
        message_type = message.get("type")
        if message_type == "http.disconnect":
            return
        if message_type == "http.request" and not message.get("more_body", False):
            return


async def health_asgi_app(
    scope: Dict[str, Any],
    receive: Callable[[], Awaitable[Dict[str, Any]]],
    send: Callable[[Dict[str, Any]], Awaitable[None]],
) -> None:
    # Consume request bodies so keep-alive connections remain in a clean state.
    await _drain_http_body(receive)
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": _HEALTH_HEADERS,
        }
    )
    await send({"type": "http.response.body", "body": _HEALTH_BODY})


async def _database_ready() -> bool:
    """Return True iff the default database answers a trivial query.

    Run on the thread-sensitive executor (Django ORM is sync). Any error
    (connection refused, auth, timeout) is treated as not-ready.
    """
    from asgiref.sync import sync_to_async

    def _check() -> bool:
        from django.db import connections

        connection = connections["default"]
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True

    try:
        return await sync_to_async(_check, thread_sensitive=True)()
    except Exception:
        return False


async def readiness_asgi_app(
    scope: Dict[str, Any],
    receive: Callable[[], Awaitable[Dict[str, Any]]],
    send: Callable[[Dict[str, Any]], Awaitable[None]],
) -> None:
    """Readiness probe: 200 only when the app can actually serve (DB reachable).

    Unlike the fast `/health` liveness path, this confirms the database is up so
    the pod is not added to the Service (and sent WebSocket traffic) before it
    can serve. Returns 503 when not ready so Kubernetes withholds traffic.
    """
    await _drain_http_body(receive)
    ready = await _database_ready()
    if ready:
        status = 200
        body = json.dumps({"status": "ready"}).encode("utf-8")
    else:
        status = 503
        body = json.dumps({"status": "not-ready", "reason": "database-unavailable"}).encode(
            "utf-8"
        )
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
