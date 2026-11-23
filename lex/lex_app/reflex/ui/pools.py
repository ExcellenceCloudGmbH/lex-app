"""The two thread pools lex.ui handler bodies run on: interactive (actions, loads) and jobs."""

from __future__ import annotations

import asyncio
import contextvars
import functools
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

_SETTINGS = {"interactive": ("LEX_UI_ACTION_THREADS", 16), "jobs": ("LEX_UI_JOB_THREADS", 8)}
_POOLS: dict[str, ThreadPoolExecutor] = {}
_LOCK = threading.Lock()


def size(kind: str) -> int:
    """Threads of one pool: its environment setting, else its default; never fewer than one."""
    name, default = _SETTINGS[kind]
    try:
        wanted = int(os.getenv(name) or default)
    except ValueError:
        wanted = default
    return max(1, wanted)


def pool(kind: str) -> ThreadPoolExecutor:
    with _LOCK:
        if kind not in _POOLS:
            _POOLS[kind] = ThreadPoolExecutor(max_workers=size(kind), thread_name_prefix=f"lex-ui-{kind}")
        return _POOLS[kind]


async def run(kind: str, fn: Callable[..., Any], /, *args: Any) -> Any:
    """Run ``fn`` on a pool thread, with the caller's context variables and fresh DB connections."""
    from lex.lex_app.reflex.django_orm import _with_fresh_connections

    call = functools.partial(contextvars.copy_context().run, _with_fresh_connections(fn), *args)
    return await asyncio.get_running_loop().run_in_executor(pool(kind), call)


def shutdown() -> None:
    """Stop both pools (tests)."""
    with _LOCK:
        for executor in _POOLS.values():
            executor.shutdown(wait=False, cancel_futures=True)
        _POOLS.clear()
