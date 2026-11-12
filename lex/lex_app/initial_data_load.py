"""The initial-data load at server start: one process at a time, finished, and remembered.

``lex start`` loads ``INITIAL_DATA`` when every model it names is empty
(docs/features/data-pipeline/initial data.md, "all-or-nothing"). Three things keep
that promise when more than one server process exists over the load's lifetime
(``--reload`` restarts, ``--workers``, a second replica, a replaced pod):

* **The load outlives a graceful stop.** It runs in a *non-daemon* thread, so a
  process that is asked to stop (SIGTERM from the ``--reload`` supervisor, from
  uvicorn's worker manager, from Kubernetes) waits for it before it exits. A
  daemon thread dies mid-write instead: the next process then finds the
  referenced models partly filled and never loads again. That was the failure:
  the loader thread inherited ``daemon=True`` from the ``lex-async-ready``
  thread that starts it.
* **One process decides at a time.** The decision ("are the models empty?") and
  the load run under a PostgreSQL session advisory lock. A second process waits
  for the first one to finish and then decides on the finished database, so it
  neither loads a second copy nor reads a half-loaded database as "already
  loaded". The lock belongs to the database session, so a process that dies
  releases it with its connection.
* **An unfinished load is not mistaken for a finished one.** The load's state
  (running, completed, failed) is kept in ``lex_initial_data_load_state``. A
  process that is killed outright (SIGKILL, OOM, a grace period running out)
  cannot finish anything, but the next start sees ``running`` with the lock
  free, knows the earlier load died, and says so instead of reporting the
  partial rows as existing data.

Other database backends (the local SQLite database) have no advisory locks; there
the load runs unguarded, as before.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Callable, Optional

from django.db import DatabaseError, connection

logger = logging.getLogger(__name__)

#: Session advisory-lock key for the initial-data load: the bytes of ``lex_init``.
LOCK_KEY = int.from_bytes(b"lex_init", "big")

#: Table that remembers how the last load of each ``INITIAL_DATA`` file ended.
STATE_TABLE = "lex_initial_data_load_state"

#: Seconds between two attempts to take the lock while another process holds it.
LOCK_POLL_SECONDS = 2.0

#: Name of the thread that runs the load.
THREAD_NAME = "lex-initial-data-load"

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"

# Outcomes of :func:`run_initial_data_load` (also printed, for operators).
OUTCOME_LOADED = "loaded"
OUTCOME_FAILED = "failed"
OUTCOME_SKIPPED = "skipped"
OUTCOME_INTERRUPTED = "interrupted"
OUTCOME_FAILED_EARLIER = "failed_earlier"
OUTCOME_ABANDONED = "abandoned"


def _process_is_stopping() -> bool:
    """True once the main thread has finished, i.e. the process is shutting down."""
    return not threading.main_thread().is_alive()


def _uses_advisory_locks() -> bool:
    return connection.vendor == "postgresql"


def acquire_lock(should_stop: Callable[[], bool] = _process_is_stopping) -> bool:
    """Take the load lock on this thread's connection, waiting while another process holds it.

    Returns False only when ``should_stop()`` turns true while waiting: the process is
    shutting down and has nothing left to decide. Without advisory locks it returns
    True at once.
    """
    if not _uses_advisory_locks():
        return True
    announced = False
    while True:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
            if cursor.fetchone()[0]:
                if announced:
                    print("The other process's initial data load has ended; deciding again here.")
                return True
        if not announced:
            print(
                "Initial data load is in progress in another server process; "
                "waiting for it to end before deciding here."
            )
            announced = True
        if should_stop():
            return False
        time.sleep(LOCK_POLL_SECONDS)


def release_lock() -> None:
    if not _uses_advisory_locks():
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])
    except DatabaseError:
        # The session is gone, and the lock went with it.
        logger.warning("Releasing the initial-data lock failed", exc_info=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_state_table(cursor) -> None:
    cursor.execute(
        f"CREATE TABLE IF NOT EXISTS {STATE_TABLE} ("
        " initial_data varchar(1024) PRIMARY KEY,"
        " status varchar(16) NOT NULL,"
        " started_at varchar(40),"
        " finished_at varchar(40),"
        " host varchar(255),"
        " pid integer,"
        " error text)"
    )


def read_state(initial_data: str) -> Optional[dict]:
    """How the last load of ``initial_data`` ended, or None if none is recorded."""
    try:
        with connection.cursor() as cursor:
            _ensure_state_table(cursor)
            cursor.execute(
                f"SELECT status, started_at, finished_at, host, pid, error FROM {STATE_TABLE} "
                "WHERE initial_data = %s",
                [initial_data],
            )
            row = cursor.fetchone()
    except DatabaseError:
        logger.warning("Reading %s failed; deciding without it", STATE_TABLE, exc_info=True)
        return None
    if row is None:
        return None
    keys = ("status", "started_at", "finished_at", "host", "pid", "error")
    return dict(zip(keys, row))


def write_state(initial_data: str, status: str, error: Optional[str] = None) -> None:
    """Record that the load of ``initial_data`` started (``RUNNING``) or ended."""
    try:
        with connection.cursor() as cursor:
            _ensure_state_table(cursor)
            if status == RUNNING:
                cursor.execute(f"DELETE FROM {STATE_TABLE} WHERE initial_data = %s", [initial_data])
                cursor.execute(
                    f"INSERT INTO {STATE_TABLE} (initial_data, status, started_at, host, pid) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    [initial_data, RUNNING, _now(), socket.gethostname()[:255], os.getpid()],
                )
            else:
                cursor.execute(
                    f"UPDATE {STATE_TABLE} SET status = %s, finished_at = %s, error = %s "
                    "WHERE initial_data = %s",
                    [status, _now(), error, initial_data],
                )
    except DatabaseError:
        logger.warning("Recording the initial-data load as %r failed", status, exc_info=True)


def _load(test, generic_app_models, initial_data, audit_enabled, mq_worker) -> None:
    from lex.lex_app.celery_tasks import WaitForTasks, load_data

    if mq_worker:
        with WaitForTasks():
            load_data(test, generic_app_models, audit_enabled, initial_data)
    else:
        load_data(test, generic_app_models, audit_enabled, initial_data)


def _describe(state: dict) -> str:
    where = f" by process {state['pid']} on {state['host']}" if state.get("pid") else ""
    return f"started {state.get('started_at') or 'at an unknown time'}{where}"


def _decide_and_load(test, generic_app_models, initial_data, audit_enabled, mq_worker) -> str:
    test.test_path = initial_data
    state = read_state(initial_data)

    if test.check_if_all_models_are_empty(generic_app_models):
        if state and state["status"] != COMPLETED:
            print(
                f"The earlier initial data load ({_describe(state)}) ended as "
                f"'{state['status']}' without leaving rows; loading again."
            )
        write_state(initial_data, RUNNING)
        try:
            _load(test, generic_app_models, initial_data, audit_enabled, mq_worker)
        except Exception as exc:
            # load_data has printed the traceback already.
            write_state(initial_data, FAILED, error=f"{type(exc).__name__}: {exc}")
            return OUTCOME_FAILED
        write_state(initial_data, COMPLETED)
        return OUTCOME_LOADED

    non_empty_models = test.get_list_of_non_empty_models(generic_app_models)
    if state and state["status"] == RUNNING:
        message = (
            f"The initial data load {_describe(state)} never finished: its process stopped "
            f"mid-load. The database holds part of {initial_data}: {non_empty_models}. "
            "Lex does not load over existing rows. To load it completely, empty the database "
            "(lex create_db, then lex init) and start again."
        )
        print(f"ERROR: {message}")
        logger.error(message)
        return OUTCOME_INTERRUPTED
    if state and state["status"] == FAILED:
        message = (
            f"The initial data load {_describe(state)} failed with {state.get('error')}. "
            f"The database holds part of {initial_data}: {non_empty_models}. Fix the cause, "
            "empty the database (lex create_db, then lex init) and start again."
        )
        print(f"ERROR: {message}")
        logger.error(message)
        return OUTCOME_FAILED_EARLIER

    print(f"Loading Initial Data not triggered due to existence of objects of Model: {non_empty_models}")
    print("Not all referenced Models are empty")
    return OUTCOME_SKIPPED


def run_initial_data_load(
    test,
    generic_app_models,
    initial_data: str,
    audit_enabled: bool,
    mq_worker: bool = False,
    should_stop: Callable[[], bool] = _process_is_stopping,
) -> str:
    """Decide and, if every referenced model is empty, load ``initial_data``. Returns the outcome."""
    try:
        if not acquire_lock(should_stop):
            print("Server process is stopping; leaving the initial data load to the other process.")
            return OUTCOME_ABANDONED
        try:
            return _decide_and_load(test, generic_app_models, initial_data, audit_enabled, mq_worker)
        finally:
            release_lock()
    except Exception:
        print("Initial data load could not run:")
        traceback.print_exc()
        return OUTCOME_FAILED
    finally:
        connection.close()


def start_initial_data_load(
    test, generic_app_models, initial_data: str, audit_enabled: bool, mq_worker: bool = False
) -> threading.Thread:
    """Run :func:`run_initial_data_load` in a non-daemon thread and return the thread.

    ``daemon=False`` is explicit on purpose: a thread inherits the daemon flag of the
    thread that creates it, and this one is created from the daemon
    ``lex-async-ready`` thread. As a daemon it is killed the moment its process exits.
    """
    thread = threading.Thread(
        target=run_initial_data_load,
        args=(test, generic_app_models, initial_data, audit_enabled, mq_worker),
        name=THREAD_NAME,
        daemon=False,
    )
    thread.start()
    return thread
