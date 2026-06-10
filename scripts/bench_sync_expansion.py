"""Benchmark: legacy vs streaming combinatorial expansion peak memory.

Sized to N ~= 10k (22^3 = 10648). Measures tracemalloc peak for each path and
asserts the produced fingerprint (ordered defining-field tuples) is identical.
Pure-logic (no DB) so it isolates the expansion engine's memory cost.

Run: DJANGO_SETTINGS_MODULE=lex_app.settings python -m lex run scripts/bench_sync_expansion.py
(or invoke run_benchmark() from a pytest).
"""

from __future__ import annotations

import tracemalloc

from lex.core.mixins.CalculatedModelMixin import ModelCombinationGenerator


class _FakeCalcModel:
    def __init__(self, key_lists=None, **attrs):
        self._key_lists = key_lists or {}
        for k, v in attrs.items():
            setattr(self, k, v)

    def __deepcopy__(self, memo):
        new = _FakeCalcModel()
        new.__dict__.update(self.__dict__)
        return new

    def get_selected_key_list(self, key):
        spec = self._key_lists.get(key)
        return spec(self) if callable(spec) else spec


def _fields_and_keys():
    vals = [f"v{i}" for i in range(22)]
    fields = ["f0", "f1", "f2"]
    # f2 is dependency-aware: its values depend on f0, exercising the DFS path.
    key_lists = {
        "f0": vals,
        "f1": vals,
        "f2": lambda m: vals,
    }
    return fields, key_lists


def _tuples(models, fields):
    return [tuple(getattr(m, f, None) for f in fields) for m in models]


def run_benchmark():
    fields, key_lists = _fields_and_keys()

    base = _FakeCalcModel(key_lists=key_lists)
    tracemalloc.start()
    legacy = ModelCombinationGenerator.generate_model_combinations(base, fields, {})
    _, legacy_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    legacy_fp = _tuples(legacy, fields)
    legacy_n = len(legacy)
    del legacy

    base = _FakeCalcModel(key_lists=key_lists)
    tracemalloc.start()
    stream_fp = []
    n = 0
    for m in ModelCombinationGenerator.generate_model_combinations_streaming(
        base, fields, {}
    ):
        stream_fp.append(tuple(getattr(m, f, None) for f in fields))
        n += 1
    _, stream_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    identical = stream_fp == legacy_fp
    report = (
        f"# Sync Expansion Benchmark (2026-06-10)\n\n"
        f"N = {legacy_n} (legacy) / {n} (streaming)\n\n"
        f"| Path | Peak (MB) | Models |\n|---|---|---|\n"
        f"| legacy (materialized) | {legacy_peak / 1e6:.2f} | {legacy_n} |\n"
        f"| streaming (DFS) | {stream_peak / 1e6:.2f} | {n} |\n\n"
        f"Fingerprint identical: {identical}\n"
        f"Peak reduction: {legacy_peak / max(stream_peak, 1):.1f}x\n"
    )
    print(report)
    assert identical, "Fingerprint mismatch — streaming diverged from legacy!"
    return report


if __name__ == "__main__":
    run_benchmark()
