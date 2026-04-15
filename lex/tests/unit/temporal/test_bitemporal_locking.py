from contextlib import nullcontext
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from lex.core.services.MetaHistory import create_meta_history_record
from lex.core.services.bitemporal_signals import (
    on_history_saved__chain_valid_to,
    on_history_saved__sync_main_table,
    on_meta_saved__chain_sys_to,
)


def _build_main_model():
    return SimpleNamespace(
        _meta=SimpleNamespace(
            pk=SimpleNamespace(name="id"),
            app_label="core",
        )
    )


class HistoryChainLockingTests(TestCase):
    def test_chain_valid_to_uses_select_for_update(self):
        manager = MagicMock()
        queryset = MagicMock()
        manager.select_for_update.return_value = queryset
        queryset.filter.return_value = queryset
        queryset.order_by.return_value = queryset

        first = SimpleNamespace(valid_from=1, valid_to=None, history_id=10, save=MagicMock())
        second = SimpleNamespace(valid_from=2, valid_to=None, history_id=20, save=MagicMock())
        queryset.__iter__.return_value = iter([first, second])

        historical_model = type("HistoricalModel", (), {"objects": manager})
        history_instance = historical_model()
        history_instance.id = 99

        with patch("lex.core.services.bitemporal_signals.transaction.atomic", return_value=nullcontext()):
            on_history_saved__chain_valid_to(
                sender=historical_model,
                instance=history_instance,
                main_model=_build_main_model(),
                historical_model=historical_model,
            )

        manager.select_for_update.assert_called_once()
        queryset.filter.assert_called_once_with(id=99)
        queryset.order_by.assert_called_once_with("valid_from", "history_id")
        first.save.assert_called_once_with(update_fields=["valid_to"])
        self.assertEqual(first.valid_to, 2)
        second.save.assert_not_called()

    def test_sync_main_runs_inline(self):
        main_model = _build_main_model()
        historical_model = object()
        instance = SimpleNamespace(id=55)

        with patch("lex.process_admin.utils.bitemporal_sync.BitemporalSynchronizer.sync_record_for_model") as sync_mock:
            on_history_saved__sync_main_table(
                sender=historical_model,
                instance=instance,
                main_model=main_model,
                historical_model=historical_model,
            )

        sync_mock.assert_called_once_with(main_model, 55, historical_model)

    def test_chain_valid_to_suppresses_reentrant_save(self):
        manager = MagicMock()
        queryset = MagicMock()
        manager.select_for_update.return_value = queryset
        queryset.filter.return_value = queryset
        queryset.order_by.return_value = queryset

        first = SimpleNamespace(valid_from=1, valid_to=None, history_id=10)
        second = SimpleNamespace(valid_from=2, valid_to=None, history_id=20)
        queryset.__iter__.return_value = iter([first, second])

        historical_model = type("HistoricalModel", (), {"objects": manager})
        history_instance = historical_model()
        history_instance.id = 99
        main_model = _build_main_model()

        def reentrant_save(**kwargs):
            on_history_saved__chain_valid_to(
                sender=historical_model,
                instance=history_instance,
                main_model=main_model,
                historical_model=historical_model,
            )

        first.save = MagicMock(side_effect=reentrant_save)
        second.save = MagicMock()

        with patch("lex.core.services.bitemporal_signals.transaction.atomic", return_value=nullcontext()):
            on_history_saved__chain_valid_to(
                sender=historical_model,
                instance=history_instance,
                main_model=main_model,
                historical_model=historical_model,
            )

        # Re-entrant invocation should no-op while internal chaining is active.
        first.save.assert_called_once_with(update_fields=["valid_to"])

    def test_chain_valid_to_dedupes_post_create_after_created_post_save(self):
        manager = MagicMock()
        queryset = MagicMock()
        manager.select_for_update.return_value = queryset
        queryset.filter.return_value = queryset
        queryset.order_by.return_value = queryset
        queryset.first.side_effect = [
            SimpleNamespace(valid_from=1, valid_to=None, history_id=10, save=MagicMock()),
            None,
        ]

        historical_model = type("HistoricalModel", (), {"objects": manager})
        history_instance = historical_model()
        history_instance.id = 99
        history_instance.valid_from = 2
        history_instance.valid_to = None
        history_instance.history_id = 20
        history_instance.save = MagicMock()
        main_model = _build_main_model()

        with patch("lex.core.services.bitemporal_signals.transaction.atomic", return_value=nullcontext()):
            on_history_saved__chain_valid_to(
                sender=historical_model,
                instance=history_instance,
                main_model=main_model,
                historical_model=historical_model,
                created=True,
            )

        self.assertTrue(getattr(history_instance, "_bt_history_chain_processed", False))

        manager.select_for_update.reset_mock()
        history_instance.save.reset_mock()

        with patch("lex.core.services.bitemporal_signals.transaction.atomic", return_value=nullcontext()):
            on_history_saved__chain_valid_to(
                sender=historical_model,
                instance=SimpleNamespace(),
                history_instance=history_instance,
                main_model=main_model,
                historical_model=historical_model,
            )

        manager.select_for_update.assert_not_called()
        history_instance.save.assert_not_called()
        self.assertFalse(hasattr(history_instance, "_bt_history_chain_processed"))


