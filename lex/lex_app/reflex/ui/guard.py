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


class Rule:
    """A project's own access rule, marked ``@ui.rule``: accepted wherever ``requires=`` is."""

    def __init__(self, fn: Any):
        import functools
        import inspect

        if not callable(fn) or inspect.iscoroutinefunction(fn):
            raise TypeError(f"@ui.rule marks a plain function of (user, params); got {fn!r}.")
        try:
            names = [p.name for p in inspect.signature(fn).parameters.values()
                     if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        except (TypeError, ValueError):
            names = []
        if len(names) != 2:
            raise TypeError(f"@ui.rule {getattr(fn, '__qualname__', fn)} takes {', '.join(names) or 'nothing'}; a rule "
                            "takes (user, params) -- the signed-in user and the page's values -- and returns True, "
                            "False or the sentence the user is refused with: def region_team(user, params): ...")
        self.fn = fn
        functools.update_wrapper(self, fn)

    def __call__(self, user: Any, params: Any) -> Any:
        return self.fn(user, params)

    def __repr__(self) -> str:
        return f"the rule {getattr(self.fn, '__qualname__', self.fn)}"


def rule(fn: Any) -> Rule:
    """Mark a project's own access rule, for ``requires=`` on a page or any handler.

    A rule is a function of the signed-in user and the page's values (``self.params``) that returns
    ``True``, ``False``, or the sentence the user is refused with::

        @ui.rule
        def region_team(user, params):
            if params.get("region") in teams.regions_of(user.id):
                return True
            return "Only the region's team sees its report."

        @ui.page("/reports/[region]", requires=region_team)
        @ui.action(requires=[ui.read(Report), region_team])

    lex-app asks it on the server, on a worker thread (it may query the database), at most once per
    event. ``False`` or nothing (``None``) refuses with lex-app's sentence; a rule that raises or
    answers anything else refuses as a failure. It stays a plain function: a load
    may call it to decide what to show, ``region_team(self.user, self.params)``.
    """
    return Rule(fn)


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
        if not isinstance(item, (Requirement, _SignedIn, Rule)):
            hint = (f" To use the function {getattr(item, '__qualname__', item)} as a rule of your own, mark it "
                    "@ui.rule." if callable(item) and not isinstance(item, type) else "")
            raise TypeError(
                f"requires= takes ui.read(Model), ui.create(), ui.edit(), ui.delete(), ui.permission(), "
                f"ui.signed_in or a rule marked @ui.rule (or a list of them); got {item!r}.{hint}"
            )
    return items


def rules_of(requirements: tuple | None) -> tuple:
    """The project's rules among ``requirements``, each once."""
    return tuple(dict.fromkeys(item for item in requirements or () if isinstance(item, Rule)))


def granted(requirements: tuple, permissions: list[dict]) -> bool:
    """Whether Keycloak's ``permissions`` meet every requirement (model-wide grants only)."""
    for item in requirements:
        if isinstance(item, (_SignedIn, Rule)):           # a rule is asked on its own (handlers._refusal)
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
