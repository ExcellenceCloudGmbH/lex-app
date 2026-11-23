"""Aids for the lex.ui harness tests (scenarios 1.771, 1.776, 1.777).

* :func:`unstored_on_error` makes the in-memory state manager behave like Reflex's Redis manager
  for a block that raises: the block's changes are not stored (Redis writes a state back only
  when ``modify_state``'s block exits normally), while the browser may already have seen them.
* :func:`lock_held` and :func:`lock_waiters` tell whether an event holds a tab's state lock and
  how many wait for it, so a test can tell that a handler is parked at ``async with self``
  before it cancels it -- no fixed waits.
"""

from __future__ import annotations

import contextlib
import copy
from typing import Any, Iterator
from unittest.mock import patch


def _tree(state: Any) -> list:
    out = [state]
    for sub in state.substates.values():
        out.extend(_tree(sub))
    return out


@contextlib.contextmanager
def unstored_on_error() -> Iterator[None]:
    """While open, a ``modify_state`` block that raises leaves the stored state as it was."""
    from reflex.istate.manager.memory import StateManagerMemory

    @contextlib.asynccontextmanager
    async def modify_state(self, token, **context):
        token = self._coerce_token(token)
        lock = await self._get_state_lock(token)
        async with lock:
            stored = self._get_or_create_state(token)
            saved = [(state,
                      {name: copy.deepcopy(object.__getattribute__(state, name)) for name in type(state).base_vars},
                      copy.deepcopy(dict(object.__getattribute__(state, "_backend_vars"))))
                     for state in _tree(stored)]
            try:
                yield stored
            except BaseException:
                for state, base, backend in saved:      # not stored: the state is what it was
                    for name, value in base.items():
                        object.__setattr__(state, name, value)
                    object.__setattr__(state, "_backend_vars", backend)
                raise

    with patch.object(StateManagerMemory, "modify_state", modify_state):
        yield


def _locks(harness: Any) -> list:
    return list(harness._processor._root_context.state_manager._states_locks.values())


def lock_held(harness: Any) -> bool:
    """Whether an event holds a state lock in ``harness``'s state manager."""
    return any(lock.locked() for lock in _locks(harness))


def lock_waiters(harness: Any) -> int:
    """How many events are waiting for a state lock in ``harness``'s state manager."""
    return sum(len(lock._waiters or ()) for lock in _locks(harness))
