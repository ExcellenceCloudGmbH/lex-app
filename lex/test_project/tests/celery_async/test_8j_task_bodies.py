"""
Cluster 8j: Task bodies in ``lex/lex_app/celery_tasks.py``.

Intent
------

8g / 8h / 8i drove the *infrastructure* around the task decorator
(callbacks, context manager, dispatch routing, scope contracts), but
the three **actual task bodies** shipped in ``celery_tasks.py`` — the
customer-visible work units — remained dark:

* ``load_data``  (``@lex_shared_task(name="initial_data_upload")``) —
  early-return gate, storage-type branching, sync-vs-celery harness
  invocation, exception → re-raise + ``finalize_batch(failure_error=…)``,
  happy path → ``finalize_batch(failure_error=None)``.
* ``calc_and_save`` — the batch-processing loop every
  ``CalculatedModelMixin.create()`` dispatch routes to: happy path,
  ``IntegrityError`` + conflict resolution, generic exception.
* ``activate_history_version`` — the Celery Beat-driven bitemporal
  activation task: the three failure-return branches
  (``failed_model_lookup`` / ``skipped_missing_record`` /
  ``failed_too_early``) and the happy path that calls
  ``BitemporalSynchronizer.sync_record_for_model`` and flips
  ``MetaHistory`` rows ``SCHEDULED → DONE``.

No broker, no Redis
-------------------

Every scenario runs without any Celery connection.  ``@lex_shared_task``
returns an ``EnhancedTaskMethodDescriptor`` that, with ``CELERY_ACTIVE``
unset, routes directly to ``self.task(...)`` — which invokes the real
task body synchronously in the calling thread.  The wrapper's return
shape is ``(inner_result, args)``; we assert on side effects, not on
the wrapper tuple.
"""

from __future__ import annotations

import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import IntegrityError
from django.test import SimpleTestCase
from django.utils import timezone
from lex.lex_app.celery_tasks import (
    activate_history_version,
    calc_and_save,
    load_data,
)
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, CelerySyncCalc

import pytest

pytestmark = pytest.mark.celery_async


# ════════════════════════════════════════════════════════════════════════
# 8.31 – 8.35: load_data
# ════════════════════════════════════════════════════════════════════════


