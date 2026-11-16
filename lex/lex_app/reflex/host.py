"""Components that connect a Reflex dashboard to lex-app's shell around it.

* :func:`theme_follower` -- the dashboard takes lex-app's light/dark mode, from
  ``?lex_theme=`` and then from the shell's ``theme`` messages (decision D9).
* :func:`auth_notice` -- tells the shell, in the lex-auth v2 contract, that the
  frame needs a click to sign in (``required``) and that it is back
  (``restored``), so the shell can offer the way back instead of a dead frame.

Both render nothing. The browser side is ``_js/lex_host.jsx``; the classes are
built on first use, because declaring their asset reads the Reflex
configuration, which does not exist while ``rxconfig.py`` loads.
"""

from __future__ import annotations

from functools import cache
from typing import Literal


@cache
def _components() -> dict[str, type]:
    import reflex as rx
    from reflex_base.components.component import field
    from reflex_base.vars.base import Var

    from lex.lex_app.reflex._js import asset_import

    class LexThemeFollower(rx.Component):
        """Applies lex-app's light/dark mode inside a framed dashboard (renders nothing)."""

        library = asset_import("lex_host.jsx")
        tag = "LexThemeFollower"
        allowed_origins: Var[list[str]] = field(doc="Origins whose theme messages are obeyed.")

    class LexAuthNotice(rx.Component):
        """Posts a lex-auth v2 ``required`` / ``restored`` message to the shell (renders nothing)."""

        library = asset_import("lex_host.jsx")
        tag = "LexAuthNotice"
        event: Var[str] = field(doc='"required" or "restored".')

    return {"theme": LexThemeFollower, "auth": LexAuthNotice}


def theme_follower(allowed_origins: list[str] | None = None):
    """The component that makes the dashboard follow lex-app's colour mode."""
    from lex.lex_app.reflex.design import lex_app_origins

    origins = lex_app_origins() if allowed_origins is None else list(allowed_origins)
    return _components()["theme"].create(allowed_origins=origins)


def auth_notice(event: Literal["required", "restored"]):
    """The component that tells lex-app's shell the frame's sign-in state changed."""
    if event not in ("required", "restored"):
        raise ValueError(f"auth_notice() takes 'required' or 'restored', got {event!r}")
    return _components()["auth"].create(event=event)
