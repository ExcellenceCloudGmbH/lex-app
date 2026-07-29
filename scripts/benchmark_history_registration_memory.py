#!/usr/bin/env python
"""Measure what history registration costs a migration-only process.

The claim under test: registering bitemporal history is the dominant memory cost
of starting a large LEX project, because every tracked model gains a Level 1
``Historical<X>`` **and** a Level 2 ``Meta<Historical<X>>`` -- so the app registry
holds roughly 3x the model classes, each carrying the parent's full field set.
A process that only applies migration files never uses any of them.

This builds N synthetic models with a realistic field count, registers them the
way ``ModelRegistration`` does, and reports peak allocation with history on and
off. It measures the *app-registry* half of the cost -- the half the fix removes.
The migration executor's ProjectState is built over the same tripled model set,
so the real saving during ``migrate`` is larger than what this reports.

Run:
    PROJECT_ROOT=lex/test_project python -m lex run \\
        scripts/benchmark_history_registration_memory.py [--models 200] [--fields 30]

Interpreting it: what matters is the *ratio* and how it scales with --models,
not the absolute megabytes, which depend on the Python build and field mix.
"""

from __future__ import annotations

import argparse
import gc
import sys
import tracemalloc


def _register_synthetic_module(label: str) -> str:
    """Make the synthetic models' module importable.

    simple_history places each ``Historical<X>`` in the module of the model it
    tracks, so an unimportable module makes registration fail silently per model
    -- and the benchmark would then be timing a *partial* registration and
    reporting a cost far lower than the real one.
    """
    import sys
    import types

    package = f"benchmark_{label}"
    module = f"{package}.models"
    for name in (package, module):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    return module


def _build_models(count: int, field_count: int, label: str):
    """Create ``count`` LexModel subclasses with ``field_count`` fields each."""
    from django.db import models as dj_models

    from lex.core.models.LexModel import LexModel

    module_name = _register_synthetic_module(label)
    built = []
    for i in range(count):
        attrs = {
            "__module__": module_name,
            "Meta": type("Meta", (), {"app_label": "lex_app"}),
        }
        for f in range(field_count):
            # A spread that resembles a real project rather than N of one type;
            # history copies every one of these onto both shadow models.
            if f % 4 == 0:
                attrs[f"num_{f}"] = dj_models.DecimalField(
                    max_digits=18, decimal_places=4, null=True, blank=True
                )
            elif f % 4 == 1:
                attrs[f"txt_{f}"] = dj_models.CharField(max_length=255, blank=True, default="")
            elif f % 4 == 2:
                attrs[f"dt_{f}"] = dj_models.DateTimeField(null=True, blank=True)
            else:
                attrs[f"flag_{f}"] = dj_models.BooleanField(default=False)
        built.append(type(f"Bench{label}{i}", (LexModel,), attrs))
    return built


def _measure(count: int, field_count: int, history_enabled: bool, label: str):
    """Peak tracemalloc bytes while registering ``count`` models."""
    from lex.process_admin.utils.model_registration import ModelRegistration

    models = _build_models(count, field_count, label)

    gc.collect()
    tracemalloc.start()
    try:
        ModelRegistration.register_models(
            models, untracked_models=[], history_tracking_enabled=history_enabled
        )
    finally:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    registered = _count_registered(label)
    return peak, registered


def _count_registered(label: str) -> int:
    """How many model classes the app registry now holds for this batch."""
    from django.apps import apps

    marker = f"Bench{label}"
    return sum(
        1
        for m in apps.get_app_config("lex_app").get_models()
        if marker in m.__name__
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=int, default=150, help="synthetic models per run")
    parser.add_argument("--fields", type=int, default=30, help="fields per model")
    args = parser.parse_args()

    print(f"\n{args.models} models x {args.fields} fields\n")

    off_peak, off_registered = _measure(args.models, args.fields, False, "Off")
    on_peak, on_registered = _measure(args.models, args.fields, True, "On")

    mb = 1024 * 1024
    print(f"  history OFF (what a migration-only process now does):")
    print(f"     peak allocated : {off_peak / mb:8.1f} MiB")
    print(f"     model classes  : {off_registered}")
    print(f"  history ON (what it used to do):")
    print(f"     peak allocated : {on_peak / mb:8.1f} MiB")
    print(f"     model classes  : {on_registered}")

    if off_peak > 0:
        print(f"\n  ratio          : {on_peak / off_peak:.1f}x memory")
    if off_registered > 0:
        print(f"  model multiplier: {on_registered / off_registered:.1f}x classes")
    print(
        f"\n  saved per migrate: {(on_peak - off_peak) / mb:.1f} MiB at "
        f"{args.models} models -- scale linearly for a real project.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
