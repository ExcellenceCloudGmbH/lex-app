"""The docs site's Reflex config: lex-app's look (Radix with RFDS's theme, then RFDS)."""

import reflex as rx
from rfds._vendor._npm import spec

from lex.lex_app.reflex.design import rfds_plugin, theme_plugin

config = rx.Config(
    app_name="docs_site",
    telemetry_enabled=False,
    show_built_with_reflex=False,
    default_color_mode="light",
    plugins=[theme_plugin(), rfds_plugin()],
    # As lex-app's own config: a sitemap would only advertise a site nobody hosts yet, and the
    # default-on plugin warns on every start.
    disable_plugins=[rx.plugins.SitemapPlugin],
    # RFDS's clearable text input (the search box of a lex.ui table) imports rfdsClearInput from
    # rfds_form.js, which imports react-number-format at module level; only RFDS's form and number
    # inputs declare that package, so a site without them fails to resolve the import.
    frontend_packages=[spec("react-number-format")],
)
