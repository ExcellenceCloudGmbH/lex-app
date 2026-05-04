"""MCP tools for calculation orchestration.

All four tools delegate to existing DRF views so RBAC, audit logging and
the calculation-cache machinery (``CacheManager``) behave identically to
the regular HTTP API:

* ``lex.calculations.run`` → :class:`lex.api.views.process_flow.CreateOrUpdate.CreateOrUpdate`
* ``lex.calculations.tail_logs`` → :class:`lex.api.views.calculations.InitCalculationLogs.InitCalculationLogs`
* ``lex.calculations.list_logs`` → :class:`lex.api.views.model_entries.CalculationLogTreeView.CalculationLogTreeView`
* ``lex.calculations.clean`` → :class:`lex.api.views.calculations.CleanCalculations.CleanCalculations`
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from lex.mcp_server.config import mcp_setting
from lex.mcp_server.dispatch import call_view
from lex.mcp_server.tools._common import (
    current_principal,
    envelope as _envelope,
    require_container as _require_container,
)

logger = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    server.add_tool(
        _run_calculation,
        name="lex.calculations.run",
        description=(
            "Trigger (or re-trigger) a calculation by writing to a model "
            "via the run_step pipeline. Pass `next_step=true` to advance "
            "to the next configured step instead of recomputing the "
            "current one."
        ),
    )
    server.add_tool(
        _tail_logs,
        name="lex.calculations.tail_logs",
        description=(
            "Return the cached log tail for a running or recently finished "
            "calculation. Output is truncated to ~64 KB to fit MCP message "
            "budgets."
        ),
    )
    server.add_tool(
        _list_logs,
        name="lex.calculations.list_logs",
        description=(
            "List persisted CalculationLog rows (audit-grade), optionally "
            "filtered by `calculation_id`. Bounded by the LOG_TREE_MAX_ROWS "
            "setting (default 200)."
        ),
    )
    server.add_tool(
        _clean,
        name="lex.calculations.clean",
        description=(
            "Identify which of the supplied {model, record_id} pairs can "
            "have their cached calculation state cleaned up. Mirrors the "
            "/api/clean-calculations endpoint."
        ),
    )


# --------------------------------------------------------------------------- #
# Tool implementations                                                        #
# --------------------------------------------------------------------------- #


async def _run_calculation(
    model_container: str,
    pk: Any,
    data: Optional[Dict[str, Any]] = None,
    next_step: bool = False,
) -> Dict[str, Any]:
    container = _require_container(model_container)
    principal = current_principal()

    from lex.api.views.process_flow.CreateOrUpdate import CreateOrUpdate

    body: Dict[str, Any] = dict(data or {})
    if next_step:
        body["next_step"] = True

    status_code, payload = await call_view(
        CreateOrUpdate,
        principal=principal,
        method="PUT",
        view_kwargs={"model_container": container},
        pk=pk,
        body=body,
    )
    return _envelope(status_code, payload)


async def _tail_logs(
    calculation_record: str,
    calculation_id: str,
) -> Dict[str, Any]:
    _ = current_principal()  # auth gate

    from lex.api.views.calculations.InitCalculationLogs import InitCalculationLogs

    status_code, payload = await call_view(
        InitCalculationLogs,
        principal=current_principal(),
        method="GET",
        view_kwargs={},
        query={
            "calculation_id": calculation_id,
            "calculation_record": calculation_record,
        },
    )

    if isinstance(payload, dict) and isinstance(payload.get("logs"), str):
        max_bytes = int(mcp_setting("LOG_TAIL_MAX_BYTES"))
        text = payload["logs"]
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) > max_bytes:
            payload = dict(payload)
            payload["logs"] = encoded[-max_bytes:].decode("utf-8", errors="replace")
            payload["truncated"] = True
            payload["original_bytes"] = len(encoded)

    return _envelope(status_code, payload)


async def _list_logs(
    calculation_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    _ = current_principal()

    from lex.api.views.model_entries.CalculationLogTreeView import CalculationLogTreeView

    query: Dict[str, Any] = {}
    if calculation_id:
        query["calculation_id"] = calculation_id

    status_code, payload = await call_view(
        CalculationLogTreeView,
        principal=current_principal(),
        method="GET",
        view_kwargs={},
        query=query,
    )

    cap = int(limit or mcp_setting("LOG_TREE_MAX_ROWS"))
    if isinstance(payload, dict) and isinstance(payload.get("data"), list) and cap > 0:
        rows: List[Any] = payload["data"]
        if len(rows) > cap:
            payload = dict(payload)
            payload["data"] = rows[-cap:]
            payload["truncated"] = True
            payload["original_count"] = len(rows)

    return _envelope(status_code, payload)


async def _clean(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Run :class:`CleanCalculations` against the supplied records."""
    _ = current_principal()

    from lex.api.views.calculations.CleanCalculations import CleanCalculations

    status_code, payload = await call_view(
        CleanCalculations,
        principal=current_principal(),
        method="POST",
        view_kwargs={},
        body={"records": list(records)},
    )
    return _envelope(status_code, payload)
