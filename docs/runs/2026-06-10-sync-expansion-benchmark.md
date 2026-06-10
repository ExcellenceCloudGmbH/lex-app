# Sync Expansion Benchmark (2026-06-10)

N = 10648 (legacy) / 10648 (streaming)

| Path | Peak (MB) | Models |
|---|---|---|
| legacy (materialized) | 3.70 | 10648 |
| streaming (DFS) | 0.78 | 10648 |

Fingerprint identical: True
Peak reduction: 4.7x

---

## How to reproduce

```bash
set -a && source .env && set +a
CELERY_ACTIVE=False PROJECT_ROOT="$(pwd)" DJANGO_SETTINGS_MODULE=lex_app.settings \
  python -c "import sys, os; sys.path.insert(0, os.path.join(os.getcwd(),'lex')); \
  import django; django.setup(); import runpy; \
  runpy.run_path('scripts/bench_sync_expansion.py', run_name='__main__')"
```

(`lex` has no `run` subcommand; the script needs `django.setup()` before importing
`CalculatedModelMixin`, so it is bootstrapped inline as above. It can also be invoked
as `run_benchmark()` from inside a pytest where Django is already configured.)

## Interpreting the numbers

The headline correctness result is **Fingerprint identical: True** — the streaming DFS
generator produces the exact same ordered set of 10,648 defining-field tuples as the
legacy breadth-first list generator.

The 4.7x peak-memory reduction **understates** the real win, because of how this harness
measures. The streaming branch accumulates a full-N equivalence fingerprint
(`stream_fp.append(...)`, ~10.6k tuples) *inside* the `tracemalloc` window, whereas the
legacy branch computes its fingerprint *after* `tracemalloc.stop()`. So the streaming
0.78 MB is dominated by the fingerprint list this benchmark builds to prove equivalence —
not by the generator's own working set.

The generator's true peak (with no fingerprint accumulation, consume-and-drop) is
measured cleanly by the Task 5 regression gate
(`test_7_187_streaming_peak_is_bounded_not_linear`): there the streaming peak is a flat
~2.8 KB vs the legacy list's ~5.4 MB at N=15,625 — a ~1883x reduction, and ~constant in N.
That is the figure that reflects the OOM relief in production sync mode; this harness's
4.7x is the conservative end-to-end-with-fingerprint number.
