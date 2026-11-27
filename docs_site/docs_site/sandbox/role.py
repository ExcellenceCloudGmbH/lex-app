"""The "Viewing as" switch on a demo card: the tab's role, Editor or Viewer.

``RoleState.role`` is the only record of the role: the sandbox's resolver reads it from the tab's
state on every event, so the switch and the grants cannot disagree.
"""

import reflex as rx
import rfds


class RoleState(rx.State):
    role: str = "editor"

    @rx.event
    def set_role(self, value: str | list[str]):
        if isinstance(value, list):
            value = value[0] if value else ""
        if value not in ("editor", "viewer"):
            return
        self.role = value


def switch() -> rx.Component:
    return rfds.segmented_control(
        {"value": "editor", "label": "Editor"},
        {"value": "viewer", "label": "Viewer"},
        value=RoleState.role,
        on_change=RoleState.set_role,
        label="Viewing as",
    )
