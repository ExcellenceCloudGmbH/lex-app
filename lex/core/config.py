import importlib.util
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Built-in serializer name used by the framework when no override is configured.
DEFAULT_SERIALIZER_NAME = "default"


@dataclass
class LexProjectConfig:
    initial_data: Optional[str] = None
    groups: List[str] = field(default_factory=list)
    #: Name under which the auto-generated framework serializer is registered
    #: when a project-defined ``api_serializers`` mapping overrides ``"default"``
    #: for a model. Defaults to ``"default"`` for backward compatibility.
    default_serializer_name: str = DEFAULT_SERIALIZER_NAME
    #: Per-model overrides for the display names of the History and Audit Log
    #: tabs shown in the frontend detail/show view and the history toggle
    #: button in the list view. Keys are model names (lowercase, matching
    #: ``original_name``). Each value is a dict that may contain
    #: ``"history_tab"`` and/or ``"audit_log_tab"`` with the desired label.
    #: A special ``"__default__"`` key applies to all models without an
    #: explicit override.
    tab_display_names: Dict[str, Dict[str, str]] = field(default_factory=dict)

    #: The exception raised while executing ``lex_config.py``, if any.
    load_error: Optional[BaseException] = None
    #: The values the file set for the keys above, as written (for validation).
    raw_values: Dict[str, Any] = field(default_factory=dict, repr=False)

    _loaded: bool = False

    @classmethod
    def load(cls) -> 'LexProjectConfig':
        """                                                                                        
        Locates and loads lex_config.py from the project root.                                     
        Returns a typed config object.                                                             
        """
        config = cls()

        # Locate project root (using env var or cwd)                                               
        project_root = os.getenv("PROJECT_ROOT", os.getcwd())
        config_path = Path(project_root) / "lex_config.py"

        # Fallback to legacy name if new one missing                                               
        if not config_path.exists():
            legacy_path = Path(project_root) / "_authentication_settings.py"
            if legacy_path.exists():
                config_path = legacy_path

        if config_path.exists():
            try:
                # Load module dynamically                                                          
                spec = importlib.util.spec_from_file_location("lex_user_config", config_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                for key, legacy in (
                    ("INITIAL_DATA", "initial_data_load"),
                    ("PROJECT_GROUPS", "azure_groups"),
                    ("DEFAULT_SERIALIZER_NAME", "default_serializer_name"),
                    ("TAB_DISPLAY_NAMES", "tab_display_names"),
                ):
                    for name in (key, legacy):
                        if hasattr(module, name):
                            config.raw_values[key] = getattr(module, name)
                            break

                # Map attributes                                                                   
                config.initial_data = getattr(module, "INITIAL_DATA", getattr(module,
                                                                              "initial_data_load", None))
                config.groups = getattr(module, "PROJECT_GROUPS", getattr(module, "azure_groups",
                                                                          []))
                # Optional override for the auto-generated "default" serializer
                # name. Accept both upper- and lower-case attribute names so the
                # convention matches the existing config style.
                raw_default_name = getattr(
                    module,
                    "DEFAULT_SERIALIZER_NAME",
                    getattr(module, "default_serializer_name", None),
                )
                if isinstance(raw_default_name, str) and raw_default_name.strip():
                    config.default_serializer_name = raw_default_name.strip()

                # Per-model tab display name overrides
                raw_tab_names = getattr(
                    module,
                    "TAB_DISPLAY_NAMES",
                    getattr(module, "tab_display_names", None),
                )
                if isinstance(raw_tab_names, dict):
                    config.tab_display_names = raw_tab_names

                config._loaded = True
            except Exception as e:
                config.load_error = e
                print(f"Error loading project config: {e}")

        return config  


def validate_project_config(config: Optional[LexProjectConfig] = None) -> LexProjectConfig:
    """Stop at startup on a broken ``lex_config.py`` instead of running with defaults.

    Before, an exception while executing ``lex_config.py`` was only printed and
    every key silently fell back to its default (no initial data, no groups,
    the default serializer name) -- a misconfiguration nobody would notice.
    Raises :class:`~django.core.exceptions.ImproperlyConfigured` when the file
    fails to load or one of its keys has the wrong type: ``INITIAL_DATA`` (a
    path), ``PROJECT_GROUPS`` (names), ``DEFAULT_SERIALIZER_NAME`` (a name),
    ``TAB_DISPLAY_NAMES`` (model name -> {tab: label}). A project without
    ``lex_config.py`` passes. Called from lex's ``ready()``.
    """
    from django.core.exceptions import ImproperlyConfigured

    config = config or LexProjectConfig.load()
    if config.load_error is not None:
        raise ImproperlyConfigured(
            f"lex_config.py could not be loaded: {config.load_error!r}"
        ) from config.load_error
    raw = config.raw_values
    if raw.get("INITIAL_DATA") is not None and not isinstance(raw["INITIAL_DATA"], (str, os.PathLike)):
        raise ImproperlyConfigured("lex_config.INITIAL_DATA must be a path (a string), or None")
    if "PROJECT_GROUPS" in raw:
        groups = raw["PROJECT_GROUPS"]
        if (
            not isinstance(groups, (list, tuple, set, frozenset))
            or not all(isinstance(group, str) for group in groups)
        ):
            raise ImproperlyConfigured("lex_config.PROJECT_GROUPS must be a list of group names")
    if raw.get("DEFAULT_SERIALIZER_NAME") is not None:
        name = raw["DEFAULT_SERIALIZER_NAME"]
        if not isinstance(name, str) or not name.strip():
            raise ImproperlyConfigured("lex_config.DEFAULT_SERIALIZER_NAME must be a non-empty string")
    if raw.get("TAB_DISPLAY_NAMES") is not None:
        names = raw["TAB_DISPLAY_NAMES"]
        if not isinstance(names, Mapping) or not all(
            isinstance(model, str)
            and isinstance(labels, Mapping)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items())
            for model, labels in names.items()
        ):
            raise ImproperlyConfigured(
                "lex_config.TAB_DISPLAY_NAMES must map model names to {tab: label} dicts of strings"
            )
    return config


