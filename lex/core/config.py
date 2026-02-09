import os
import importlib.util
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class LexProjectConfig:
    initial_data: Optional[str] = None
    groups: List[str] = field(default_factory=list)

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
                config._loaded = True
            except Exception as e:
                print(f"Error loading project config: {e}")

        return config  