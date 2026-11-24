"""The docs app: lex-app's look, no sign-in, every page from nav.py."""

import reflex as rx
import rfds
from rfds.platform import install_platform

from lex import ui
from lex.lex_app.reflex.design import lex_head_components, lex_security_headers


def _home() -> rx.Component:
    return rfds.page(rfds.page_header(title="lex-app dashboards", path="/"))


app = rx.App(**rfds.app_kwargs(html_lang="en", extra_head=lex_head_components()))
install_platform(app, headers=lex_security_headers())
rfds.install(app, tenant=None, auth=None)
rfds.toast  # noqa: B018 - lazy attribute; loading it defines states, which must exist before any event
app.add_page(_home, route="/", title="lex-app dashboards")
ui.install(app)