class TestCluster08j_LoadData(SimpleTestCase):
    """
    ``load_data`` (the ``initial_data_upload`` task body).

    Invoked by :class:`lex.lex_app.apps.LexAppConfig.ready` when the
    project's auth settings enable ``initial_data_load``.  The task
    receives a ``ProcessAdminTestCase``-shaped harness and drives it
    through one of four routes depending on ``STORAGE_TYPE`` +
    whether we're inside a Celery worker.  All four routes must
    finalize the audit batch.
    """

    def _fake_test(self):
        return SimpleNamespace(
            setUp=MagicMock(name="setUp"),
            setUpCloudStorage=MagicMock(name="setUpCloudStorage"),
            test_path=None,
        )

    # -- 8.31 ---------------------------------------------------------------
    def test_8_31_early_return_when_initial_data_load_falsy(self) -> None:
        """
        Scenario 8.31: ``initial_data_load`` falsy → task is a no-op.

        Given: ``initial_data_load=None`` (or False/"" — the default in
               projects that never opt in).
        When:  ``load_data`` fires.
        Then:  The harness' ``setUp`` / ``setUpCloudStorage`` are NOT
               called, no audit logger is built, no exception raised.

        This is the "customer never configured initial data" gate that
        keeps Celery's own beat-triggered invocations harmless on every
        cold start.
        """
        test = self._fake_test()
        with patch(
            "lex.lex_app.apps._create_audit_logger_for_task"
        ) as mock_audit_factory:
            load_data(test, [], initial_data_load=None)

        self.assertEqual(
            test.setUp.call_count, 0,
            "setUp must NOT be called when initial_data_load is falsy",
        )
        self.assertEqual(
            test.setUpCloudStorage.call_count, 0,
            "setUpCloudStorage must NOT be called when initial_data_load is falsy",
        )
        self.assertEqual(
            mock_audit_factory.call_count, 0,
            "Audit-logger factory must NOT be invoked on the early-return path",
        )

    # -- 8.32 ---------------------------------------------------------------
    def test_8_32_legacy_storage_running_in_celery_calls_setup_direct(self) -> None:
        """
        Scenario 8.32: LEGACY storage + Celery worker → ``test.setUp(audit_logger)``.

        The worker-side branch takes the direct-call path; the
        asgiref ``sync_to_async`` bridge is only for the
        sync-from-Django-request path.  ``finalize_batch`` must still
        fire on success with ``failure_error=None``.
        """
        test = self._fake_test()
        fake_audit = MagicMock(name="audit_logger")
        fake_audit.finalize_batch.return_value = {"pending_resolved": 0}

        with patch.dict(os.environ, {"STORAGE_TYPE": "LEGACY"}, clear=False), \
             patch("lex.lex_app.apps._create_audit_logger_for_task",
                   return_value=fake_audit), \
             patch("lex.lex_app.celery_tasks.is_running_in_celery",
                   return_value=True), \
             patch("lex.lex_app.celery_tasks.asyncio") as mock_asyncio:
            load_data(test, [], initial_data_load="seed.json")

        test.setUp.assert_called_once_with(fake_audit)
        self.assertEqual(
            test.setUpCloudStorage.call_count, 0,
            "LEGACY branch must NOT invoke setUpCloudStorage",
        )
        self.assertEqual(
            mock_asyncio.run.call_count, 0,
            "Worker-side LEGACY branch must call setUp directly, not via asyncio",
        )
        fake_audit.finalize_batch.assert_called_once_with(failure_error=None)
        self.assertEqual(
            test.test_path, "seed.json",
            "test.test_path must be set to the initial_data_load value before setUp",
        )

    # -- 8.33 ---------------------------------------------------------------
    def test_8_33_legacy_storage_not_in_celery_uses_asyncio(self) -> None:
        """
        Scenario 8.33: LEGACY + not-in-Celery → ``asyncio.run(sync_to_async(setUp))``.

        This is the branch that fires when the framework boot runs
        inside a synchronous Django startup (``manage.py runserver``,
        ``lex run``).  The framework wraps ``setUp`` in
        ``sync_to_async`` and runs it under ``asyncio.run`` so the
        harness can coexist with the ASGI lifespan.  We don't care
        about the coroutine mechanics — we care that ``asyncio.run``
        was invoked exactly once and that ``setUpCloudStorage`` stayed
        untouched.
        """
        test = self._fake_test()
        fake_audit = MagicMock()
        fake_audit.finalize_batch.return_value = {"pending_resolved": 0}

        with patch.dict(os.environ, {"STORAGE_TYPE": "LEGACY"}, clear=False), \
             patch("lex.lex_app.apps._create_audit_logger_for_task",
                   return_value=fake_audit), \
             patch("lex.lex_app.celery_tasks.is_running_in_celery",
                   return_value=False), \
             patch("lex.lex_app.celery_tasks.asyncio") as mock_asyncio:
            load_data(test, [], initial_data_load="seed.json")

        self.assertEqual(
            mock_asyncio.run.call_count, 1,
            "Non-Celery LEGACY path must bridge through asyncio.run exactly once",
        )
        self.assertEqual(
            test.setUpCloudStorage.call_count, 0,
            "LEGACY path never invokes setUpCloudStorage",
        )
        fake_audit.finalize_batch.assert_called_once_with(failure_error=None)

    # -- 8.34 ---------------------------------------------------------------
    def test_8_34_non_legacy_storage_with_celery_active_calls_cloud_setup(self) -> None:
        """
        Scenario 8.34: non-LEGACY storage + ``CELERY_ACTIVE=true`` →
        ``test.setUpCloudStorage(models, audit_logger)`` direct.

        Once STORAGE_TYPE leaves LEGACY (e.g. S3 / Azure Blob) the
        harness must run ``setUpCloudStorage`` with both the seeded
        model list AND the audit logger.  The ``CELERY_ACTIVE`` branch
        is the worker-side of the fan-out and must NOT wrap through
        asyncio.
        """
        test = self._fake_test()
        fake_audit = MagicMock()
        fake_audit.finalize_batch.return_value = {"pending_resolved": 0}
        models = [object(), object()]

        with patch.dict(os.environ,
                        {"STORAGE_TYPE": "S3", "CELERY_ACTIVE": "true"},
                        clear=False), \
             patch("lex.lex_app.apps._create_audit_logger_for_task",
                   return_value=fake_audit), \
             patch("lex.lex_app.celery_tasks.is_running_in_celery",
                   return_value=False), \
             patch("lex.lex_app.celery_tasks.asyncio") as mock_asyncio:
            load_data(test, models, initial_data_load="seed.json")

        test.setUpCloudStorage.assert_called_once_with(models, fake_audit)
        self.assertEqual(
            test.setUp.call_count, 0,
            "Non-LEGACY path must NOT invoke setUp (LEGACY branch only)",
        )
        self.assertEqual(
            mock_asyncio.run.call_count, 0,
            "CELERY_ACTIVE branch invokes setUpCloudStorage directly",
        )
        fake_audit.finalize_batch.assert_called_once_with(failure_error=None)

    # -- 8.34a --------------------------------------------------------------
    def test_8_34a_non_legacy_storage_with_false_celery_env_uses_asyncio(self) -> None:
        """
        Scenario 8.34a: non-LEGACY storage + ``CELERY_ACTIVE=false`` →
        ``asyncio.run(sync_to_async(setUpCloudStorage))``.

        ``CELERY_ACTIVE`` is an environment string and must be parsed as a
        boolean, not treated as truthy merely because the variable exists.
        The string ``"false"`` should behave like the non-worker path and
        avoid direct ORM work in an async startup thread.
        """
        test = self._fake_test()
        fake_audit = MagicMock()
        fake_audit.finalize_batch.return_value = {"pending_resolved": 0}
        models = [object()]

        with patch.dict(os.environ,
                        {"STORAGE_TYPE": "S3", "CELERY_ACTIVE": "false"},
                        clear=False), \
             patch("lex.lex_app.apps._create_audit_logger_for_task",
                   return_value=fake_audit), \
             patch("lex.lex_app.celery_tasks.is_running_in_celery",
                   return_value=False), \
             patch("lex.lex_app.celery_tasks.asyncio") as mock_asyncio:
            load_data(test, models, initial_data_load="seed.json")

        self.assertEqual(
            test.setUpCloudStorage.call_count, 0,
            "False-string CELERY_ACTIVE must not take the direct cloud-setup path",
        )
        self.assertEqual(
            mock_asyncio.run.call_count, 1,
            "False-string CELERY_ACTIVE must use asyncio.run for non-worker cloud setup",
        )
        fake_audit.finalize_batch.assert_called_once_with(failure_error=None)

    # -- 8.35 ---------------------------------------------------------------
    def test_8_35_exception_reraises_and_finalizes_with_failure_error(self) -> None:
        """
        Scenario 8.35: setUp raises → re-raise + ``finalize_batch(failure_error=…)``.

        If the harness blows up mid-seed, the outer Celery task must
        **both** re-raise (so the caller's ``on_failure`` callback
        fires and the worker reports the failure) AND close the audit
        batch with a ``failure_error`` string carrying the traceback.
        Leaking pending audit rows across batch boundaries is how the
        audit trail becomes useless — the ``finally`` gate in the task
        body is the contract that prevents it.
        """
        test = self._fake_test()
        boom = RuntimeError("seed crashed")
        test.setUp.side_effect = boom
        fake_audit = MagicMock()
        fake_audit.finalize_batch.return_value = {"pending_resolved": 3}

        with patch.dict(os.environ, {"STORAGE_TYPE": "LEGACY"}, clear=False), \
             patch("lex.lex_app.apps._create_audit_logger_for_task",
                   return_value=fake_audit), \
             patch("lex.lex_app.celery_tasks.is_running_in_celery",
                   return_value=True), \
             self.assertRaises(RuntimeError) as ctx:
            load_data(test, [], initial_data_load="seed.json")

        self.assertIs(
            ctx.exception, boom,
            "Original exception must propagate — framework must not swallow "
            "or rewrap setUp failures (on_failure depends on the original type)",
        )
        self.assertEqual(
            fake_audit.finalize_batch.call_count, 1,
            "finalize_batch must fire exactly once even on the crash path",
        )
        call_kwargs = fake_audit.finalize_batch.call_args.kwargs
        self.assertIn(
            "RuntimeError: seed crashed", call_kwargs["failure_error"],
            "failure_error must embed the exception type + message so the "
            "audit trail surfaces the root cause without a separate log dive",
        )

    # -- 8.36 ---------------------------------------------------------------
    def test_8_36_audit_logger_none_skips_finalize_without_crashing(self) -> None:
        """
        Scenario 8.36: ``audit_logger`` is ``None`` → ``finally`` block
        must be a no-op, not an ``AttributeError``.

        ``_create_audit_logger_for_task`` returns ``None`` when audit
        logging is disabled OR the import failed.  The ``finally``
        guard ``if audit_logger:`` is what keeps every LEGACY customer
        who never enabled audit logging from seeing a spurious
        ``AttributeError: 'NoneType' object has no attribute
        'finalize_batch'`` on every seed run.
        """
        test = self._fake_test()

        with patch.dict(os.environ, {"STORAGE_TYPE": "LEGACY"}, clear=False), \
             patch("lex.lex_app.apps._create_audit_logger_for_task",
                   return_value=None), \
             patch("lex.lex_app.celery_tasks.is_running_in_celery",
                   return_value=True):
            # Must NOT raise.
            load_data(test, [], initial_data_load="seed.json")

        test.setUp.assert_called_once_with(None)


