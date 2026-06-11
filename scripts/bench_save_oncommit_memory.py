"""Benchmark: per-save ``transaction.on_commit`` retention vs plain save.

Reproduces the v1 -> v2 memory regression mechanism in isolation, at scale
(up to 1,000,000 "saves") in seconds, with no heavy framework dependency.

WHAT THIS MODELS
----------------
v2's ``LexModel.save()`` ends every save with (verified, file:line):

    lex/core/models/LexModel.py:532
        transaction.on_commit(self._reset_initial_state)

``self._reset_initial_state`` is a *bound method*, so it holds a strong
reference to the model instance. django-lifecycle also gives every instance a
per-instance snapshot of all its fields:

    django_lifecycle/mixins.py:109   self._initial_state = ModelState.from_instance(self)
    django_lifecycle/mixins.py:144   def _reset_initial_state(self): ...

A whole calculation runs inside ONE outer transaction:

    lex/core/models/CalculationModel.py:1059
        with transaction.atomic():
            func()            # <- all the .save() calls happen in here

Django queues every ``on_commit`` callback in ``connection.run_on_commit`` and
only fires (and clears) them when the OUTERMOST atomic commits -- i.e. at the
very end of the calc. So N saves => N closures, each pinning its instance (and
its ``_initial_state`` snapshot) alive for the entire run. That is O(N-saves)
Python memory that v1's plain ``Model.save()`` never had.

v1 (old lex-generic-app) ``CalculatedModelMixin`` extends plain
``django.db.models.Model`` and its ``calc_and_save`` is just
``model.calculate(); model.save()`` -- no snapshot, no on_commit, so each
instance is collectable the moment the loop moves on.

THE COMPARISON
--------------
For each N we run two modes inside one ``transaction.atomic()`` and measure the
resident-set (RSS) growth of the process:

  * ``v1`` : build instance, (plain) -- drop reference each iteration.
  * ``v2`` : build instance + ``_initial_state`` snapshot, then
             ``transaction.on_commit(inst._reset_initial_state)`` -- the pin.

We also print ``len(connection.run_on_commit)`` so you can see it grow exactly
1:1 with the number of saves -- that list IS the leak.

RUN
---
    .venv-test/bin/python scripts/bench_save_oncommit_memory.py
    .venv-test/bin/python scripts/bench_save_oncommit_memory.py 50000 200000 1000000
"""

from __future__ import annotations

import gc
import sys

import django
from django.conf import settings


def _bootstrap_django() -> None:
    if settings.configured:
        return
    settings.configure(
        DEBUG=False,  # DEBUG=True would make connection.queries accumulate too
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[],
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()


_bootstrap_django()

from datetime import date  # noqa: E402
from decimal import Decimal  # noqa: E402

from django.db import connection, models, transaction  # noqa: E402


class MiniInvoice(models.Model):
    """Footprint roughly mirrors the stress-lab ``StressInvoice``."""

    invoice_number = models.CharField(max_length=64)
    counterparty_id = models.IntegerField()
    period_id = models.IntegerField()
    booked_on = models.DateField()
    due_on = models.DateField()
    amount_net = models.DecimalField(max_digits=14, decimal_places=2)
    amount_tax = models.DecimalField(max_digits=14, decimal_places=2)
    amount_gross = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)
    category = models.CharField(max_length=16)
    is_posted = models.BooleanField(default=True)

    class Meta:
        app_label = "bench_oncommit"

    # --- mirror django-lifecycle's per-instance snapshot (mixins.py:111) ---
    def _snapshot_state(self) -> dict:
        return {
            f.attname: getattr(self, f.attname)
            for f in self._meta.concrete_fields
        }

    # --- mirror LexModel/django-lifecycle reset (mixins.py:144) ---
    def _reset_initial_state(self):
        self._initial_state = self._snapshot_state()


def _rss_mb() -> float:
    """Point-in-time resident set size in MB (Linux /proc)."""
    try:
        with open("/proc/self/statm") as fh:
            pages = int(fh.read().split()[1])  # resident pages
        import os

        return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except Exception:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _make(i: int) -> MiniInvoice:
    return MiniInvoice(
        invoice_number=f"INV-{i:08d}",
        counterparty_id=i % 200,
        period_id=i % 12,
        booked_on=date(2025, 1, 1),
        due_on=date(2025, 2, 1),
        amount_net=Decimal("100.00"),
        amount_tax=Decimal("20.00"),
        amount_gross=Decimal("120.00"),
        currency="EUR",
        category="standard",
        is_posted=True,
    )


def run_mode(mode: str, n: int) -> tuple[float, int]:
    """Return (RSS growth in MB, len(run_on_commit)) for ``n`` saves."""
    gc.collect()
    base = _rss_mb()
    peak = base
    oncommit_len = 0

    # One outer transaction wraps the whole "calculation", exactly like
    # CalculationModel.execute_calculation_sync (CalculationModel.py:1059).
    with transaction.atomic():
        for i in range(n):
            inst = _make(i)
            if mode == "v2":
                # django-lifecycle builds this snapshot in __init__; build it
                # here so the retained footprint matches reality.
                inst._initial_state = inst._snapshot_state()
                # THE PIN: bound method holds `inst` until the outer atomic
                # commits. Mirrors LexModel.py:532.
                transaction.on_commit(inst._reset_initial_state)
            # v1: no snapshot, no on_commit -> `inst` is dropped next iteration.
            del inst
            if i % 5000 == 0:
                cur = _rss_mb()
                if cur > peak:
                    peak = cur
        cur = _rss_mb()
        if cur > peak:
            peak = cur
        oncommit_len = len(connection.run_on_commit)
        # Roll back so we don't actually fire N callbacks or keep rows; we've
        # already measured the in-flight peak, which is what OOMs production.
        transaction.set_rollback(True)

    gc.collect()
    return peak - base, oncommit_len


def main(argv: list[str]) -> None:
    ns = [int(x) for x in argv[1:]] or [10_000, 50_000, 200_000, 1_000_000]

    print(f"python {sys.version.split()[0]}  django {django.get_version()}")
    print(
        "Reproduces lex/core/models/LexModel.py:532 "
        "transaction.on_commit(self._reset_initial_state)\n"
        "inside one transaction.atomic() (CalculationModel.py:1059).\n"
    )
    header = (
        f"{'N saves':>12} | {'v1 plain ΔRSS':>14} | {'v2 on_commit ΔRSS':>18} | "
        f"{'run_on_commit len':>18} | {'bytes/save':>11}"
    )
    print(header)
    print("-" * len(header))
    for n in ns:
        v1_growth, _ = run_mode("v1", n)
        v2_growth, oc_len = run_mode("v2", n)
        per_save = (v2_growth - v1_growth) * 1024 * 1024 / n
        print(
            f"{n:>12,} | {v1_growth:>11.1f} MB | {v2_growth:>15.1f} MB | "
            f"{oc_len:>18,} | {per_save:>8.0f} B"
        )

    print(
        "\nReading:\n"
        "  * v2 ΔRSS grows ~linearly with N; v1 stays ~flat. That gap is the\n"
        "    regression: every save pins its instance in connection.run_on_commit.\n"
        "  * run_on_commit len == N proves the 1:1 closure accumulation.\n"
        "  * The streaming fix (PR #610) only freed the expansion list (a few MB);\n"
        "    it does NOT touch this per-save pin, which is why it didn't help."
    )


if __name__ == "__main__":
    # Create the table for MiniInvoice (in-memory sqlite).
    from django.db import connection as _conn

    with _conn.schema_editor() as se:
        se.create_model(MiniInvoice)
    main(sys.argv)
