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

        for model in models:
            try:
                if issubclass(model, HTMLReport):
                    cls._register_html_report(model)
                elif issubclass(model, Process):
                    cls._register_process_model(model)
                elif not issubclass(model, type) and not model._meta.abstract:
                    cls._register_standard_model(model, untracked_models)

                    if issubclass(model, CalculationModel):
                        cls._handle_calculation_model_reset(model)
            except Exception as e:
                raise RuntimeError(
                    f"Failed to register model {model.__name__}: {str(e)}"
                ) from e

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

    @classmethod
    def _register_standard_model(
        cls, model: Type[models.Model], untracked_models: List[str]
    ) -> None:
        """
        Register a standard model with both admin sites and bitemporal history.

        This sets up the full 3-layer architecture:
          1. Level 1: Standard History  (valid_from / valid_to)
          2. Level 2: Meta History      (sys_from / sys_to)
          3. Signal handlers for chaining, sync, and scheduling
        """
        from lex.process_admin.settings import processAdminSite, adminSite
        from simple_history import register
        from simple_history.exceptions import MultipleRegistrationsError
        from lex.core.services.StandardHistory import StandardHistory
        from lex.core.services.MetaHistory import MetaLevelHistoricalRecords
        from lex.core.services.signal_registry import connect_bitemporal_signals

        model_name = model.__name__.lower()
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
                    return

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

                if is_already_tracked:
                    print(
                        f"✓ History hooks attached to existing history "
                        f"for: {model.__name__}"
                    )
                else:
                    print(
                        f"✓ History and Meta-History enabled "
                        f"for: {model.__name__}"
                    )

            except Exception as e:
                print(
                    f"⚠ Failed to register history for {model.__name__}: "
                    f"{type(e).__name__} - {e}"
                )
        else:
            exclusion_reason = get_model_exclusion_reason(model)
            if exclusion_reason:
                print(
                    f"⊘ History tracking skipped for {model.__name__}: "
                    f"{exclusion_reason}"
                )
            elif model_name in untracked_models:
                print(
                    f"⊘ History tracking skipped for {model.__name__}: "
                    f"In untracked_models list"
                )

        if not adminSite.is_registered(model):
            try:
                adminSite.register([model])
            except admin.exceptions.AlreadyRegistered:
                pass

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
    def register_model_structure(cls, structure: dict):
        from lex.process_admin.settings import processAdminSite
        if structure:
            processAdminSite.register_model_structure(structure)

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
