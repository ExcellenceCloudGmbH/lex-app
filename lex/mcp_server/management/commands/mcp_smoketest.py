"""Smoke-test the embedded MCP server end-to-end.

Runs an in-process MCP client over ``httpx.ASGITransport`` against the
real Django ASGI app, calls ``tools/list`` and ``lex_models_list``, and
prints the result. Useful for CI sanity checks and local debugging.

Usage::

    python manage.py mcp_smoketest --api-key <raw-key>
"""
from __future__ import annotations

import asyncio
import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run an in-process round-trip against the embedded MCP server."

    def add_arguments(self, parser):
        parser.add_argument(
            "--api-key",
            required=True,
            help="Raw API key value (created via the Django admin) to authenticate with.",
        )
        parser.add_argument(
            "--mount",
            default="/mcp",
            help="MCP mount path (defaults to /mcp).",
        )
        parser.add_argument(
            "--exercise-files",
            nargs=3,
            metavar=("MODEL", "PK", "FIELD"),
            default=None,
            help=(
                "Optional: actually call lex_files_download with the given "
                "model container, pk and file field, then report size_bytes."
            ),
        )
        parser.add_argument(
            "--burst",
            type=int,
            default=0,
            help=(
                "Optional: fire N rapid lex_models_list HTTP requests against "
                "the discovery endpoint and report when (and if) HTTP 429 appears."
            ),
        )

    def handle(self, *args, **options):
        try:
            asyncio.run(
                _run(
                    options["api_key"],
                    options["mount"],
                    self.stdout,
                    exercise_files=options.get("exercise_files"),
                    burst=int(options.get("burst") or 0),
                )
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc


async def _run(api_key: str, mount: str, stdout, *, exercise_files=None, burst=0) -> None:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    from lex.lex_app.asgi import application

    transport = httpx.ASGITransport(app=application)
    base_url = f"http://testserver{mount}"
    headers = {"API-KEY": api_key}

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as _client:
        # The MCP SDK opens its own httpx client; we only verify the
        # ASGI transport works by separately probing the discovery endpoint.
        info = await _client.get("/.well-known/mcp")
        stdout.write(f"discovery: {info.status_code} {info.text}\n")

    async with streamablehttp_client(base_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            stdout.write(f"tools: {[t.name for t in tools.tools]}\n")
            try:
                resources = await session.list_resources()
                stdout.write(f"resources: {[r.name for r in resources.resources]}\n")
            except Exception as exc:
                stdout.write(f"resources: <unavailable: {exc}>\n")
            try:
                prompts = await session.list_prompts()
                stdout.write(f"prompts: {[p.name for p in prompts.prompts]}\n")
            except Exception as exc:
                stdout.write(f"prompts: <unavailable: {exc}>\n")
            result = await session.call_tool("lex_models_list", {})
            stdout.write(json.dumps(result.model_dump(), indent=2, default=str) + "\n")
            try:
                logs = await session.call_tool("lex_calculations_list_logs", {"limit": 5})
                stdout.write("calculations.list_logs (limit=5):\n")
                stdout.write(json.dumps(logs.model_dump(), indent=2, default=str) + "\n")
            except Exception as exc:
                stdout.write(f"calculations.list_logs failed: {exc}\n")

            file_tool_names = {"lex_files_download", "lex_files_export",
                               "lex_sharepoint_download", "lex_sharepoint_preview_link",
                               "lex_sharepoint_share_link", "lex_calculations_download_pdf"}
            registered = {t.name for t in tools.tools}
            missing = file_tool_names - registered
            if missing:
                stdout.write(f"file tools missing: {sorted(missing)}\n")
            else:
                stdout.write("file tools registered: OK\n")

            permission_tool_names = {"lex_permissions_user", "lex_permissions_model"}
            missing_perms = permission_tool_names - registered
            if missing_perms:
                stdout.write(f"permission tools missing: {sorted(missing_perms)}\n")
            else:
                stdout.write("permission tools registered: OK\n")
                try:
                    res = await session.call_tool("lex_permissions_user", {})
                    dump = res.model_dump()
                    # Trim to first 5 entries to keep stdout readable.
                    structured = dump.get("structuredContent") or {}
                    if isinstance(structured.get("result"), list):
                        structured["result"] = structured["result"][:5]
                    stdout.write("permissions.user (first 5):\n")
                    stdout.write(json.dumps(dump, indent=2, default=str) + "\n")
                except Exception as exc:
                    stdout.write(f"permissions.user failed: {exc}\n")

            if exercise_files:
                model, pk, field = exercise_files
                try:
                    res = await session.call_tool(
                        "lex_files_download",
                        {"model_container": model, "pk": pk, "field": field},
                    )
                    dump = res.model_dump()
                    stdout.write("files.download:\n")
                    # Strip base64 from the printed dump so the terminal stays usable.
                    sanitized = json.loads(json.dumps(dump, default=str))
                    _strip_base64(sanitized)
                    stdout.write(json.dumps(sanitized, indent=2, default=str) + "\n")
                except Exception as exc:
                    stdout.write(f"files.download failed: {exc}\n")

    if burst > 0:
        # Probe the rate limiter directly via the discovery endpoint, which
        # also goes through the MCP ASGI auth + rate-limit pipeline.
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as probe:
            seen_429_at = None
            for i in range(1, burst + 1):
                resp = await probe.get(mount, headers={"API-KEY": api_key})
                if resp.status_code == 429:
                    seen_429_at = i
                    stdout.write(
                        f"burst: 429 at request #{i}; body={resp.text[:200]}\n"
                    )
                    break
            if seen_429_at is None:
                stdout.write(f"burst: completed {burst} requests without a 429\n")


def _strip_base64(node):
    if isinstance(node, dict):
        if "base64" in node and isinstance(node["base64"], str):
            node["base64"] = f"<{len(node['base64'])} chars elided>"
        for value in node.values():
            _strip_base64(value)
    elif isinstance(node, list):
        for value in node:
            _strip_base64(value)