# ---------------------------------------------------------------------------
# Module-level cached accessor for the configured default serializer name.
#
# ``LexProjectConfig.load`` performs a filesystem lookup and dynamic module
# import. The serializer wiring runs frequently, so we cache the resolved name
# to avoid repeating that work for every model. The cache is cheap to reset in
# tests via :func:`reset_default_serializer_name_cache`.
# ---------------------------------------------------------------------------

_default_serializer_name_cache: Optional[str] = None


def get_configured_default_serializer_name() -> str:
    """Return the project-configured override for the auto-generated
    serializer name, falling back to ``"default"``.

    The value is cached after the first lookup. Tests that mutate
    ``lex_config.py`` at runtime should call
    :func:`reset_default_serializer_name_cache`.
    """
    global _default_serializer_name_cache
    if _default_serializer_name_cache is None:
        try:
            _default_serializer_name_cache = (
                LexProjectConfig.load().default_serializer_name
                or DEFAULT_SERIALIZER_NAME
            )
        except Exception:
            _default_serializer_name_cache = DEFAULT_SERIALIZER_NAME
    return _default_serializer_name_cache


def reset_default_serializer_name_cache() -> None:
    """Reset the cached default serializer name. Intended for tests."""
    global _default_serializer_name_cache
    _default_serializer_name_cache = None


# ---------------------------------------------------------------------------
# Tab display name resolution
# ---------------------------------------------------------------------------

_tab_display_names_cache: Optional[Dict[str, Dict[str, str]]] = None


def get_tab_display_names_for_model(model_name: str) -> Dict[str, str]:
    """Return the tab display name overrides for a given model.

    Merges the ``__default__`` entry (if any) with the model-specific entry
    so that model-level keys take precedence. Returns an empty dict when
    no overrides are configured.
    """
    global _tab_display_names_cache
    if _tab_display_names_cache is None:
        try:
            _tab_display_names_cache = LexProjectConfig.load().tab_display_names or {}
        except Exception:
            _tab_display_names_cache = {}

    defaults = _tab_display_names_cache.get("__default__", {})
    model_specific = _tab_display_names_cache.get(model_name, {})
    if not defaults and not model_specific:
        return {}
    return {**defaults, **model_specific}


def reset_tab_display_names_cache() -> None:
    """Reset the cached tab display names. Intended for tests."""
    global _tab_display_names_cache
    _tab_display_names_cache = None
