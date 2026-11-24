"""A copy of the sample data per browser tab, in this process's memory.

A reader's edits never reach another reader, a reload of the page (a new tab token) starts from
the seed, and a tab idle for ``idle_seconds`` is dropped. One backend process only: the copies
are not shared between processes.
"""

import threading
import time
from collections.abc import Callable

from docs_site.sandbox.data import seed


class TabData:
    def __init__(self, now: float):
        self.rows: dict[str, list[dict]] = seed()
        self.role: str = "editor"
        self.seen = now


class Store:
    def __init__(self, clock: Callable[[], float] = time.monotonic, idle_seconds: float = 1800):
        self._clock = clock
        self._idle = idle_seconds
        self._tabs: dict[str, TabData] = {}
        self._lock = threading.Lock()

    def tab(self, tab_id: str) -> TabData:
        with self._lock:
            now = self._clock()
            for key in [k for k, t in self._tabs.items() if now - t.seen > self._idle]:
                del self._tabs[key]
            data = self._tabs.get(tab_id)
            if data is None:
                data = self._tabs[tab_id] = TabData(now)
            data.seen = now
            return data

    def reset(self, tab_id: str) -> None:
        with self._lock:
            self._tabs.pop(tab_id, None)


STORE = Store()
