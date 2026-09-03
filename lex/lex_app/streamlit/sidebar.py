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

_ASSETS = Path(__file__).resolve().parents[2] / "assets"

#: For the SIDEBAR, which is brand-navy in both light and dark. The dark variant
#: -- white wordmark, teal accent -- the same file lex-app's own sidenav imports.
#:
#: Vendored into ``lex/assets/``, already declared as package data, so it ships
#: in the wheel rather than being fetched from the frontend build at runtime:
#: those filenames carry content hashes and change on every rebuild.
_LOGO_PATH = _ASSETS / "dark-lex-logo.svg"

#: For the APP'S UPPER-LEFT, which is where ``st.logo`` puts the mark when the
#: sidebar is COLLAPSED -- and that surface follows the page theme rather than
#: being navy. The light variant: teal accent, navy wordmark (#282f63).
#:
#: Two files rather than one adaptive SVG on purpose. An SVG can switch fills on
#: ``prefers-color-scheme``, but that follows the OPERATING SYSTEM, and both
#: products deliberately default to light regardless of it -- so a dark-OS user
#: on a light page would get a white wordmark on white. Matching the product
#: default is right where matching the OS is not.
_LOGO_COLLAPSED_PATH = _ASSETS / "lex-logo.svg"

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
    """Whether the sidebar logo is on disk.

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

    Two variants, because the two slots have different backgrounds. The sidebar
    is brand-navy in either mode, so it takes the white wordmark. The upper-left
    corner -- where ``st.logo`` moves the mark when the sidebar is COLLAPSED --
    follows the page theme, so it takes the navy one. Passing only the dark file
    is what made the collapsed logo nearly invisible: white on a light page.

    Residual case, stated rather than implied: collapsed AND the page switched to
    dark gives a navy wordmark on a dark surface. ``st.logo`` takes one image per
    slot, and choosing between them would mean reading a client-side theme from
    Python. Matching the product default -- light, on both surfaces -- is the
    honest half to get right.
    """
    if not logo_is_available():
        return
    kwargs = {"size": "large"}
    if _LOGO_COLLAPSED_PATH.is_file():
        kwargs["icon_image"] = str(_LOGO_COLLAPSED_PATH)
    st_module.logo(str(_LOGO_PATH), **kwargs)


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


#: Pins our account block to the bottom of the sidebar's content area.
#:
#: This is the one place that touches a Streamlit selector, and it is a
#: ``data-testid`` rather than a generated class -- those are Streamlit's own
#: testing surface and have been stable for years, where an emotion class is
#: regenerated on every build.
#:
#: It degrades to nothing. If the selector ever stops matching, the block simply
#: sits in normal flow directly under the navigation, which is where it would be
#: without this rule at all. So the failure mode is "not pinned", not "broken" --
#: which is the only reason reaching for a selector is acceptable here.
#: Left inset of Streamlit's main content column in ``layout="wide"``.
#:
#: The one number here worth being able to change in a hurry. When the sidebar is
#: COLLAPSED the logo moves into the app header, where it sat hard against the
#: left edge while the page's own text began a good deal further in -- so the two
#: did not line up. Matching this inset is what puts the logo on the same line as
#: the content beneath it.
_CONTENT_INSET = "3.5rem"

_CHROME_CSS = f"""
<style>
  /* ── Account block to the foot of the panel ─────────────────────────────
     Call order alone cannot do this: our block renders after the navigation,
     so it sits directly under it with the empty panel below. Pushing it down
     needs `margin-top: auto`, and that needs two things to be true.

     First, an unbroken flex column from the panel to the block. Streamlit's
     sidebar is
       stSidebarContent > [stSidebarHeader] [stSidebarNav] [stSidebarUserContent]
     and the user content wraps every element again:
       stSidebarUserContent > ... > stVerticalBlock > <element> > .stMarkdown
                            > [data-testid=stMarkdownContainer] > OUR div

     Second, room to push into. `min-height: 100%` rather than a fixed
     `calc(100vh - <header + nav>)`: the navigation's height depends on how many
     pages the author declared, so any constant is wrong for every app but one --
     too small and the block floats mid-panel, too large and the sidebar grows a
     scrollbar. `min-height` also grows rather than clips when the content is
     genuinely taller than the panel. */
  section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {{
    display: flex;
    flex-direction: column;
    min-height: 100%;
  }}
  section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"],
  section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div,
  section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
  }}
  /* `:has(div[...])` is a DESCENDANT match on purpose. `> div[data-lex-account]`
     matched the innermost wrapper Streamlit puts around our markup -- which is
     not a flex child of the column, so `auto` had nothing to consume and the
     block simply stayed where it was. Two depths are listed because Streamlit
     wraps a lone markdown block differently from one among several. */
  section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]
    > div:has(div[data-lex-account]),
  section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"]
    > div:has(div[data-lex-account]) {{
    margin-top: auto;
    flex: 0 0 auto;
  }}

  /* Sidebar logo: line it up with the navigation beneath it, which is indented
     from the panel edge. Left alone it sat further left than everything it
     heads, which is what read as "not aligned". */
  section[data-testid="stSidebar"] [data-testid="stLogo"] {{
    margin-left: 0.5rem;
    margin-bottom: 0.25rem;
  }}

  /* Collapsed logo, in the app header: line it up with the content column
     instead of the window edge. */
  header [data-testid="stLogo"] {{
    margin-left: {_CONTENT_INSET};
  }}
</style>
"""

#: Kept under its old name because the test-plan scenario refers to it, and
#: because the bottom-pin is still the part that earns the selector dependency.
_PIN_TO_BOTTOM_CSS = _CHROME_CSS


def render_account(st_module, session_state, logout_href: Optional[str] = None) -> None:
    """Render who is signed in, and the way out, at the BOTTOM of the sidebar.

    Together and last, which is both an ordering and a grouping decision. The
    two belong to each other -- an account block -- and Streamlit lays the
    sidebar out in call order, so rendering them after ``main()`` puts them
    below whatever navigation the app declared.

    The logo is NOT here. It goes through ``st.logo`` into the header slot at
    the very top, above even Streamlit's page navigation:

        stSidebarContent -> [ stSidebarHeader (logo, collapse) ]
                            [ stSidebarNav ]
                            [ stSidebarUserContent  <- this ]
    """
    st_module.sidebar.markdown(_PIN_TO_BOTTOM_CSS, unsafe_allow_html=True)
    st_module.sidebar.markdown(
        f'<div data-lex-account="1">'
        f"{identity_html(_display_name(session_state), subtitle=_role_subtitle(session_state))}"
        f'{logout_row_html(logout_href) if logout_href else ""}'
        f"</div>",
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
