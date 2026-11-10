"""The header-line limit the ``websockets`` library parses with.

``websockets`` reads its parser limits from the environment **once, when
``websockets.http11`` is imported**, and refuses any request line or header line
longer than ``WEBSOCKETS_MAX_LINE_LENGTH`` (8192 bytes by default). The Streamlit
proxy forwards the user's Keycloak access token to Streamlit on the handshake, so
one header line is as long as the token -- and in LEX-894 a user with access to
many instances carried an ~8.3 KB token, which is past that limit.

What made it an outage rather than an error was the shape of the failure: the
uvicorn ``websockets-sansio`` handler neither answered the handshake nor closed
the connection when parsing blew up, so the browser sat on an open socket and
the dashboard never loaded, with nothing in the logs naming the cause.

LEX-898 removes the cause by scoping instance tokens (the Keycloak client no
longer runs with full scope, so a token carries only that instance's roles).
This module is the safety net under it: a ceiling high enough that no plausible
token can reach it, applied before anything can import ``websockets``.

``setdefault``, so ``WEBSOCKETS_MAX_LINE_LENGTH`` in a deployment's environment
still wins -- including the custom env that was set on lex-1483 to get it back
up, which can be removed without this changing under it.

Importing this module applies the default. That is the point: it has to happen
before the import of ``websockets`` it protects, and an import is the one thing
that cannot be accidentally reordered after it.
"""

from __future__ import annotations

import os
from typing import Optional

#: Name of the environment variable ``websockets.http11`` reads at import time.
WEBSOCKETS_MAX_LINE_LENGTH_ENV = "WEBSOCKETS_MAX_LINE_LENGTH"

#: Where the library keeps the limit it ended up with, newest name first.
#: Renamed in websockets 13 -- the release that also started reading the
#: environment variable -- so an installation carrying only ``MAX_LINE`` is by
#: definition one that ignores it. Looking for both is what tells the two apart;
#: looking for either alone would make the check a silent no-op on half the
#: versions it exists to judge.
WEBSOCKETS_LIMIT_ATTRIBUTES = ("MAX_LINE_LENGTH", "MAX_LINE")

#: 64 KiB. Eight times the library's own default and four times the largest
#: token seen in production, so a header line cannot be what breaks a handshake.
#: Larger than the ingress's 16 KB line limit on purpose: a request that an
#: ingress would reject never reaches this parser, and a deployment without one
#: should fail on something it can report rather than hang.
DEFAULT_MAX_LINE_LENGTH = "65536"


def apply_websocket_header_limits() -> None:
    """Install the default limit, leaving an operator's own value alone.

    Safe to call more than once and from any process; it only ever writes a key
    that is not already set.
    """
    os.environ.setdefault(WEBSOCKETS_MAX_LINE_LENGTH_ENV, DEFAULT_MAX_LINE_LENGTH)


def websockets_line_limit_shortfall() -> Optional[str]:
    """One sentence naming the gap between what we asked for and what is in force.

    A limit that nothing checks is not a limit. ``WEBSOCKETS_MAX_LINE_LENGTH``
    only exists from websockets 13; an older release parses with a hard-coded
    8192 and reports nothing, so the environment can say 65536 while the handshake
    still dies at 8 KB -- the exact failure this module exists to prevent, back
    again and just as silent. Comparing what the library ended up with against
    what we set catches that, and anything else that overrode it.

    Returns ``None`` when the limit is in force, or when there is no websockets
    to ask (a deployment that never opens one has nothing to warn about).
    """
    try:
        from websockets import http11  # imported lazily: this is a diagnostic
    except Exception:
        return None

    effective = next(
        (getattr(http11, name) for name in WEBSOCKETS_LIMIT_ATTRIBUTES if hasattr(http11, name)),
        None,
    )
    configured = os.environ.get(WEBSOCKETS_MAX_LINE_LENGTH_ENV)
    if effective is None or configured is None:
        return None

    try:
        effective_bytes = int(effective)
        configured_bytes = int(configured)
    except (TypeError, ValueError):
        return None

    if effective_bytes >= configured_bytes:
        return None

    return (
        f"websockets parses header lines up to {effective_bytes} bytes, not the "
        f"{configured_bytes} this process asked for via "
        f"{WEBSOCKETS_MAX_LINE_LENGTH_ENV} -- the variable is only read from "
        "websockets 13. A request whose cookie or access token is longer than "
        f"{effective_bytes} bytes will hang the WebSocket handshake with nothing "
        "in the log. Install websockets>=13."
    )


apply_websocket_header_limits()
