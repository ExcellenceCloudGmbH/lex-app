"""The docs' sandbox: who is asking (a demo user), what they may do (the tab's role, read from
its ``RoleState``) and what ``self.api`` answers (a copy of the sample data for the tab).
Installed in place of lex-app's sign-in, so no demo needs Keycloak, a lex-app instance or a
database.
"""

from typing import Any

from docs_site.sandbox.api import SandboxApi
from docs_site.sandbox.models import MODELS
from docs_site.sandbox.store import STORE

_ALL = ["read", "create", "edit", "delete"]
ROLES: dict[str, list[dict]] = {
    "editor": [{"rsname": f"docs.{name}", "scopes": list(_ALL)} for name in MODELS],
    "viewer": [{"rsname": f"docs.{name}", "scopes": ["read"]} for name in MODELS],
}


async def role_of(target: Any) -> str:
    """The tab's role, from its RoleState. A state with no ``get_state`` (a test's stand-in) is
    an editor."""
    from docs_site.sandbox.role import RoleState

    get_state = getattr(target, "get_state", None)
    if get_state is None:
        return "editor"
    role = (await get_state(RoleState)).role
    return role if role in ROLES else "editor"


async def resolve(state: Any):
    from lex.lex_app.reflex.ui import context, workcopy

    target = workcopy.raw(state)
    try:
        tab = str(target.router.session.client_token or "")
    except Exception:  # noqa: BLE001 - a state without router data comes from no tab
        tab = ""
    data = STORE.tab(tab)
    grants = ROLES[await role_of(target)]

    async def token() -> str:
        return "docs-sandbox"

    async def permissions() -> list:
        return [dict(g, scopes=list(g["scopes"])) for g in grants]

    user = context.User(id="demo", name="Demo Consultant", username="demo", email="demo@example.test")
    return context.Session(user=user, params=context._params_of(target), token=token,
                           permissions=permissions, api=SandboxApi(data), tab=tab)


def install() -> None:
    from lex.lex_app.reflex.ui import context

    context.set_resolver(resolve)
