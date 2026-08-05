"""HTTP access to the LEX backend from inside the Streamlit host.

Deliberately action-agnostic: this is the piece a future ``lex_action()`` reuses
unchanged, so generalising the widget stays an additive module rather than a
refactor.

Never imports Django models. Every call goes over HTTP as the signed-in user, so
read permission, audit actor resolution and the ``calculate=true`` trigger path
stay identical to the React UI's. An in-process ORM call from a dashboard would
bypass all three at once.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional

import requests

#: Seconds before a backend call is abandoned. These run on the poller thread
#: rather than inside a render, so a hung backend no longer freezes a page --
#: but a call that never returns would still stall that record's watch, and a
#: dashboard whose tiles quietly stop updating is worse than one that says so.
REQUEST_TIMEOUT = 10

#: One :class:`requests.Session` per calling thread.
#:
#: A bare ``requests.get`` opens a new TCP connection -- and, against an HTTPS
#: deployment, negotiates a new TLS session -- for every single call. A page
#: watching six records pays that handshake six times per poll, which is most of
#: what a status read costs and none of what it is for. A Session keeps the
#: connection alive, so the second read onwards is one round trip.
#:
#: Thread-local because ``requests.Session`` is not thread-safe, and the poller
#: reads several records at once through a small pool.
_sessions = threading.local()


def _session() -> requests.Session:
    session = getattr(_sessions, "session", None)
    if session is None:
        session = requests.Session()
        _sessions.session = session
    return session


class LexApiError(Exception):
    """A backend call did not succeed.

    Carries the HTTP status when there was a response at all; ``None`` means the
    request never reached the server. Callers catch this one type, which is what
    keeps a transport failure from escaping into Streamlit's renderer.
    """

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def resolve_api_base_url() -> str:
    """Base URL of the backend REST API.

    Priority:
      1. ``LEX_API_URL`` -- explicit override, for a deployment that does not
         serve the API from the frontend host
      2. ``REACT_APP_URL`` / ``LEX_FRONTEND_URL`` -- the origin
         :func:`lex.lex_app.streamlit.embed._resolve_base_url` already resolves.
         Django serves the React bundle *and* ``/api/...`` from one host (see
         ``lex_app/urls.py``: the API routes are registered before the
         ``serve_react`` catch-all), so a dashboard that can embed a ``lex_view``
         can reach the API with no additional configuration -- and the two
         cannot be pointed at different hosts by accident.
      3. localhost, for local development
    """
    override = os.getenv("LEX_API_URL")
    if override:
        return override.rstrip("/")

    return (
        os.getenv("REACT_APP_URL")
        or os.getenv("LEX_FRONTEND_URL")
        or "http://localhost:8000"
    ).rstrip("/")


def _headers(token: str) -> Dict[str, str]:
    """Authenticate as the signed-in user.

    A bearer token, never the instance's ``LEX_API_KEY``: that key is a
    machine-to-machine secret (``Authorization: Api-Key ...``, see
    ``lex.api.utils.api_key_requests``) whose caller is a technical user. Using
    it here would run the calculation under the wrong actor and skip the user's
    own read permission entirely.
    """
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def get_json(path: str, token: str, params: Optional[dict] = None) -> Dict[str, Any]:
    """GET ``path`` and return parsed JSON, or raise :class:`LexApiError`."""
    url = f"{resolve_api_base_url()}{path}"
    try:
        response = _session().get(
            url, headers=_headers(token), params=params, timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise LexApiError(f"Backend unreachable: {exc}") from exc

    if response.status_code >= 400:
        raise LexApiError(_message_of(response), status=response.status_code)
    return response.json()


def patch_json(
    path: str,
    token: str,
    params: Optional[dict] = None,
    json: Optional[dict] = None,
) -> Dict[str, Any]:
    """PATCH ``path`` and return parsed JSON, or raise :class:`LexApiError`.

    An empty body is still sent as JSON: the endpoints this reaches are DRF
    partial updates, which need a parseable body even when every meaningful
    flag travels in it.
    """
    url = f"{resolve_api_base_url()}{path}"
    try:
        response = _session().patch(
            url,
            headers=_headers(token),
            params=params,
            json=json if json is not None else {},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise LexApiError(f"Backend unreachable: {exc}") from exc

    if response.status_code >= 400:
        raise LexApiError(_message_of(response), status=response.status_code)
    return response.json() if response.content else {}


def _message_of(response) -> str:
    """The backend's own explanation, or the bare status when it gave none.

    DRF reports refusals as ``{"detail": ...}``; a proxy or a 502 from the
    ingress may answer with HTML, which must not turn a failed call into a JSON
    decode error on top of the original one.
    """
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if not isinstance(payload, dict):
        return f"HTTP {response.status_code}"
    return (
        payload.get("detail")
        or payload.get("message")
        or f"HTTP {response.status_code}"
    )
