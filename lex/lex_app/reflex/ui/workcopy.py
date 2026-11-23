"""Copies of a state's fields: the snapshot a failed handler returns to, and a job's working copy."""

from __future__ import annotations

import copy
import inspect
import types
from typing import Any


def raw(state: Any) -> Any:
    """The state itself, behind a background handler's proxy."""
    return getattr(state, "__wrapped__", state)


def fields_of(state_cls: Any) -> tuple[str, ...]:
    """The class's own fields: its vars, and its backend vars (not the inherited ones)."""
    inherited = set(getattr(state_cls, "inherited_backend_vars", {}) or {})
    backend = [name for name in state_cls.backend_vars if name not in inherited]
    return (*state_cls.base_vars, *backend)


def _plain(value: Any) -> Any:
    """A deep copy of a field's value, without Reflex's change-tracking wrapper."""
    return copy.deepcopy(getattr(value, "__wrapped__", value))


def snapshot(state: Any) -> dict[str, Any]:
    """Deep copies of the state's own fields."""
    target = raw(state)
    return {name: _plain(getattr(target, name)) for name in fields_of(type(target))}


def restore(state: Any, snap: dict[str, Any]) -> None:
    """Set every field that differs from ``snap`` back to its snapshot value."""
    target = raw(state)
    for name, value in snap.items():
        if _plain(getattr(target, name)) != value:
            setattr(state, name, copy.deepcopy(value))


def reset(state: Any, keep: tuple[str, ...] = ()) -> None:
    """Set the state's own fields to their class defaults, except ``keep``."""
    state_cls = type(raw(state))
    declared = state_cls.get_fields()
    for name in fields_of(state_cls):
        if name in keep:
            continue
        if name in state_cls.backend_vars:
            default = copy.deepcopy(state_cls.backend_vars[name])
        else:
            default = declared[name].default_value()
        setattr(state, name, default)


class Draft:
    """What a load or job body sees as ``self``: the state's fields, as a working copy."""

    __slots__ = ("_cls", "_values", "_base")

    def __init__(self, state_cls: Any, values: dict[str, Any]):
        object.__setattr__(self, "_cls", state_cls)
        object.__setattr__(self, "_values", values)
        object.__setattr__(self, "_base", copy.deepcopy(values))

    def __getattr__(self, name: str) -> Any:
        values = object.__getattribute__(self, "_values")
        if name in values:
            return values[name]
        state_cls = object.__getattribute__(self, "_cls")
        try:
            static = inspect.getattr_static(state_cls, name)
        except AttributeError:
            raise AttributeError(f"{state_cls.__name__} has no attribute {name!r}") from None
        from reflex.event import EventHandler

        if isinstance(static, property):
            return static.fget(self)
        if isinstance(static, EventHandler):
            raise AttributeError(
                f"{state_cls.__name__}.{name} is an event handler. Put shared logic in a helper method "
                f"whose name starts with '_' and call that instead."
            )
        computed = getattr(static, "fget", None)
        if computed is not None:
            return computed(self)
        if inspect.isfunction(static):
            return types.MethodType(static, self)
        return getattr(state_cls, name)

    def __setattr__(self, name: str, value: Any) -> None:
        values = object.__getattribute__(self, "_values")
        if name not in values:
            state_cls = object.__getattribute__(self, "_cls")
            raise AttributeError(f"{state_cls.__name__} has no field {name!r}; declare it on the state to assign it")
        values[name] = value

    def changed(self) -> dict[str, Any]:
        """The fields whose value differs from the last merge (assignments and in-place changes)."""
        values = object.__getattribute__(self, "_values")
        base = object.__getattribute__(self, "_base")
        return {name: value for name, value in values.items() if value != base.get(name)}

    def rebase(self, values: dict[str, Any]) -> None:
        """Start again from ``values`` (the live state's fields after a merge)."""
        object.__setattr__(self, "_values", values)
        object.__setattr__(self, "_base", copy.deepcopy(values))


def merge(state: Any, draft: Draft) -> list[str]:
    """Write the draft's changed fields to ``state``, then refresh the draft from it."""
    changed = draft.changed()
    for name, value in changed.items():
        setattr(state, name, copy.deepcopy(value))
    draft.rebase(snapshot(state))
    return list(changed)
