"""
Signal Registry — wires up all bitemporal signal handlers for a model triple.

Usage (called from ``ModelRegistration._register_standard_model``):

    connect_bitemporal_signals(
        main_model=MyModel,
        historical_model=HistoricalMyModel,
        meta_historical_model=MetaHistoricalMyModel,
    )
"""

import logging
from functools import partial

from django.db.models.signals import post_save, pre_delete, post_delete
from simple_history.signals import post_create_historical_record

from lex.core.services.bitemporal_signals import (
    on_history_saved__chain_valid_to,
    on_history_saved__create_meta,
    on_history_saved__sync_main_table,
    on_history_pre_delete__cancel_schedules,
    on_history_post_delete__repair_chain,
    on_meta_saved__chain_sys_to,
)

logger = logging.getLogger(__name__)


def connect_bitemporal_signals(
    main_model, historical_model, meta_historical_model
):
    """
    Connect all 6 bitemporal signal handlers for a (main, history, meta) triple.

    Uses ``functools.partial`` to bind the model references as keyword
    arguments, replacing the old closure-capture approach.
    """
    model_name = main_model.__name__.lower()

    # ── Build bound handlers ──

    chain_valid_to = partial(
        on_history_saved__chain_valid_to,
        main_model=main_model,
        historical_model=historical_model,
    )

    create_meta = partial(
        on_history_saved__create_meta,
        main_model=main_model,
        historical_model=historical_model,
        model_name=model_name,
    )

    sync_main = partial(
        on_history_saved__sync_main_table,
        main_model=main_model,
        historical_model=historical_model,
    )

    cancel_schedules = partial(
        on_history_pre_delete__cancel_schedules,
        historical_model=historical_model,
    )

    repair_chain = partial(
        on_history_post_delete__repair_chain,
        main_model=main_model,
        historical_model=historical_model,
    )

    chain_sys_to = partial(
        on_meta_saved__chain_sys_to,
        meta_historical_model=meta_historical_model,
    )

    # ── Disconnect first (idempotent re-registration) ──

    post_create_historical_record.disconnect(
        dispatch_uid=f"bt_chain_valid_to_{model_name}", sender=historical_model
    )
    post_create_historical_record.disconnect(
        dispatch_uid=f"bt_create_meta_pchr_{model_name}", sender=historical_model
    )

    post_save.disconnect(
        dispatch_uid=f"bt_create_meta_{model_name}", sender=historical_model
    )
    post_save.disconnect(
        dispatch_uid=f"bt_chain_valid_to_ps_{model_name}", sender=historical_model
    )
    post_save.disconnect(
        dispatch_uid=f"bt_sync_main_{model_name}", sender=historical_model
    )
    post_save.disconnect(
        dispatch_uid=f"bt_chain_sys_to_{model_name}", sender=meta_historical_model
    )

    pre_delete.disconnect(
        dispatch_uid=f"bt_cancel_sched_{model_name}", sender=historical_model
    )
    post_delete.disconnect(
        dispatch_uid=f"bt_repair_chain_{model_name}", sender=historical_model
    )

    # ── Connect ──

    # post_save on History (Level 1)
    post_save.connect(
        create_meta, sender=historical_model, weak=False,
        dispatch_uid=f"bt_create_meta_{model_name}",
    )
    post_save.connect(
        chain_valid_to, sender=historical_model, weak=False,
        dispatch_uid=f"bt_chain_valid_to_ps_{model_name}",
    )
    post_save.connect(
        sync_main, sender=historical_model, weak=False,
        dispatch_uid=f"bt_sync_main_{model_name}",
    )

    # post_create_historical_record for standard flow chaining
    post_create_historical_record.connect(
        chain_valid_to, sender=historical_model, weak=False,
        dispatch_uid=f"bt_chain_valid_to_{model_name}",
    )

    # pre_delete / post_delete on History
    pre_delete.connect(
        cancel_schedules, sender=historical_model, weak=False,
        dispatch_uid=f"bt_cancel_sched_{model_name}",
    )
    post_delete.connect(
        repair_chain, sender=historical_model, weak=False,
        dispatch_uid=f"bt_repair_chain_{model_name}",
    )

    # post_save on MetaHistory (Level 2)
    post_save.connect(
        chain_sys_to, sender=meta_historical_model, weak=False,
        dispatch_uid=f"bt_chain_sys_to_{model_name}",
    )