# ════════════════════════════════════════════════════════════════════════
# 8.37 – 8.40: calc_and_save
# ════════════════════════════════════════════════════════════════════════


class TestCluster08j_CalcAndSave(E2ETestCase):
    """
    ``calc_and_save`` — the batch-processing loop.

    Every row a ``CalculatedModelMixin.create(...)`` dispatches ends up
    here.  Per-model: ``lex_func()`` → ``save()``.  On
    ``IntegrityError`` (duplicate defining-field row) the task calls
    ``delete_models_with_same_defining_fields`` to detect the existing
    row and either re-uses its pk or resets the current instance's pk
    to NULL before retrying ``save()``.  On any other exception the
    task re-raises so the outer ``on_failure`` callback can flip the
    row to ``ERROR`` + log the audit row.
    """

    e2e_models = ALL_MODELS

    # -- 8.37 ---------------------------------------------------------------
    def test_8_37_happy_path_processes_every_model(self) -> None:
        """
        Scenario 8.37: batch of N clean models → all saved, summary accurate.

        The summary dict is what the Celery return-value monitor shows
        on the worker console — if ``processed_successfully`` drifts
        from ``total_models`` on a clean batch, we've silently skipped
        rows.
        """
        a = CelerySyncCalc(name="calc-a")
        b = CelerySyncCalc(name="calc-b")
        result, _args = calc_and_save([a, b])

        self.assertEqual(result["total_models"], 2)
        self.assertEqual(
            result["processed_successfully"], 2,
            "Every clean model must be counted as processed",
        )
        self.assertEqual(result["conflicts_resolved"], 0)
        self.assertEqual(result["errors"], 0)
        self.assertIsNotNone(a.pk, "save() must have persisted row A")
        self.assertIsNotNone(b.pk, "save() must have persisted row B")

    # -- 8.38 ---------------------------------------------------------------
    def test_8_38_integrity_error_triggers_conflict_resolution(self) -> None:
        """
        Scenario 8.38: ``save()`` raises IntegrityError → conflict resolver
        reassigns pk from ``delete_models_with_same_defining_fields``' result.

        Simulated path: first ``save()`` raises, the task asks the model
        for its defining-field twin, the twin reports an existing pk,
        the task rewires the current instance's pk to that existing row
        and ``save()``s again — which succeeds because the row now
        UPDATEs instead of INSERTs.  ``conflicts_resolved`` AND
        ``processed_successfully`` both bump.
        """
        model = CelerySyncCalc(name="calc-conflict")
        # Give the instance a `delete_models_with_same_defining_fields`
        # mock that returns a distinct instance carrying the pk of the
        # pre-existing row.
        existing = CelerySyncCalc.objects.create(name="calc-conflict-preexisting")

        integrity = IntegrityError("duplicate defining_fields")
        real_save = CelerySyncCalc.save
        call_count = {"n": 0}

        def flaky_save(self, *args, **kwargs):
            call_count["n"] += 1
            if self is model and call_count["n"] == 1:
                raise integrity
            return real_save(self, *args, **kwargs)

        model.delete_models_with_same_defining_fields = MagicMock(
            return_value=existing
        )

        with patch.object(CelerySyncCalc, "save", flaky_save):
            result, _args = calc_and_save([model])

        self.assertEqual(
            model.pk, existing.pk,
            "Conflict resolver must reassign pk to the pre-existing row so "
            "the retried save UPDATEs instead of re-INSERTing",
        )
        self.assertEqual(result["conflicts_resolved"], 1)
        self.assertEqual(result["processed_successfully"], 1)
        self.assertEqual(result["errors"], 0)
        model.delete_models_with_same_defining_fields.assert_called_once()

    # -- 8.39 ---------------------------------------------------------------
    def test_8_39_generic_exception_bumps_errors_and_reraises(self) -> None:
        """
        Scenario 8.39: ``lex_func()`` raises non-IntegrityError → error
        counted and re-raised to on_failure.

        The outer ``CallbackTask.on_failure`` depends on the original
        exception to stamp ``is_calculated=ERROR`` and close the audit
        row.  The task must NOT swallow the exception — the
        ``errors += 1`` bookkeeping is a side effect, not a
        replacement for propagation.
        """
        model = CelerySyncCalc(name="calc-fail", should_fail=True)

        with self.assertRaises(RuntimeError) as ctx:
            calc_and_save([model])

        self.assertIn("failing on purpose", str(ctx.exception))
        self.assertIsNone(
            model.pk,
            "save() must NOT have run when calculate() raised",
        )


