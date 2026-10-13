"""The product areas fleet metrics counts (K2), in the two vocabularies that write them.

Two writers share this module, so they can never disagree (spec §5.3.1, §5.3.2):

* ``API_AREAS`` maps the URL name a request resolved to onto an area. It is the backend's
  view of an area, recorded on the ``api`` channel as supporting detail. The backend
  cannot tell several interface areas apart: the show page, the edit drawer and a hover
  card make the same request, and starting a calculation is the same PATCH as saving an
  edit. So K2 is never read from it.
* ``UI_AREAS`` is the closed list the web interface may send to ``POST api/usage/area``,
  recorded on the ``ui`` and ``ui_embed`` channels. It is K2's vocabulary. It contains
  every API area except ``app``. The interface mirrors it in process-admin-general-client
  (``src/usage/areas.ts``), so the two lists change in the same release pair.

A request is classified by URL name, never by path: names do not move when
``DJANGO_BASE_PATH`` prefixes every route. A named route nobody mapped is ``app``: the
person was here. Two kinds of request are not recorded at all (``None``):

* an unnamed route, such as the SPA's catch-all and static assets, which are not use;
* the usage endpoint itself, whose event is already recorded on its interface channel.

``calculate`` has no API area. The Instance Controller counts calculation runs from the
audit tables (K3), on every instance, whether or not it runs this recorder.

Design: docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md (§5.3)
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Optional

APP = "app"
GRID = "grid"
HISTORY = "history"

#: The route name of ``POST api/usage/area`` (lex/process_admin/sites/process_admin_site.py).
USAGE_AREA_URL_NAME = "usage-area"

API_AREAS: Mapping[str, str] = MappingProxyType({
    "model-entries-list": GRID,
    "model-history-list": HISTORY,
    "model-one-entry-read-update-delete": "record",
    "model-one-entry-create": "record",
    "model-many-entries": "record",
    "model-export": "export",
    "calculationlog-tree": "calculation_logs",
    "init-calculation-logs": "calculation_logs",
    "download-markdown-pdf": "calculation_logs",
    "process": "processes",
    "run_step": "processes",
    "htmlreports": "reports",
    "streamlit_token": "analytics",
    # Routed in lex-app-v2, though absent from spec §5.3.1's table. The interface's own
    # search uses a query-string form this route does not match; see the plan, Task 2.
    "global-search": "search",
    "file-download": "files",
    "sharepoint-file-download": "files",
    "sharepoint-file-share-link": "files",
    "sharepoint-file-preview-link": "files",
})

UI_AREAS: frozenset[str] = frozenset({
    "grid", "record", "create", "history", "audit_log", "calculate", "calculation_logs",
    "processes", "reports", "analytics", "dashboard", "search", "export", "files",
    "monitoring",
})

_NOT_RECORDED = frozenset({USAGE_AREA_URL_NAME})


def area_for(url_name: Optional[str], query_params: Mapping[str, str]) -> Optional[str]:
    """The API area of a request that resolved to ``url_name``, or None to skip it.

    A list request carrying a non-empty ``as_of`` is the grid's history mode, so it counts
    as ``history``. An unmapped name is ``app``. An unnamed route and the usage endpoint
    are not recorded.
    """
    if not url_name or url_name in _NOT_RECORDED:
        return None
    area = API_AREAS.get(url_name, APP)
    if area == GRID and query_params.get("as_of"):
        return HISTORY
    return area