class MetaChainLockingTests(TestCase):
    def test_chain_sys_to_uses_select_for_update(self):
        manager = MagicMock()
        queryset = MagicMock()
        manager.select_for_update.return_value = queryset
        queryset.filter.return_value = queryset
        queryset.order_by.return_value = queryset

        first = SimpleNamespace(pk=1, sys_from=10, sys_to=None)
        second = SimpleNamespace(pk=2, sys_from=20, sys_to=None)
        queryset.__iter__.return_value = iter([first, second])

        meta_model = type("MetaModel", (), {"objects": manager})
        instance = meta_model()
        instance.history_object_id = 9

        with patch("lex.core.services.bitemporal_signals.transaction.atomic", return_value=nullcontext()):
            on_meta_saved__chain_sys_to(
                sender=meta_model,
                instance=instance,
                meta_historical_model=meta_model,
            )

        manager.select_for_update.assert_called_once()
        queryset.filter.assert_called_once_with(history_object_id=9)
        queryset.order_by.assert_called_once_with("sys_from", "meta_history_id")
        self.assertEqual(first.sys_to, 20)
        manager.bulk_update.assert_called_once_with([first], ["sys_to"])

    def test_chain_sys_to_suppresses_reentrant_save(self):
        manager = MagicMock()
        queryset = MagicMock()
        manager.select_for_update.return_value = queryset
        queryset.filter.return_value = queryset
        queryset.order_by.return_value = queryset

        first = SimpleNamespace(pk=1, sys_from=10, sys_to=None)
        second = SimpleNamespace(pk=2, sys_from=20, sys_to=None)
        queryset.__iter__.return_value = iter([first, second])

        meta_model = type("MetaModel", (), {"objects": manager})
        instance = meta_model()
        instance.history_object_id = 9

        with patch("lex.core.services.bitemporal_signals.transaction.atomic", return_value=nullcontext()):
            on_meta_saved__chain_sys_to(
                sender=meta_model,
                instance=instance,
                meta_historical_model=meta_model,
            )

        manager.bulk_update.assert_called_once_with([first], ["sys_to"])

    def test_chain_sys_to_skips_non_chain_updates(self):
        manager = MagicMock()
        meta_model = type("MetaModel", (), {"objects": manager})
        instance = meta_model()
        instance.history_object_id = 9

        on_meta_saved__chain_sys_to(
            sender=meta_model,
            instance=instance,
            meta_historical_model=meta_model,
            created=False,
            update_fields={"name"},
        )

        manager.select_for_update.assert_not_called()
        manager.bulk_update.assert_not_called()


class MetaStrictChainingTests(TestCase):
    def test_strict_chaining_update_locks_latest_meta_row(self):
        manager = MagicMock()
        queryset = MagicMock()
        manager.select_for_update.return_value = queryset
        queryset.filter.return_value = queryset
        queryset.order_by.return_value = queryset

        latest = SimpleNamespace(sys_to=None, foo=None, save=MagicMock())
        queryset.first.return_value = latest

        tracked_field = SimpleNamespace(attname="foo")
        meta_model = type(
            "MetaModel",
            (),
            {
                "objects": manager,
                "tracked_fields": [tracked_field],
            },
        )

        meta_manager = SimpleNamespace(model=meta_model)
        history_instance = SimpleNamespace(_strict_chaining_update=True, foo="bar")

        with patch("lex.core.services.MetaHistory.transaction.atomic", return_value=nullcontext()):
            result = create_meta_history_record(
                meta_manager=meta_manager,
                instance=history_instance,
                history_type="~",
            )

        manager.select_for_update.assert_called_once()
        queryset.filter.assert_called_once_with(history_object=history_instance)
        queryset.order_by.assert_called_once_with("-sys_from", "-meta_history_id")
        latest.save.assert_called_once()
        self.assertEqual(latest.foo, "bar")
        self.assertIs(result, latest)
