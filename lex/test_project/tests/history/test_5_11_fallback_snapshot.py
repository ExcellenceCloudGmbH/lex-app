"""
Cluster 5.11: History fallback-snapshot path.

Targets ``HistoryModelEntry._get_snapshot`` (lines 180–201 of
``lex/api/views/model_entries/History.py``) — the branch that fires
when a model-container has no registered ``serializers_map['default']``
and the view falls back to per-field manual serialization.

Baseline coverage before this scenario: 62.71%. Lines 180–201 were
entirely uncovered because every ``LexModel`` in the test project
registers a default serializer, so the fallback path is never exercised
by the end-to-end 5c scenarios.

Intent (from the view docstring + docs/features/history/):

    Even when no serializer is registered, ``/history/`` must still
    return a sane snapshot for every history row:

      * System-managed bitemporal columns (``history_id``,
        ``valid_from``, ``valid_to``, ``sys_from``, ``meta_task_*``, …)
        must never appear in the snapshot — they're already surfaced
        on the envelope level.
      * Datetimes / dates are ISO-formatted so the frontend can parse
        them without guessing the type.
      * Primitive-ish values (``str``, ``int``, ``float``, ``bool``,
        ``None``, ``list``, ``dict``) pass through unchanged.
      * Anything else is coerced to ``str()`` — never a raw Python
        object that DRF can't JSON-encode.

Why not drive the full endpoint: the route requires a
``model_container`` with a registered default serializer, which
guarantees we take the branch at line 176 (``if serializer_class:``)
and never reach the fallback. Unit-testing ``_get_snapshot`` directly
is the only way to cover the uncovered lines without hand-editing
every test-project model's container registration.

Scenario numbering matches
docs/test-plan/test-clusters.md § Planned Expansions → 5.11.
"""

from __future__ import annotations

import datetime as _dt
import unittest
from types import SimpleNamespace

from django.test import SimpleTestCase

from lex.api.views.model_entries.History import HistoryModelEntry


class _FakeField:
    """Minimal stand-in for ``Field`` — ``_get_snapshot`` only reads
    ``.name`` off each entry of ``record.__class__._meta.fields``."""

    def __init__(self, name: str) -> None:
        self.name = name


def _make_record(field_names, **values):
    """Build a synthetic history record shaped like what
    ``_get_snapshot`` reads: ``record.__class__._meta.fields`` yields
    objects with ``.name``, and ``getattr(record, name)`` returns the
    per-field value. Python forbids setting ``__class__`` to a non-type,
    so we create a real class per record via ``type()``."""
    class _Meta:
        fields = [_FakeField(n) for n in field_names]

    Record = type("FakeHistoryRecord", (), {"_meta": _Meta})
    rec = Record()
    for k, v in values.items():
        setattr(rec, k, v)
    return rec


class _CustomValue:
    """A non-primitive whose ``str()`` is stable and loud so we can
    assert the fallback coercion path was taken."""

    def __str__(self) -> str:
        return "<custom-value>"


class TestCluster05_11_FallbackSnapshot(SimpleTestCase):
    """
    Scenario 5.11: ``_get_snapshot`` with no serializer class exercises
    the fallback branch — CONTROL_FIELDS skipped, ``isoformat`` for
    datetime/date, ``str()`` coercion for exotic objects, primitives
    and containers unchanged.
    """

    def _snapshot(self, record):
        # ``HistoryModelEntry`` is a DRF ListAPIView subclass but
        # ``_get_snapshot`` is a plain method — instantiate without
        # wiring a request.
        view = HistoryModelEntry()
        return view._get_snapshot(record, serializer_class=None)

    # -- 5.11 ----------------------------------------------------------
    def test_5_11_fallback_snapshot_contract(self) -> None:
        """
        One scenario, five branches (each one an explicit sub-assertion
        so a regression surfaces the exact drift, not a generic diff):

          a. ``CONTROL_FIELDS`` entries are filtered out even when they
             carry real values — otherwise the frontend would see
             duplicated system columns inside ``snapshot``.
          b. A ``datetime`` is ISO-formatted (exact ``str`` round-trip
             via ``.isoformat()``).
          c. A ``date`` is ISO-formatted via the same branch.
          d. A non-JSON-safe object is coerced through ``str()`` so
             DRF's ``Response`` encoder doesn't blow up at render time.
          e. Primitives (int / bool / None) and containers (list, dict)
             pass through unchanged.
        """
        now = _dt.datetime(2026, 4, 21, 15, 30, 45)
        today = _dt.date(2026, 4, 21)

        record = _make_record(
            field_names=[
                # control fields — must be skipped even though populated
                "history_id", "valid_from", "history_type",
                "history_user_id", "sys_from", "meta_task_status",
                # business fields
                "amount",         # int passthrough
                "is_active",      # bool passthrough
                "note",           # str passthrough
                "nothing",        # None passthrough
                "tags",           # list passthrough
                "extra",          # dict passthrough
                "created",        # datetime → isoformat
                "due",            # date → isoformat
                "custom",         # non-primitive → str(...)
            ],
            history_id=999, valid_from=now, history_type="+",
            history_user_id=7, sys_from=now, meta_task_status="SUCCESS",
            amount=42, is_active=True, note="hello",
            nothing=None,
            tags=["a", "b"],
            extra={"k": "v"},
            created=now,
            due=today,
            custom=_CustomValue(),
        )

        snap = self._snapshot(record)

        # (a) CONTROL_FIELDS filtered
        for control in (
            "history_id", "valid_from", "history_type",
            "history_user_id", "sys_from", "meta_task_status",
        ):
            self.assertNotIn(
                control, snap,
                msg=(
                    f"Control field {control!r} leaked into the "
                    "snapshot payload — the frontend would render it "
                    "as a business column. Check CONTROL_FIELDS in "
                    "History._get_snapshot."
                ),
            )

        # (b) datetime → ISO string
        self.assertEqual(
            snap["created"], now.isoformat(),
            msg=(
                "Datetime value must be ISO-formatted so the frontend "
                "can parse without guessing. "
                f"Expected {now.isoformat()!r}; got {snap['created']!r}"
            ),
        )

        # (c) date → ISO string
        self.assertEqual(
            snap["due"], today.isoformat(),
            msg=(
                "Date value must be ISO-formatted. "
                f"Expected {today.isoformat()!r}; got {snap['due']!r}"
            ),
        )

        # (d) non-primitive → str(...)
        self.assertEqual(
            snap["custom"], "<custom-value>",
            msg=(
                "Non-primitive field values must be coerced via str() "
                "so DRF's JSON encoder doesn't crash at response time. "
                f"Got {snap['custom']!r}"
            ),
        )

        # (e) primitives + containers pass through
        self.assertEqual(snap["amount"], 42)
        self.assertIs(snap["is_active"], True)
        self.assertEqual(snap["note"], "hello")
        self.assertIsNone(snap["nothing"])
        self.assertEqual(snap["tags"], ["a", "b"])
        self.assertEqual(snap["extra"], {"k": "v"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


