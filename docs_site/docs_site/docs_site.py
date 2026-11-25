"""The docs app: lex-app's look, no sign-in, every page from nav.py."""

# Django first, as lex/reflex_app.py does: `reflex run` processes never ran the lex CLI's bootstrap,
# and a guarded @ui.action enters lex-app's acting-user context, which imports Django models.
from lex.tools.django_bootstrap import setup_django

setup_django()

import reflex as rx  # noqa: E402 - Django must be ready first
import rfds  # noqa: E402
from rfds.platform import install_platform  # noqa: E402

from docs_site import layout, llms, sandbox
from lex.lex_app.reflex.ui.pages import declare
from lex.lex_app.reflex.design import lex_head_components, lex_security_headers


sandbox.install()
app = rx.App(**rfds.app_kwargs(html_lang="en",
                                extra_head=[*lex_head_components(),
                                            rx.el.link(rel="stylesheet", href=layout.MONO_STYLESHEET)],
                                extra_routes=llms.routes()))
install_platform(app, headers=lex_security_headers())
rfds.install(app, tenant=None, auth=None)
rfds.toast  # noqa: B018 - lazy attribute; loading it defines states, which must exist before any event
layout.add_pages(app)
declare.install(app)  # lex.ui's part of an app: /ui-error, the jobs' clock, the route check (as create_app does)
