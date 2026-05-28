import json
from typing import Any, Awaitable, Callable, Dict


FAST_HEALTH_PATHS = frozenset({"/health", "/health/", "/api/health", "/api/health/"})
_HEALTH_BODY = json.dumps({"status": "Healthy :)"}).encode("utf-8")
_HEALTH_HEADERS = [
    (b"content-type", b"application/json"),
    (b"content-length", str(len(_HEALTH_BODY)).encode("ascii")),
    (b"cache-control", b"no-store"),
]


def is_fast_health_path(path: str) -> bool:
    return path in FAST_HEALTH_PATHS


def match_health_request_path(path: str) -> bool:
    """Return True if ``path`` is a recognised health-check endpoint.

    Like :func:`is_fast_health_path` but tolerates an optional query
    string. Probes from Kubernetes, GCP uptime checks, and external
    monitoring services often append a query suffix (``?source=k8s``,
    ``?check=ready``) to differentiate themselves in access logs; the
    strict matcher rejects those even though the underlying path is a
    valid health endpoint. This variant strips the query before
    comparing against ``FAST_HEALTH_PATHS``.

    Examples:
        >>> match_health_request_path("/health")
        True
        >>> match_health_request_path("/health?source=k8s")
        True
        >>> match_health_request_path("/api/health/?check=ready")
        True
        >>> match_health_request_path("/users")
        False
    """
    if "?" in path:
        path = path.split("?", 1)[0]
    return path in FAST_HEALTH_PATHS


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
