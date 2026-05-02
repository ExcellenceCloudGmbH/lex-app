"""Resolve registered ``model_container`` instances for MCP tooling."""
from __future__ import annotations

from functools import lru_cache
from typing import Iterable, List

from lex.mcp_server.config import mcp_setting


def _ensure_collection_initialized() -> None:
    """Force the lazy ``ModelCollection`` build on the singleton site."""
    from lex.process_admin.settings import processAdminSite

    if not processAdminSite.initialized:
        # Trigger the lazy build inside ``processAdminSite.urls``.
        _ = processAdminSite.urls


def _all_containers() -> List:
    _ensure_collection_initialized()
    from lex.process_admin.settings import processAdminSite

    if processAdminSite.model_collection is None:
        return []
    return sorted(
        processAdminSite.model_collection.all_containers,
        key=lambda c: c.id,
    )


def exposed_containers() -> List:
    """Return the sorted list of containers that should be exposed via MCP."""
    allowlist = mcp_setting("EXPOSED_MODELS")
    containers = _all_containers()
    if allowlist is None:
        return containers
    allowed = {str(x).lower() for x in allowlist}
    return [c for c in containers if c.id in allowed]


def container_is_writable(container_id: str) -> bool:
    if not mcp_setting("ENABLE_WRITE"):
        return False
    deny = {str(x).lower() for x in mcp_setting("WRITE_DISABLED_MODELS") or ()}
    return container_id not in deny


@lru_cache(maxsize=1)
def container_index() -> dict:
    return {c.id: c for c in exposed_containers()}


def get_container(container_id: str):
    """Look up an exposed container by id; return ``None`` if not exposed."""
    return container_index().get(container_id)


def reset_caches() -> None:
    container_index.cache_clear()


def container_ids() -> Iterable[str]:
    return container_index().keys()
