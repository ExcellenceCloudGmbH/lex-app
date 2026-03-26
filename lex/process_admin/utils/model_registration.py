import asyncio
import os
from typing import List, Type, Optional

import nest_asyncio
from asgiref.sync import sync_to_async
from django.db import models
from django.contrib import admin

from lex.lex_app.simple_history_config import get_model_exclusion_reason

import logging

logger = logging.getLogger(__name__)


class ModelRegistration:
    """
    Handles registration of Django models with admin sites and history tracking.

    This class manages the registration of different types of models including:
    - HTML Report models
    - Process models
    - Standard models with bitemporal history tracking
    - CalculationModel instances with aborted calculation handling
    """

    @classmethod
    def register_models(
        cls,
        models: List[Type[models.Model]],
        untracked_models: Optional[List[str]] = None,
    ) -> None:
        """
        Register a list of Django models with appropriate admin sites and history tracking.

        Args:
            models: List of Django model classes to register
            untracked_models: Optional list of model names (lowercase) that should
                            not have history tracking. Defaults to empty list.
        """
        from lex.process_admin.settings import processAdminSite
        from lex.core.models.Process import Process
        from lex.core.models.HTMLReport import HTMLReport
        from lex.core.models.CalculationModel import CalculationModel
        from django.contrib.auth.models import User

        if untracked_models is None:
            untracked_models = []

        # Configure User model display name
        def get_username(self):
            return f"{self.first_name} {self.last_name}"

        User.add_to_class("__str__", get_username)
        processAdminSite.register([User])

        history_ok = []
        history_skipped = []
        history_failed = []

        for model in models:
            try:
                # Validate before any registration work
                if not (issubclass(model, HTMLReport) or issubclass(model, Process)):
                    _model_name = model.__name__.lower()
                    _exclusion = get_model_exclusion_reason(model)
                    _is_already_tracked = _exclusion == "Already has history tracking"
                    _will_track = (
                        (_exclusion is None or _is_already_tracked)
                        and _model_name not in (untracked_models or [])
                    )
                    cls._validate_model_definition(
                        model, will_track_history=_will_track,
                    )

                if issubclass(model, HTMLReport):
                    cls._register_html_report(model)
                elif issubclass(model, Process):
                    cls._register_process_model(model)
                elif not issubclass(model, type) and not model._meta.abstract:
                    result = cls._register_standard_model(model, untracked_models)
                    if result == "ok":
                        history_ok.append(model.__name__)
                    elif result == "skipped":
                        history_skipped.append(model.__name__)
                    elif result == "failed":
                        history_failed.append(model.__name__)

                    if issubclass(model, CalculationModel):
                        cls._handle_calculation_model_reset(model)
            except Exception as e:
                raise RuntimeError(
                    f"Failed to register model {model.__name__}: {str(e)}"
                ) from e

        # One-line summary instead of per-model print spam
        if history_ok or history_failed:
            logger.info(
                f"History tracking: {len(history_ok)} enabled, "
                f"{len(history_skipped)} skipped, {len(history_failed)} failed"
            )
        if history_ok:
            logger.debug(f"History enabled for: {', '.join(history_ok)}")
        if history_skipped:
            logger.debug(f"History skipped for: {', '.join(history_skipped)}")
        if history_failed:
            logger.warning(f"History FAILED for: {', '.join(history_failed)}")

    @classmethod
    def _register_html_report(cls, model: Type[models.Model]) -> None:
        from lex.process_admin.settings import processAdminSite

        model_name = model.__name__.lower()
        processAdminSite.registerHTMLReport(model_name, model)
        processAdminSite.register([model])

    @classmethod
    def _register_process_model(cls, model: Type[models.Model]) -> None:
        from lex.process_admin.settings import processAdminSite

        model_name = model.__name__.lower()
        processAdminSite.registerProcess(model_name, model)
        processAdminSite.register([model])

    # ── Reserved names managed by the framework ──
    # Class names that user models must not use.
    _RESERVED_CLASS_NAMES = frozenset({
        'LexModel', 'LexManager', 'CalculationModel',
        'History', 'MetaHistory', 'StandardHistory',
        'Process', 'HTMLReport', 'PermissionResult', 'UserContext',
    })

    # Prefixes that collide with auto-generated history model names.
    _RESERVED_CLASS_PREFIXES = ('Historical', 'MetaHistorical', 'Meta')

    # ── Dynamic reserved-field introspection ──
    # Instead of hardcoding field names, we pull them from the actual
    # framework classes so that implementation changes stay in sync.

    @classmethod
    def _get_base_model_fields(cls) -> frozenset:
        """Fields defined on LexModel (always reserved for every user model)."""
        from lex.core.models.LexModel import LexModel
        return frozenset(
            f.name for f in LexModel._meta.local_fields
            if f.name != 'id'
        )

    @classmethod
    def _get_calculation_model_fields(cls) -> frozenset:
        """Extra fields added by CalculationModel on top of LexModel."""
        from lex.core.models.CalculationModel import CalculationModel
        from lex.core.models.LexModel import LexModel
        calc_fields = {f.name for f in CalculationModel._meta.local_fields}
        base_fields = {f.name for f in LexModel._meta.local_fields}
        return frozenset(calc_fields - base_fields)

    @classmethod
    def _get_history_fields(cls) -> frozenset:
        """Control fields injected by StandardHistory (Level 1 — valid time)."""
        from lex.core.services.StandardHistory import StandardHistory
        from django.contrib.auth.models import User  # any concrete model works
        history = StandardHistory()
        extra = history.get_extra_fields(model=User, fields={})
        return frozenset(extra.keys())

    @classmethod
    def _get_meta_history_fields(cls) -> frozenset:
        """Control fields injected by MetaLevelHistoricalRecords (Level 2 — system time)."""
        from lex.core.services.MetaHistory import MetaLevelHistoricalRecords
        from django.contrib.auth.models import User  # any concrete model works
        meta = MetaLevelHistoricalRecords()
        extra = meta.get_extra_fields(model=User, fields={})
        return frozenset(extra.keys())

    @classmethod
    def _get_reserved_field_names(cls, *, include_history: bool = True) -> frozenset:
        """
        Dynamically compute the set of reserved field names.

        Args:
            include_history: When False, history (Level 1) and meta-history
                (Level 2) fields are excluded.  This is used for models that
                will not have bitemporal history tracking.
        """
        reserved = set(cls._get_base_model_fields())
        reserved |= cls._get_calculation_model_fields()
        if include_history:
            reserved |= cls._get_history_fields()
            reserved |= cls._get_meta_history_fields()
        # Remove property/non-field entries that leak from get_extra_fields
        reserved.discard('instance')
        reserved.discard('instance_type')
        return frozenset(reserved)

    @classmethod
    def _validate_model_definition(
        cls,
        model: Type[models.Model],
        *,
        will_track_history: bool = True,
    ) -> None:
        """
        Ensure a user-defined model does not collide with framework-managed
        class names or field names.

        Args:
            model: The Django model class to validate.
            will_track_history: Whether this model will have bitemporal
                history tracking.  When False, history / meta-history
                field names are **not** treated as reserved.

        Raises ``ValueError`` with an actionable message on violation.
        """
        name = model.__name__

        # 1. Exact reserved class names
        if name in cls._RESERVED_CLASS_NAMES:
            raise ValueError(
                f"Model name '{name}' is reserved by the LEX framework. "
                f"Please choose a different class name."
            )

        # 2. Prefixes used by auto-generated history models
        for prefix in cls._RESERVED_CLASS_PREFIXES:
            if name.startswith(prefix):
                raise ValueError(
                    f"Model name '{name}' must not start with '{prefix}' — "
                    f"that prefix is reserved for auto-generated history models."
                )

        # 3. Reserved field names — dynamically computed
        reserved = cls._get_reserved_field_names(
            include_history=will_track_history,
        )

        #    Only check concrete fields explicitly declared on *this* class,
        #    not inherited ones from LexModel/CalculationModel.
        own_fields = {
            f.name
            for f in model._meta.local_fields
            if f.name in reserved
        }
        # Exclude fields that actually come from an abstract parent the user
        # subclassed (LexModel, CalculationModel) — those are expected.
        inherited_fields: set = set()
        for parent in model.__mro__[1:]:
            if parent is model:
                continue
            meta = getattr(parent, '_meta', None)
            if meta and getattr(meta, 'abstract', False):
                inherited_fields |= {f.name for f in meta.local_fields}

        user_conflicts = own_fields - inherited_fields
        if user_conflicts:
            raise ValueError(
                f"Model '{name}' declares reserved field(s): "
                f"{', '.join(sorted(user_conflicts))}. "
                f"These are automatically managed by the LEX framework's "
                f"history tracking and must not be redefined."
            )

    @classmethod
    def _register_standard_model(
        cls, model: Type[models.Model], untracked_models: List[str]
    ) -> str:
        """
        Register a standard model with both admin sites and bitemporal history.

        Returns:
            'ok' if history was enabled, 'skipped' if excluded, 'failed' on error.
        """
        from lex.process_admin.settings import processAdminSite, adminSite
        from simple_history import register
        from simple_history.exceptions import MultipleRegistrationsError
        from lex.core.services.StandardHistory import StandardHistory
        from lex.core.services.MetaHistory import MetaLevelHistoricalRecords
        from lex.core.services.signal_registry import connect_bitemporal_signals

        model_name = model.__name__.lower()
        result = "skipped"
        processAdminSite.register([model])

        exclusion_reason = get_model_exclusion_reason(model)
        is_already_tracked = exclusion_reason == "Already has history tracking"
        should_track = (
            (exclusion_reason is None or is_already_tracked)
            and model_name not in untracked_models
        )

        if should_track:
            try:
                # ── Level 1: Standard History ──
                try:
                    register(model, records_class=StandardHistory)
                except MultipleRegistrationsError:
                    pass

                if not hasattr(model, "history"):
                    logger.error(
                        f"Failed to retrieve history model for {model.__name__} "
                        f"after registration attempt."
                    )
                    return "failed"

                historical_model = model.history.model
                processAdminSite.register([historical_model])

                # ── Level 2: Meta History ──
                history = MetaLevelHistoricalRecords(
                    app=model._meta.app_label,
                    table_name=f"{model._meta.db_table}_meta_history",
                    custom_model_name=lambda x: f"Meta{x}",
                )

                try:
                    history.contribute_to_class(historical_model, "meta_history")
                except MultipleRegistrationsError:
                    pass
                except Exception as e:
                    if not hasattr(historical_model, "meta_history"):
                        logger.warning(
                            f"Could not attach meta_history to "
                            f"{historical_model.__name__}: {e}"
                        )

                try:
                    history.finalize(sender=historical_model)
                except Exception:
                    pass

                meta_historical_model = historical_model.meta_history.model

                # ── Connect all bitemporal signals ──
                connect_bitemporal_signals(
                    main_model=model,
                    historical_model=historical_model,
                    meta_historical_model=meta_historical_model,
                )

                processAdminSite.register([meta_historical_model])
                result = "ok"

            except Exception as e:
                logger.warning(
                    f"Failed to register history for {model.__name__}: "
                    f"{type(e).__name__} - {e}"
                )
                result = "failed"
        else:
            reason = exclusion_reason or "In untracked_models list"
            logger.debug(
                f"History tracking skipped for {model.__name__}: {reason}"
            )

        if not adminSite.is_registered(model):
            try:
                adminSite.register([model])
            except admin.exceptions.AlreadyRegistered:
                pass

        return result

    @classmethod
    def _handle_calculation_model_reset(cls, model: Type[models.Model]) -> None:
        """
        Reset CalculationModel instances left in IN_PROGRESS state on startup.
        """
        from lex.core.models.CalculationModel import CalculationModel

        if not os.getenv("CALLED_FROM_START_COMMAND"):
            return

        @sync_to_async
        def reset_instances_with_aborted_calculations():
            aborted = model.objects.filter(
                is_calculated=CalculationModel.IN_PROGRESS
            )
            aborted.update(is_calculated=CalculationModel.ABORTED)

        nest_asyncio.apply()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(reset_instances_with_aborted_calculations())

    @classmethod
    def register_model_structure(
        cls,
        structure: dict,
        auto_include_missing_models: bool = True,
    ):
        from lex.process_admin.settings import processAdminSite
        if structure is not None:
            processAdminSite.register_model_structure(
                structure,
                auto_include_missing_models=auto_include_missing_models,
            )

    @classmethod
    def register_model_styling(cls, styling: dict):
        from lex.process_admin.settings import processAdminSite
        if styling:
            processAdminSite.register_model_styling(styling)

    @classmethod
    def register_widget_structure(cls, structure):
        from lex.process_admin.settings import processAdminSite
        if structure:
            processAdminSite.register_widget_structure(structure)
