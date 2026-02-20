import json
import keyword
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from django.contrib import admin
from django.conf import settings
from django.core.exceptions import SynchronousOnlyOperation
from django.db import connection, models

from lex.lex_app.apps import LexAppConfig


def _safe_attr_name(column_name: str, used_names: set[str]) -> str:
    candidate = re.sub(r"\W+", "_", column_name.strip().lower())
    candidate = candidate.strip("_") or "column"
    if candidate[0].isdigit():
        candidate = f"f_{candidate}"
    if keyword.iskeyword(candidate):
        candidate = f"{candidate}_field"

    base = candidate
    idx = 1
    while candidate in used_names:
        idx += 1
        candidate = f"{base}_{idx}"
    used_names.add(candidate)
    return candidate


def _map_db_field(column, introspection, pk_column: str) -> Tuple[str, models.Field]:
    try:
        db_field_type = introspection.get_field_type(column.type_code, column)
    except Exception:
        db_field_type = "TextField"
    field_kwargs: Dict[str, object] = {
        "null": bool(getattr(column, "null_ok", True)),
        "blank": bool(getattr(column, "null_ok", True)),
    }
    if column.name == pk_column:
        field_kwargs["primary_key"] = True
        field_kwargs.pop("null", None)
        field_kwargs.pop("blank", None)

    if db_field_type in {"AutoField", "SmallAutoField", "BigAutoField"}:
        field_cls = {
            "AutoField": models.AutoField,
            "SmallAutoField": models.SmallAutoField,
            "BigAutoField": models.BigAutoField,
        }[db_field_type]
        return db_field_type, field_cls(**field_kwargs)

    if db_field_type in {"BigIntegerField", "IntegerField", "SmallIntegerField"}:
        return db_field_type, getattr(models, db_field_type)(**field_kwargs)
    if db_field_type in {"BooleanField", "NullBooleanField"}:
        return "BooleanField", models.BooleanField(**field_kwargs)
    if db_field_type in {"DateTimeField", "DateField", "TimeField", "DurationField"}:
        return db_field_type, getattr(models, db_field_type)(**field_kwargs)
    if db_field_type == "DecimalField":
        precision = getattr(column, "precision", None) or 38
        scale = getattr(column, "scale", None) or 18
        return db_field_type, models.DecimalField(
            max_digits=precision,
            decimal_places=scale,
            **field_kwargs,
        )
    if db_field_type == "FloatField":
        return db_field_type, models.FloatField(**field_kwargs)
    if db_field_type == "UUIDField":
        return db_field_type, models.UUIDField(**field_kwargs)
    if db_field_type == "BinaryField":
        return db_field_type, models.BinaryField(**field_kwargs)
    if db_field_type == "JSONField":
        return db_field_type, models.JSONField(**field_kwargs)
    if db_field_type == "CharField":
        max_length = getattr(column, "internal_size", None)
        if not max_length or max_length <= 0:
            return "TextField", models.TextField(**field_kwargs)
        return db_field_type, models.CharField(max_length=max_length, **field_kwargs)

    # Safe fallback for unknown/custom DB types.
    return "TextField", models.TextField(**field_kwargs)


def _deny_legacy_save(self, *args, **kwargs):
    raise NotImplementedError("This is a legacy archive model. Edits are not allowed.")


def _deny_legacy_delete(self, *args, **kwargs):
    raise NotImplementedError("This is a legacy archive model. Deletions are not allowed.")


