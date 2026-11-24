"""The docs app: lex-app's look, no sign-in, every page from nav.py."""

import reflex as rx
import rfds
from rfds.platform import install_platform

from docs_site import layout, llms, sandbox
from lex import ui
from lex.lex_app.reflex.design import lex_head_components, lex_security_headers


sandbox.install()
app = rx.App(**rfds.app_kwargs(html_lang="en", extra_head=lex_head_components(),
                                extra_routes=llms.routes()))
install_platform(app, headers=lex_security_headers())
rfds.install(app, tenant=None, auth=None)
rfds.toast  # noqa: B018 - lazy attribute; loading it defines states, which must exist before any event
layout.add_pages(app)
ui.install(app)
