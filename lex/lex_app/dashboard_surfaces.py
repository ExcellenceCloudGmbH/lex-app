"""Which dashboard framework draws each model's dashboards -- told to lex-app's frontend.

lex-app's record page has an Analytics tab, and its tables an Analytics toggle;
each frames a dashboard, Streamlit's or Reflex's. The frontend asks
``api/project-info`` which one, per model (decision D11 of the RFDS plan):

* a model with its own ``reflex_main`` / ``reflex_class_main`` gets Reflex, when
  ``IS_REFLEX_ENABLED`` is on;
* otherwise one with its own ``streamlit_main`` / ``streamlit_class_main`` gets
  Streamlit;
* otherwise ``None``, and the frontend keeps doing what it always did.

``LEX_DASHBOARD_SURFACE=streamlit`` keeps every model on Streamlit, whatever
hooks it has -- for a project moving its dashboards over one at a time.

Read in the Django process, which never imports Reflex: a hook is told apart
from LexModel's default by identity alone, and the project's structure module
is found without importing it.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any

REFLEX = "reflex"
STREAMLIT = "streamlit"

#: ``LEX_DASHBOARD_SURFACE`` values: ``auto`` (the rules above) or ``streamlit``.
SURFACE_SETTINGS = ("auto", STREAMLIT)


def reflex_enabled() -> bool:
    """``IS_REFLEX_ENABLED``, read the way ``IS_STREAMLIT_ENABLED`` is."""
    return os.getenv("IS_REFLEX_ENABLED") == "true"


def surface_setting() -> str:
    value = (os.getenv("LEX_DASHBOARD_SURFACE") or "auto").strip().lower()
    return value if value in SURFACE_SETTINGS else "auto"


def overrides(model: Any, hook: str) -> bool:
    """Whether ``model`` supplies its own ``hook`` rather than LexModel's default."""
    from lex.core.models.LexModel import LexModel

    implementation = getattr(model, hook, None)
    if implementation is None:
        return False
    default = getattr(LexModel, hook, None)
    own = getattr(implementation, "__func__", implementation)
    return default is None or own is not getattr(default, "__func__", default)


def surface_for(model: Any, level: str, *, reflex: bool | None = None, setting: str | None = None) -> str | None:
    """The framework drawing ``model``'s ``level`` (``"record"`` or ``"table"``) dashboard."""
    hooks = {"record": ("reflex_main", "streamlit_main"), "table": ("reflex_class_main", "streamlit_class_main")}
    if level not in hooks:
        raise ValueError(f"level is 'record' or 'table', got {level!r}")
    reflex_hook, streamlit_hook = hooks[level]
    reflex = reflex_enabled() if reflex is None else reflex
    setting = surface_setting() if setting is None else setting
    if reflex and setting != STREAMLIT and overrides(model, reflex_hook):
        return REFLEX
    if overrides(model, streamlit_hook):
        return STREAMLIT
    return None


def project_models() -> list[Any]:
    """The project's own models (its app is named after the repository)."""
    from django.apps import apps

    from lex.lex_app.settings import repo_name

    try:
        return list(apps.get_app_config(repo_name).get_models())
    except LookupError:
        return []


def dashboard_surfaces(models: list[Any] | None = None) -> dict[str, dict[str, str | None]]:
    """``{model id: {"record": surface, "table": surface}}`` for every model with a dashboard."""
    reflex, setting = reflex_enabled(), surface_setting()
    surfaces = {}
    for model in project_models() if models is None else models:
        entry = {level: surface_for(model, level, reflex=reflex, setting=setting) for level in ("record", "table")}
        if entry["record"] or entry["table"]:
            surfaces[model._meta.model_name] = entry
    return surfaces


def reflex_home_available() -> bool:
    """Whether the project has a Reflex home: Reflex is on and ``_reflex_structure.py`` exists."""
    if not reflex_enabled():
        return False
    from lex.lex_app.settings import repo_name

    try:
        return importlib.util.find_spec(f"{repo_name}._reflex_structure") is not None
    except (ImportError, ValueError):
        return False
