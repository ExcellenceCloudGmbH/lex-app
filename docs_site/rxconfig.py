"""The docs site's Reflex config: lex-app's look (Radix with RFDS's theme, then RFDS)."""

import reflex as rx

from lex.lex_app.reflex.design import rfds_plugin, theme_plugin

config = rx.Config(
    app_name="docs_site",
    telemetry_enabled=False,
    show_built_with_reflex=False,
    default_color_mode="light",
    plugins=[theme_plugin(), rfds_plugin()],
)
