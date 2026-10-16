"""Who receives which realtime events (WebSockets).

By default every signed-in socket receives every ``record_mutation`` and
calculation-status event — fine for one team, a leak of activity metadata
between tenants. Two optional ``lex_config.py`` hooks scope delivery:

* ``MUTATION_GROUPS(instance_or_model) -> list[str]`` – the groups an event
  about this record (or model, for bulk changes) goes to;
* ``USER_GROUPS(user) -> list[str]`` – the groups a socket joins.

With both set, an event reaches only sockets that share a group with it.
Payloads do not change. A model can also opt out of mutation broadcasts
entirely with ``broadcast_mutations = False``.
"""
from __future__ import annotations

import re
from typing import Any, List

from lex.core.extensions import get_hook

_UNSAFE = re.compile(r"[^A-Za-z0-9\-_.]")


def _group(base: str, suffix: Any) -> str:
    # Channels group names: ASCII letters, digits, "-", "_" and "."; under 100 characters.
    return f"{base}.{_UNSAFE.sub('_', str(suffix))}"[:99]


def scoped() -> bool:
    return get_hook("MUTATION_GROUPS") is not None and get_hook("USER_GROUPS") is not None


def event_groups(base: str, target) -> List[str]:
    """Groups an event about ``target`` (an instance or a model class) is sent to."""
    if not scoped():
        return [base]
    groups = get_hook("MUTATION_GROUPS")(target) or []
    return [_group(base, g) for g in groups]


def socket_groups(base: str, user) -> List[str]:
    """Groups a socket of ``user`` joins."""
    if not scoped():
        return [base]
    if user is None or not getattr(user, "is_authenticated", False):
        return []
    groups = get_hook("USER_GROUPS")(user) or []
    return [_group(base, g) for g in groups]


def broadcasts(target) -> bool:
    """Whether mutations of ``target``'s model are broadcast (``broadcast_mutations``)."""
    model = target if isinstance(target, type) else type(target)
    return bool(getattr(model, "broadcast_mutations", True))
