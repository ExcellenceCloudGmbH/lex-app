"""Reusable MCP prompt templates for common Lex investigations.

Prompts surface in the MCP client UI as pickable templates. Each
returns plain text — FastMCP wraps it into a ``user`` message — and can
inline pointers to the corresponding MCP resources/tools so the LLM
knows exactly where to fetch supporting data.
"""
from __future__ import annotations

import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    @server.prompt(
        name="lex.investigate_calculation",
        description="Walk an LLM through investigating a calculation by id.",
    )
    def investigate_calculation(calculation_id: str) -> str:
        return (
            f"Investigate Lex calculation `{calculation_id}`.\n\n"
            "Steps:\n"
            f"1. Call tool `lex.calculations.tail_logs` with `calculation_id={calculation_id}` "
            "and the matching `calculation_record` to inspect the live log buffer.\n"
            f"2. Call tool `lex.calculations.list_logs` with `calculation_id={calculation_id}` "
            "for the persisted CalculationLog rows.\n"
            "3. Summarise the calculation's status, surface any error or warning "
            "messages, and propose a next action (rerun, clean, escalate)."
        )

    @server.prompt(
        name="lex.summarize_model",
        description="Summarise the most recent entries of a model container.",
    )
    def summarize_model(
        model_container: str,
        ordering: Optional[str] = None,
        page_size: int = 25,
    ) -> str:
        order_clause = ordering or "-id"
        return (
            f"Summarise the model container `{model_container}`.\n\n"
            f"1. Read resource `lex://models/{model_container}/fields` for the schema.\n"
            f"2. Call tool `lex.entries.list` with `model_container={model_container}`, "
            f"`ordering={order_clause!r}`, `page_size={page_size}`.\n"
            "3. Produce a concise overview: row count, key dimensions, distinctive "
            "values, and any obvious anomalies."
        )

    @server.prompt(
        name="lex.audit_record",
        description="Audit a single record using its history + linked calculation logs.",
    )
    def audit_record(model_container: str, pk: str) -> str:
        return (
            f"Audit record `{pk}` of model `{model_container}`.\n\n"
            f"1. Read resource `lex://entries/{model_container}/{pk}` for the current state.\n"
            f"2. Call tool `lex.entries.history` with `model_container={model_container}`, "
            f"`pk={pk}` for the simple_history timeline.\n"
            f"3. Call tool `lex.calculations.list_logs` (no filter) and identify any "
            "CalculationLog rows that mention this record.\n"
            "4. Report: who changed what when, whether any change came from a "
            "calculation run, and any suspicious pattern."
        )

    @server.prompt(
        name="lex.check_permissions",
        description="Check what the current principal is allowed to do for a model container before writing.",
    )
    def check_permissions(model_container: str) -> str:
        return (
            f"Before attempting any write to `{model_container}`, verify "
            "what the current MCP principal is allowed to do.\n\n"
            "1. Call tool `lex.permissions.user` for the principal's overall "
            "ra-rbac scopes.\n"
            f"2. Call tool `lex.permissions.model` with `model_container={model_container}` "
            "for container-level modification restrictions (`can_create`, "
            "`can_modify`, `can_delete`).\n"
            "3. If the requested action is denied, surface the human-readable "
            "violations from the response and propose either an allowed "
            "alternative or a permission change for the user to request."
        )
