import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

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
                print(f"Error loading project config: {e}")

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
