"""The Streamlit sidebar's chrome, in lex-app's visual language.

Scope, deliberately narrow: this owns the CONTAINER -- a brand lockup, who is
signed in, the hairlines between them, and the log-out row. It renders nothing
navigational. Whatever ``streamlit_structure.main()`` puts in the sidebar is the
author's, and is neither styled nor reordered here.

That boundary is also what keeps this robust. Every element below is markup we
emit ourselves with inline styles, so nothing depends on Streamlit's own DOM:
no ``[data-testid=...]`` selectors, no class names that a Streamlit upgrade can
rename underneath us. The one thing we do not attempt is true bottom-pinning,
which would need exactly those selectors -- the log-out row is last in call
order, which puts it at the bottom of the sidebar without touching Streamlit's
layout at all.

Colours come from the vendored design tokens rather than being retyped, so the
sidebar cannot drift from the product it is imitating. The surface is navy in
BOTH light and dark modes, matching lex-app, where the sidenav is deliberately
mode-invariant.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Optional

from lex.lex_app.design_system import lex_tokens

#: The same logo lex-app's sidenav renders (`assets/images/dark-lex-logo.svg`
#: there, imported by CustomSidebar). The DARK variant -- white wordmark, teal
#: accent -- because this surface is navy in both modes.
#:
#: Vendored into ``lex/assets/``, which is already declared as package data, so
#: it ships in the wheel rather than being fetched from the frontend build at
#: runtime: those filenames carry content hashes and change on every rebuild.
_LOGO_PATH = Path(__file__).resolve().parents[2] / "assets" / "dark-lex-logo.svg"

#: Rendered width, matching what lex-app's sidebar uses.
_LOGO_WIDTH_PX = 140

#: Sidebar palette, matching lex-app's `NAV` constants in CustomSidebar.tsx.
#: Derived from the vendored tokens where one exists, so the two cannot drift.
NAV_SURFACE = lex_tokens.NAVY
NAV_ACCENT = lex_tokens.TEAL
NAV_TEXT = "#dfe7ee"
NAV_DIM = "rgba(223,231,238,0.6)"
NAV_HOVER = "rgba(255,255,255,0.06)"
NAV_BORDER = "rgba(255,255,255,0.10)"
NAV_ACTIVE_BG = "rgba(20,180,180,0.16)"


def logo_is_available() -> bool:
    """Whether the vendored logo is on disk.

    Guarded because ``st.logo`` raises on a missing file, and a packaging
    mistake should cost the logo rather than the page.
    """
    return _LOGO_PATH.is_file()


def render_logo(st_module) -> None:
    """Put the logo in Streamlit's own logo slot.

    ``st.logo`` renders into the header slot Streamlit reserves for exactly
    this -- top of the sidebar, on the same line as the collapse control, which
    is where lex-app puts its own logo too.

    Hand-placing an ``<img>`` in the sidebar's user content is what made it look
    misplaced: that content area begins *below* the header, so the image landed
    under the collapse button with the header's whitespace above it, and no
    amount of negative margin fixes that honestly.

    Known limit, worth stating: ``st.logo`` also renders in the app's upper-left
    corner when the sidebar is COLLAPSED, and that surface follows the page
    theme. This is the dark variant -- a white wordmark -- so on a light page
    with the sidebar collapsed it will be hard to see. The sidebar is navy in
    both modes, so the common case is right; fixing the other one means choosing
    a variant from the client's theme, which is not knowable here.
    """
    if not logo_is_available():
        return
    st_module.logo(str(_LOGO_PATH), size="medium")


def _initials(name: str) -> str:
    """Up to two initials, for the avatar.

    Falls back to the first character of whatever we were given rather than
    rendering an empty circle: an identity block that shows nothing is worse
    than one showing a single letter.
    """
    parts = [p for p in name.replace(".", " ").replace("_", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _display_name(session_state) -> str:
    """The best name we have, in descending order of how human it reads."""
    info = session_state.get("user_info") or {}
    for candidate in (
        info.get("name"),
        info.get("preferred_username"),
        session_state.get("user_username"),
        info.get("email"),
        session_state.get("user_email"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "Signed in"


def identity_html(name: str, subtitle: Optional[str] = None) -> str:
    """The brand lockup and the signed-in user, as one block of markup.

    Identity sits in the sidebar rather than a top bar -- the reverse of
    lex-app, and on purpose. lex-app puts the user menu top-right because its
    sidenav is already full of navigation; a Streamlit page has no top bar of
    ours to use, and its sidebar is mostly empty, so this is where the
    information belongs.

    Every value is escaped: a display name arrives from the identity provider,
    which is not a place to trust markup from.
    """
    safe_name = html.escape(name)
    safe_initials = html.escape(_initials(name))
    safe_subtitle = html.escape(subtitle) if subtitle else ""

    subtitle_markup = (
        f'<div style="color:{NAV_DIM};font-size:11px;line-height:1.4;">{safe_subtitle}</div>'
        if safe_subtitle
        else ""
    )

    return f"""
