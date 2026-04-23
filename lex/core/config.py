import os
import importlib.util
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional


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
