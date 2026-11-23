"""What a lex.ui handler requires, and the guard on direct database access."""

from __future__ import annotations

import contextlib
import dataclasses
import types
from typing import Any, Iterator


@dataclasses.dataclass(frozen=True)
class Requirement:
    """Keycloak grants ``scope`` on ``target`` (a model class or a resource name), model-wide."""

    target: Any
    scope: str

    @property
    def resource(self) -> str:
        from lex.lex_app.reflex.auth import _resource_name

        return _resource_name(self.target)


class _SignedIn:
    def __repr__(self) -> str:
        return "ui.signed_in"


#: ``requires=ui.signed_in``: any signed-in user may run the handler's queries.
signed_in = _SignedIn()


def permission(target: Any, scope: str) -> Requirement:
    return Requirement(target, str(scope))


def read(model: Any) -> Requirement:
    return Requirement(model, "read")


def create(model: Any) -> Requirement:
    return Requirement(model, "create")


def edit(model: Any) -> Requirement:
    return Requirement(model, "edit")


def delete(model: Any) -> Requirement:
    return Requirement(model, "delete")


def normalise(requires: Any) -> tuple | None:
    """``requires=`` as a tuple of requirements; ``None`` when the handler declared nothing."""
    if requires is None:
        return None
    items = tuple(requires) if isinstance(requires, (list, tuple)) else (requires,)
    if not items:
        raise ValueError(
            "requires= must name at least one requirement: ui.read(Model), ui.create(), ui.edit(), "
            "ui.delete(), ui.permission() or ui.signed_in"
        )
    for item in items:
        if not isinstance(item, (Requirement, _SignedIn)):
            raise TypeError(
                f"requires= takes ui.read(Model), ui.create(), ui.edit(), ui.delete(), ui.permission() "
                f"or ui.signed_in (or a list of them); got {item!r}"
            )
    return items


def granted(requirements: tuple, permissions: list[dict]) -> bool:
    """Whether Keycloak's ``permissions`` meet every requirement (model-wide grants only)."""
    for item in requirements:
        if isinstance(item, _SignedIn):
            continue
        if not any(
            isinstance(grant, dict)
            and grant.get("rsname") == item.resource
            and grant.get("resource_set_id") is None
            and item.scope in (grant.get("scopes") or ())
            for grant in permissions or ()
        ):
            return False
    return True


class UnguardedQuery(RuntimeError):
    """A lex.ui handler queried the database without declaring what it requires."""


@contextlib.contextmanager
def block_queries(handler: str) -> Iterator[None]:
    """Refuse every database query on this thread while the block is open."""
    from django.db import connections

    def refuse(execute, sql, params, many, context):
        raise UnguardedQuery(
            f"{handler} queried the database directly, which bypasses lex-app's field and row rules. "
            "Read and write through self.api, or declare what the handler requires: "
            "@ui.action(requires=ui.read(Model))."
        )

    with contextlib.ExitStack() as stack:
        for connection in connections.all():
            stack.enter_context(connection.execute_wrapper(refuse))
        yield


class _Actor:
    """The signed-in user as lex-app's audit fields read a request's user."""

    is_authenticated = True

    def __init__(self, user: Any):
        # The user's id when they have neither a username nor an e-mail: an audit field never
        # falls back to the anonymous actor for a signed-in user.
        self._name = str(getattr(user, "username", "") or getattr(user, "email", "") or getattr(user, "id", "") or "")

    def __str__(self) -> str:
        return self._name


@contextlib.contextmanager
def acting_user(user: Any) -> Iterator[None]:
    """Make ``user`` the actor of ORM writes on this thread (created_by / edited_by)."""
    from lex.api.utils import operation_context

    current = operation_context.get()
    request = types.SimpleNamespace(user=_Actor(user))
    token = operation_context.set({**(current if isinstance(current, dict) else {}), "request_obj": request})
    try:
        yield
    finally:
        operation_context.reset(token)
