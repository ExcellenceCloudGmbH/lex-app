"""Isolated local smoke harness for the Celery worker-recovery requeue path.

This deliberately avoids the CalculationModel/DB stack so it tests ONLY the
recovery machinery: heartbeat -> dead detection -> same-task-id requeue ->
bounded budget. A plain task sleeps; you SIGKILL the worker mid-sleep and watch
the supervisor re-dispatch the SAME task_id onto the same queue.

Usage (4 terminals, repo root, local Redis on 6379):

  export PYTHONPATH="lex:$PWD"
  export DJANGO_SETTINGS_MODULE=lex_app.settings
  export CELERY_ACTIVE=TRUE                 # REQUIRED: enables recovery + a real worker
  export LEX_TASK_RECOVERY_ENABLED=true
  export LEX_TASK_HEARTBEAT_INTERVAL=2      # fast beat so the test is quick
  export LEX_TASK_HB_TTL_MULTIPLIER=2       # hb TTL = 4s
  export LEX_TASK_SUPERVISOR_SCAN_INTERVAL=3
  export LEX_TASK_MAX_RETRIES=2             # small budget so give-up is observable
  PY=/opt/anaconda/bin/python

  # Terminal A — a real worker (concurrency 1 so there is exactly one child to kill)
  $PY -m celery -A scripts.recovery_smoke.harness worker -c 1 -Q celery --loglevel=info

  # Terminal B — the supervisor loop
  $PY -m lex run_recovery_supervisor --interval 3

  # Terminal C — enqueue a 60s task and print its id
  $PY scripts/recovery_smoke/harness.py enqueue 60

  # Terminal D — watch the recovery keys live
  $PY scripts/recovery_smoke/harness.py watch

  # Now: in Terminal A find the worker CHILD pid (ForkPoolWorker) and `kill -9` it,
  # or just `kill -9` the whole worker. Within ~hb_ttl + scan_interval the
  # supervisor logs "requeued dead task <id> attempt 1/2" and a fresh worker run
  # picks up the SAME id. Kill it twice more to watch the budget exhaust and the
  # supervisor mark the task FAILURE (MaxRequeueExceeded).
"""

from __future__ import annotations

import os
import sys
import time


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")
# Make recovery actually run even if the caller forgot to export it.
os.environ.setdefault("CELERY_ACTIVE", "TRUE")
os.environ.setdefault("LEX_TASK_RECOVERY_ENABLED", "true")

import django  # noqa: E402

django.setup()

from lex.lex_app.celery import app  # noqa: E402  (import after django.setup)


@app.task(name="recovery_smoke.sleep", bind=True)
def sleep_task(self, seconds: int = 60):
    """Sleep so there is a live, killable, in-flight task to recover."""
    print(f"[sleep_task] id={self.request.id} sleeping {seconds}s", flush=True)
    deadline = time.monotonic() + int(seconds)
    while time.monotonic() < deadline:
        time.sleep(1)
    print(f"[sleep_task] id={self.request.id} COMPLETED", flush=True)
    return {"task_id": self.request.id, "slept": seconds}


def _redis():
    import redis

    return redis.from_url(app.conf.broker_url, decode_responses=True)


def _enqueue(seconds: int):
    res = app.send_task("recovery_smoke.sleep", args=[seconds], queue="celery")
    print(f"ENQUEUED task_id={res.id} (sleep {seconds}s, queue=celery)")
    print("Watch it / kill the worker, then check it resolves to the SAME id.")
    return res.id


def _watch():
    from lex.lex_app.celery_recovery import redis_keys

    r = _redis()
    idx = redis_keys.index_key()
    print(f"Watching {idx} and hb/payload keys (Ctrl-C to stop)...")
    while True:
        tracked = sorted(r.smembers(idx) or [])
        line = []
        for tid in tracked:
            hb = r.ttl(redis_keys.heartbeat_key(tid))
            pl = r.get(redis_keys.payload_key(tid))
            retries = "?"
            if pl:
                try:
                    import base64
                    import pickle

                    retries = pickle.loads(base64.b64decode(pl)).get("retries")
                except Exception:
                    retries = "decode-err"
            hb_state = f"hb_ttl={hb}s" if hb and hb > 0 else "HB-EXPIRED(dead)"
            line.append(f"{tid[:8]} {hb_state} retries={retries}")
        print(f"[{time.strftime('%H:%M:%S')}] tracked={len(tracked)} | " + " | ".join(line))
        time.sleep(1)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "enqueue"
    if cmd == "enqueue":
        _enqueue(int(sys.argv[2]) if len(sys.argv) > 2 else 60)
    elif cmd == "watch":
        _watch()
    else:
        print(__doc__)