class LegacyDataConfig(LexAppConfig):
    name = "lex.legacy_data"
    verbose_name = "Legacy Data (V1 Archive)"

    def _table_names(self) -> List[str]:
        if hasattr(self, "_table_names_cache"):
            return self._table_names_cache
        try:
            table_names = connection.introspection.table_names()
            self._table_introspection_unavailable = False
        except SynchronousOnlyOperation:
            # Avoid startup crash in async lifecycle.
            table_names = []
            self._table_introspection_unavailable = True
        except Exception:
            table_names = []
            self._table_introspection_unavailable = True
        self._table_names_cache = table_names
        return table_names

    def _table_exists(self, table_name: str) -> bool:
        return table_name in self._table_names()

    def _has_table_introspection(self) -> bool:
        self._table_names()
        return not getattr(self, "_table_introspection_unavailable", False)

    def _manifest_path(self) -> Path:
        override = os.getenv("LEX_LEGACY_FREEZE_MANIFEST_PATH")
        if override:
            return Path(override).expanduser().resolve()

        manifest_name = ".lex_legacy_freeze_manifest.json"
        candidates: List[Path] = []

        env_project_root = os.getenv("PROJECT_ROOT")
        if env_project_root:
            candidates.append(Path(env_project_root).expanduser() / manifest_name)

        base_dir = getattr(settings, "BASE_DIR", None)
        if base_dir:
            base_dir_path = Path(base_dir).expanduser()
            candidates.append(base_dir_path / manifest_name)
            candidates.append(base_dir_path.parent / manifest_name)

        cwd = Path(os.getcwd()).resolve()
        candidates.append(cwd / manifest_name)
        for parent in cwd.parents:
            candidates.append(parent / manifest_name)

        # Prefer the first existing manifest.
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()

        # Fallback path for environments where file is generated later.
        return (candidates[0] if candidates else (cwd / manifest_name)).resolve()

    def _load_manifest_tables(self) -> List[str]:
        path = self._manifest_path()
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return []
        tables = payload.get("freeze_tables") or []
        return [str(table_name) for table_name in tables if str(table_name).strip()]

    def _get_table_pk_column(self, table_name: str) -> Optional[str]:
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, table_name)
        pk_columns = []
        for data in constraints.values():
            if data.get("primary_key"):
                pk_columns.extend(data.get("columns") or [])
        pk_columns = list(dict.fromkeys(pk_columns))
        if len(pk_columns) != 1:
            return None
        return pk_columns[0]

    def _build_dynamic_model(self, table_name: str):
        pk_column = self._get_table_pk_column(table_name)
        if not pk_column:
            return None

        with connection.cursor() as cursor:
            table_description = connection.introspection.get_table_description(
                cursor, table_name
            )

        used_names: set[str] = set()
        attrs: Dict[str, object] = {
            "__module__": self.__module__,
            "_is_dynamic_legacy_archive": True,
            "_legacy_archive_table_name": table_name,
            "can_create": lambda self, request=None: False,
            "can_delete": lambda self, request=None: False,
            "can_edit": lambda self, request=None: set(),
            "save": _deny_legacy_save,
            "delete": _deny_legacy_delete,
        }

        for column in table_description:
            db_type, field = _map_db_field(column, connection.introspection, pk_column)
            attr_name = _safe_attr_name(column.name, used_names)
            if attr_name != column.name:
                field.db_column = column.name
            attrs[attr_name] = field

        class Meta:
            managed = False
            app_label = "legacy_data"
            db_table = table_name
            verbose_name = f"Legacy {table_name}"
            verbose_name_plural = f"Legacy {table_name}"

        attrs["Meta"] = Meta

        class_name = "LegacyDynamic" + "".join(
            token.capitalize() for token in re.split(r"[^a-zA-Z0-9]+", table_name) if token
        )
        return type(class_name, (models.Model,), attrs)

    def _register_static_legacy_models(self, processAdminSite, read_only_restriction):
        from lex.legacy_data.admin import (
            LegacyCalculationIdAdmin,
            LegacyCalculationLogAdmin,
            LegacyLogAdmin,
            LegacyUserChangeLogAdmin,
        )
        from lex.legacy_data.models import (
            LegacyCalculationId,
            LegacyCalculationLog,
            LegacyLog,
            LegacyUserChangeLog,
        )
        from lex.legacy_data.serializers.legacy_data_serializers import (
            LegacyCalculationIdSerializer,
            LegacyCalculationLogSerializer,
            LegacyLogSerializer,
            LegacyUserChangeLogSerializer,
        )

        LegacyCalculationLog.api_serializers = {"default": LegacyCalculationLogSerializer}
        LegacyUserChangeLog.api_serializers = {"default": LegacyUserChangeLogSerializer}
        LegacyCalculationId.api_serializers = {"default": LegacyCalculationIdSerializer}
        LegacyLog.api_serializers = {"default": LegacyLogSerializer}

        LegacyCalculationLog.modification_restriction = read_only_restriction
        LegacyUserChangeLog.modification_restriction = read_only_restriction
        LegacyCalculationId.modification_restriction = read_only_restriction
        LegacyLog.modification_restriction = read_only_restriction

        static_models = [
            (LegacyCalculationLog, LegacyCalculationLogAdmin),
            (LegacyUserChangeLog, LegacyUserChangeLogAdmin),
            (LegacyCalculationId, LegacyCalculationIdAdmin),
            (LegacyLog, LegacyLogAdmin),
        ]
        table_introspection_available = self._has_table_introspection()
        table_aliases = {
            LegacyCalculationLog: [
                "generic_app_calculationlog",
                "generic_app_calculation_log",
                "generic_calculation_log",
            ],
            LegacyUserChangeLog: [
                "generic_app_userchangelog",
                "generic_app_user_change_log",
                "generic_user_change_log",
            ],
            LegacyCalculationId: [
                "generic_app_calculationids",
                "generic_app_calculation_ids",
                "generic_calculation_ids",
            ],
            LegacyLog: [
                "generic_app_log",
                "generic_log",
            ],
        }

        for model, admin_model in static_models:
            preferred = model._meta.db_table
            selected_table = preferred
            if table_introspection_available:
                if not self._table_exists(preferred):
                    selected_table = None
                    for alias in table_aliases.get(model, []):
                        if self._table_exists(alias):
                            selected_table = alias
                            break
                if not selected_table:
                    continue

            if model._meta.db_table != selected_table:
                # Runtime alias to cope with project-specific legacy naming differences.
                model._meta.db_table = selected_table

            if not admin.site.is_registered(model):
                admin.site.register(model, admin_model)
            processAdminSite.register([model])
            if model._meta.model_name not in self.untracked_models:
                self.untracked_models.append(model._meta.model_name)

    def _register_dynamic_legacy_models(self, processAdminSite, read_only_restriction):
        from lex.legacy_data.admin.read_only_admin import ReadOnlyAdmin

        if not self._has_table_introspection():
            return

        if not hasattr(self, "_dynamic_model_cache"):
            self._dynamic_model_cache = {}

        for table_name in self._load_manifest_tables():
            if not self._table_exists(table_name):
                continue

            dynamic_model = self._dynamic_model_cache.get(table_name)
            if dynamic_model is None:
                dynamic_model = self._build_dynamic_model(table_name)
                if dynamic_model is not None:
                    self._dynamic_model_cache[table_name] = dynamic_model
            if dynamic_model is None:
                continue

            dynamic_model.modification_restriction = read_only_restriction

            model_fields = [f.name for f in dynamic_model._meta.fields]
            list_display = model_fields[:8] if model_fields else []
            search_fields = [model_fields[0]] if model_fields else []
            dynamic_admin = type(
                f"{dynamic_model.__name__}Admin",
                (ReadOnlyAdmin,),
                {
                    "list_display": tuple(list_display),
                    "search_fields": tuple(search_fields),
                },
            )

            if not admin.site.is_registered(dynamic_model):
                admin.site.register(dynamic_model, dynamic_admin)
            processAdminSite.register([dynamic_model])
            if dynamic_model._meta.model_name not in self.untracked_models:
                self.untracked_models.append(dynamic_model._meta.model_name)

    def register_models(self):
        from lex.core.mixins.ModelModificationRestriction import (
            AdminReportsModificationRestriction,
        )
        from lex.process_admin.settings import processAdminSite

        if not hasattr(self, "untracked_models"):
            self.untracked_models = []

        read_only_restriction = AdminReportsModificationRestriction()
        self._register_static_legacy_models(processAdminSite, read_only_restriction)
        self._register_dynamic_legacy_models(processAdminSite, read_only_restriction)