<div style="margin:-0.25rem -0.25rem 0;">
  <div style="display:flex;align-items:center;gap:10px;padding:2px 6px 12px;">
    <div style="width:34px;height:34px;border-radius:50%;background:{NAV_ACTIVE_BG};
                color:{NAV_ACCENT};display:flex;align-items:center;justify-content:center;
                font-size:13px;font-weight:500;flex:0 0 34px;">{safe_initials}</div>
    <div style="min-width:0;">
      <div style="color:{NAV_TEXT};font-size:13px;font-weight:500;line-height:1.4;
                  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{safe_name}</div>
      {subtitle_markup}
    </div>
  </div>
  <div style="height:1px;background:{NAV_BORDER};margin-bottom:2px;"></div>
</div>
""".strip()


def logout_row_html(href: str) -> str:
    """The log-out control, shaped like a navigation row rather than a link.

    It was a teal link floating in an otherwise empty sidebar. Teal is this
    palette's accent, which reads as "primary action" -- and logging out is not
    one. As a dim row with an icon and a hover fill it matches the weight of
    everything around it.

    ``target="_top"`` because the sign-out is a full navigation: inside a frame
    it would otherwise replace the widget rather than the page.
    """
    return f"""
<div style="margin:0 -0.25rem;">
  <div style="height:1px;background:{NAV_BORDER};margin-bottom:6px;"></div>
  <a href="{html.escape(href, quote=True)}" target="_top"
     style="display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:6px;
            color:{NAV_DIM};font-size:13px;text-decoration:none;"
     onmouseover="this.style.background='{NAV_HOVER}';this.style.color='{NAV_TEXT}';"
     onmouseout="this.style.background='transparent';this.style.color='{NAV_DIM}';">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
      <polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>
    </svg>
    <span>Log out</span>
  </a>
</div>
""".strip()


def render_identity(st_module, session_state) -> None:
    """Render the lockup and identity at the TOP of the sidebar.

    Call order is what places it: Streamlit lays the sidebar out in the order
    blocks are created, so this has to run before ``main()``. Nothing here
    reserves space or repositions anything -- the author's own sidebar content
    simply follows.
    """
    render_logo(st_module)
    st_module.sidebar.markdown(
        identity_html(_display_name(session_state), subtitle=_role_subtitle(session_state)),
        unsafe_allow_html=True,
    )


def _role_subtitle(session_state) -> Optional[str]:
    """A second line under the name, when we have something worth showing.

    The email, unless the name already IS the email -- repeating it twice in
    two type sizes looks like a rendering bug rather than information.
    """
    info = session_state.get("user_info") or {}
    email = info.get("email") or session_state.get("user_email") or ""
    if not isinstance(email, str) or not email.strip():
        return None
    email = email.strip()
    return None if email == _display_name(session_state) else email