# ════════════════════════════════════════════════════════════════════════
# 8.40 – 8.43: activate_history_version
# ════════════════════════════════════════════════════════════════════════


class TestCluster08j_ActivateHistoryVersion(SimpleTestCase):
    """
    ``activate_history_version`` — Celery Beat-driven bitemporal
    activation.

    Fired when a history record's ``valid_from`` becomes current.  The
    task has four documented return strings and one happy-path
    ``"success"`` — the frontend uses these to distinguish
    "no such model" / "record deleted before activation" / "clock drift
    rejected" / "activated cleanly".  Silent ``None`` returns would
    look identical to a dead task and hide real failures.
    """

    # -- 8.40 ---------------------------------------------------------------
    def test_8_40_unknown_model_returns_failed_model_lookup(self) -> None:
        """
        Scenario 8.40: ``apps.get_model`` raises LookupError →
        ``"failed_model_lookup"`` (not an unhandled exception).

        Happens when a model was renamed/removed but Beat still has a
        stale scheduled task referencing the old label.  Must not
        crash the worker.
        """
        with patch("django.apps.apps.get_model", side_effect=LookupError("nope")):
            ret = activate_history_version("ghost_app", "GhostModel", 1)
        # @lex_shared_task returns (inner, args)
        inner, _args = ret
        self.assertEqual(inner, "failed_model_lookup")

    # -- 8.41 ---------------------------------------------------------------
    def test_8_41_missing_history_returns_skipped_missing_record(self) -> None:
        """
        Scenario 8.41: History row deleted between scheduling and firing
        → ``"skipped_missing_record"``.

        Race: user deletes a history entry while Beat has the activation
        queued.  The task must swallow the ``DoesNotExist`` and return
        the skipped sentinel so the scheduler doesn't flag it red.
        """
        fake_model = MagicMock(name="fake_model")
        # Build a DoesNotExist exception class hanging off the fake history model
        class _HDN(Exception):
            pass
        fake_model.history.model.DoesNotExist = _HDN
        fake_model.history.model.objects.get.side_effect = _HDN("gone")

        with patch("django.apps.apps.get_model", return_value=fake_model):
            ret = activate_history_version("app", "M", 99)

        inner, _ = ret
        self.assertEqual(inner, "skipped_missing_record")

    # -- 8.42 ---------------------------------------------------------------
    def test_8_42_too_early_returns_failed_too_early(self) -> None:
        """
        Scenario 8.42: ``valid_from`` is far in the future →
        ``"failed_too_early"``.

        Guards against clock drift + Beat premature-fire.  The task
        allows up to 5 seconds of drift; beyond that it refuses.  The
        sync and meta-update paths must NOT run.
        """
        fake_model = MagicMock(name="fake_model")
        class _HDN(Exception):
            pass
        fake_model.history.model.DoesNotExist = _HDN

        future_record = MagicMock(
            valid_from=timezone.now() + timedelta(hours=1),
        )
        fake_model.history.model.objects.get.return_value = future_record

        with patch("django.apps.apps.get_model", return_value=fake_model), \
             patch(
                 "lex.process_admin.utils.bitemporal_sync."
                 "BitemporalSynchronizer.sync_record_for_model"
             ) as mock_sync:
            ret = activate_history_version("app", "M", 1)

        inner, _ = ret
        self.assertEqual(inner, "failed_too_early")
        self.assertEqual(
            mock_sync.call_count, 0,
            "Too-early guard must short-circuit BEFORE sync fires",
        )

    # -- 8.43 ---------------------------------------------------------------
    def test_8_43_happy_path_syncs_and_flips_meta_to_done(self) -> None:
        """
        Scenario 8.43: valid history record → sync runs, meta rows
        ``SCHEDULED → DONE``, return ``"success"``.

        The two observable side effects the scheduler depends on:
        (a) ``BitemporalSynchronizer.sync_record_for_model`` fires
            with ``(model, pk_val, HistoryModel)``.
        (b) ``MetaModel.objects.filter(history_object_id=…,
            meta_task_status="SCHEDULED").update(meta_task_status=
            "DONE")`` — so Beat doesn't re-fire the same activation
            on the next tick.
        """
        fake_model = MagicMock(name="fake_model")
        class _HDN(Exception):
            pass
        history_model = fake_model.history.model
        history_model.DoesNotExist = _HDN
        # model._meta.pk.name → "id"
        fake_model._meta.pk.name = "id"

        due_record = MagicMock(
            valid_from=timezone.now() - timedelta(minutes=5),
            id=42,
        )
        history_model.objects.get.return_value = due_record

        meta_model = history_model.meta_history.model
        meta_filter = MagicMock()
        meta_model.objects.filter.return_value = meta_filter

        with patch("django.apps.apps.get_model", return_value=fake_model), \
             patch(
                 "lex.process_admin.utils.bitemporal_sync."
                 "BitemporalSynchronizer.sync_record_for_model"
             ) as mock_sync:
            ret = activate_history_version("app", "M", 7)

        inner, _ = ret
        self.assertEqual(inner, "success")
        mock_sync.assert_called_once_with(fake_model, 42, history_model)
        meta_model.objects.filter.assert_called_once_with(
            history_object_id=7,
            meta_task_status="SCHEDULED",
        )
        meta_filter.update.assert_called_once_with(meta_task_status="DONE")

    # -- 8.44 ---------------------------------------------------------------
    def test_8_44_unexpected_exception_reraises(self) -> None:
        """
        Scenario 8.44: sync blows up mid-activation → task re-raises.

        Anything beyond the three documented sentinel-return branches
        must propagate so Beat retries / alerts — silently swallowing
        is how bitemporal drift goes undetected for days.
        """
        fake_model = MagicMock(name="fake_model")
        class _HDN(Exception):
            pass
        history_model = fake_model.history.model
        history_model.DoesNotExist = _HDN
        fake_model._meta.pk.name = "id"

        due_record = MagicMock(
            valid_from=timezone.now() - timedelta(minutes=5), id=42,
        )
        history_model.objects.get.return_value = due_record

        with patch("django.apps.apps.get_model", return_value=fake_model), \
             patch(
                 "lex.process_admin.utils.bitemporal_sync."
                 "BitemporalSynchronizer.sync_record_for_model",
                 side_effect=RuntimeError("sync exploded"),
             ), \
             self.assertRaises(RuntimeError) as ctx:
            activate_history_version("app", "M", 7)

        self.assertIn("sync exploded", str(ctx.exception))
