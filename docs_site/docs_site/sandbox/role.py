"""The "Viewing as" switch on a demo card: the tab's role, Editor or Viewer."""

import reflex as rx
import rfds

from docs_site.sandbox.store import STORE


class RoleState(rx.State):
    role: str = "editor"

    @rx.event
    def set_role(self, value: str | list[str]):
        if isinstance(value, list):
            value = value[0] if value else ""
        if value not in ("editor", "viewer"):
            return
        self.role = value
        STORE.tab(self.router.session.client_token).role = value


def switch() -> rx.Component:
    return rfds.segmented_control(
        {"value": "editor", "label": "Editor"},
        {"value": "viewer", "label": "Viewer"},
        value=RoleState.role,
        on_change=RoleState.set_role,
        label="Viewing as",
    )
