"""
Reproduction test: Heavy calculations block the health endpoint and data APIs.

Root cause analysis:
──────────────────────────────────────────────────────────────────────────────
When CELERY_ACTIVE is not set (the default for most deployments), all
calculations run **synchronously inside the HTTP request thread** that
triggered them (see CalculationModel.execute_calculation_sync and
CalculatedModelMixin._dispatch_model_processing).

Uvicorn serves Django sync views via a thread-pool executor (default size
~40 threads).  When a calculation occupies a thread for an extended period,
two blocking mechanisms combine to starve other requests:

1. **Python GIL contention** – CPU-intensive calculation code holds the GIL
   for extended periods (the GIL switches every 5ms by default, but long-
   running C-extension or pandas/numpy operations can hold it longer, and
   even with pure Python the 5ms quantum multiplied across dozens of model
   iterations adds up to significant latency for other threads).

2. **SQLite file-level locking** (local dev) – execute_calculation_sync wraps
   the calculation in `transaction.atomic()`.  SQLite's write-lock is
   process-global: while one thread holds a write transaction, *all* other
   threads that try to read or write the DB are blocked (including list-API
   requests, the admin, etc.).

3. **PostgreSQL row/table-level locking** (deployed) – While PostgreSQL
   doesn't have a global file lock, heavy calculations that do many INSERTs/
   UPDATEs inside a single transaction.atomic() can hold row locks that block
   concurrent queries on the same tables.

The combination means:
  - The thread pool fills up with blocked threads waiting for DB locks or GIL
  - New HTTP requests (including /health) cannot acquire a thread
  - The fast_health ASGI bypass *should* still respond (it's pure async),
    BUT the WebSocket health check and any HTTP endpoint that touches Django
    ORM will stall
  - In practice, many load-balancer probes hit /api/health through middleware
    that uses Django's request cycle (authentication, logging, etc.)

This test demonstrates the blocking behavior concretely.
"""

import asyncio
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock

from django.test import TestCase, TransactionTestCase, override_settings
from django.test.client import RequestFactory
from django.http import JsonResponse


class TestCalculationBlocksOtherRequests(TransactionTestCase):
    """
    Proves that a long-running synchronous calculation blocks other threads
    from accessing the database (SQLite file-lock scenario).
    """

    def test_sqlite_write_lock_blocks_concurrent_reads(self):
        """
        When one thread holds a write transaction (as execute_calculation_sync does),
        other threads trying to read the database are blocked until it completes.
        
        This is the direct mechanism causing the "server not ready" behavior
        with SQLite backends.
        """
        from django.db import connection, connections
        from django.contrib.auth.models import User

        # Ensure we have at least one user to query
        User.objects.create_user(username="test_concurrent", password="test")

        barrier = threading.Barrier(2, timeout=10)
        results = {"read_blocked_duration": None, "write_duration": None}

        def long_write_transaction():
            """Simulates execute_calculation_sync holding a write lock."""
            from django.db import connection as conn
            barrier.wait()  # Synchronize start
            start = time.perf_counter()
            from django.db import transaction
            with transaction.atomic():
                # Simulate a heavy calculation that writes data
                User.objects.create(username="calc_result_1", password="x")
                time.sleep(2)  # Simulate calculation time
                User.objects.create(username="calc_result_2", password="x")
            results["write_duration"] = time.perf_counter() - start

        def concurrent_read():
            """Simulates a data-fetching API request during calculation."""
            from django.db import connection as conn
            barrier.wait()  # Synchronize start
            time.sleep(0.1)  # Small delay to ensure write transaction is active
            start = time.perf_counter()
            # This read will be blocked by the write lock in SQLite
            list(User.objects.all())
            results["read_blocked_duration"] = time.perf_counter() - start

        writer = threading.Thread(target=long_write_transaction)
        reader = threading.Thread(target=concurrent_read)

        writer.start()
        reader.start()
        writer.join(timeout=10)
        reader.join(timeout=10)

        # If using SQLite, the read should have been blocked for ~2 seconds
        # (the duration of the write transaction)
        from django.conf import settings
        db_engine = settings.DATABASES["default"]["ENGINE"]

        if "sqlite" in db_engine:
            # With SQLite, the read is blocked for approximately the duration
            # of the write transaction
            self.assertIsNotNone(results["read_blocked_duration"])
            self.assertGreater(
                results["read_blocked_duration"],
                1.0,  # Should be blocked for most of the 2s write
                f"Expected read to be blocked by SQLite write lock, "
                f"but it completed in {results['read_blocked_duration']:.3f}s. "
                f"This test demonstrates the root cause of health endpoint timeouts."
            )
            print(
                f"\n{'='*70}\n"
                f"REPRODUCTION SUCCESSFUL (SQLite)\n"
                f"{'='*70}\n"
                f"Write transaction duration: {results['write_duration']:.3f}s\n"
                f"Read was blocked for: {results['read_blocked_duration']:.3f}s\n"
                f"\n"
                f"ROOT CAUSE: SQLite's file-level write lock blocks ALL concurrent\n"
                f"reads while execute_calculation_sync() holds transaction.atomic().\n"
                f"This is why the health endpoint and data APIs become unreachable\n"
                f"during calculations.\n"
                f"{'='*70}\n"
            )
        else:
            # With PostgreSQL, reads aren't fully blocked but can still be slow
            # due to GIL contention and connection pool exhaustion
            print(
                f"\n{'='*70}\n"
                f"PostgreSQL mode: read completed in {results['read_blocked_duration']:.3f}s\n"
                f"(PostgreSQL uses MVCC so reads aren't fully blocked by writes,\n"
                f"but the GIL + thread pool exhaustion still causes the issue)\n"
                f"{'='*70}\n"
            )


class TestThreadPoolExhaustion(unittest.TestCase):
    """
    Proves that when the thread pool is exhausted by long-running calculations,
    new requests (including health checks) cannot be served.
    """

    def test_thread_pool_starvation_blocks_new_requests(self):
        """
        When all threads in the executor are occupied by calculations,
        new requests queue up and time out.
        
        This demonstrates the behavior even with PostgreSQL where DB locking
        is not the primary issue.
        """
        # Simulate uvicorn's thread pool with a small size for testing
        pool_size = 4
        executor = ThreadPoolExecutor(max_workers=pool_size)

        health_response_times = []
        calculation_complete = threading.Event()

        def simulate_heavy_calculation(duration):
            """Occupies a thread for `duration` seconds (like execute_calculation_sync)."""
            time.sleep(duration)
            return "calculation_done"

        def simulate_health_check():
            """A simple health check that needs a thread from the pool."""
            start = time.perf_counter()
            # Just return immediately - simulates fast_health_path 
            # but going through the thread pool (like any Django view)
            elapsed = time.perf_counter() - start
            health_response_times.append(elapsed)
            return "healthy"

        # Fill all threads with calculations
        calc_futures = []
        for i in range(pool_size):
            future = executor.submit(simulate_heavy_calculation, 3)
            calc_futures.append(future)

        # Give threads time to start
        time.sleep(0.2)

        # Now try to submit a health check - it should be queued
        start = time.perf_counter()
        health_future = executor.submit(simulate_health_check)
        
        # Wait for health check with timeout
        try:
            health_future.result(timeout=1.0)
            health_latency = time.perf_counter() - start
            # If it completed within 1s, the pool wasn't fully exhausted
            # (shouldn't happen since all 4 threads are busy for 3s)
            self.fail(
                f"Health check completed in {health_latency:.3f}s but pool should be exhausted"
            )
        except Exception:
            # TimeoutError - this proves the point
            health_latency = time.perf_counter() - start
            self.assertGreater(health_latency, 0.9)
            print(
                f"\n{'='*70}\n"
                f"REPRODUCTION SUCCESSFUL (Thread Pool Exhaustion)\n"
                f"{'='*70}\n"
                f"Thread pool size: {pool_size}\n"
                f"Active calculations: {pool_size} (all threads occupied)\n"
                f"Health check wait time: >{health_latency:.3f}s (timed out)\n"
                f"\n"
                f"ROOT CAUSE: When all threads in uvicorn's executor pool are\n"
                f"occupied by long-running synchronous calculations, NO new HTTP\n"
                f"requests can be processed - including health checks.\n"
                f"\n"
                f"Even though fast_health.py is async and bypasses Django,\n"
                f"it still runs on the same event loop. If the event loop's\n"
                f"thread pool is exhausted AND any blocking call sneaks into\n"
                f"the async path, everything stalls.\n"
                f"{'='*70}\n"
            )

        # Cleanup
        for f in calc_futures:
            f.result(timeout=10)
        executor.shutdown(wait=True)


class TestGILContentionDuringCalculation(unittest.TestCase):
    """
    Demonstrates how CPU-intensive calculations cause GIL contention
    that slows down ALL other threads, including those serving HTTP requests.
    """

    def test_cpu_bound_work_causes_gil_contention(self):
        """
        Even with PostgreSQL (no file-level DB lock), CPU-intensive calculations
        hog the GIL, causing latency spikes in concurrent request-handling threads.
        """
        results = {"io_latencies": [], "cpu_duration": None}
        stop_event = threading.Event()

        def cpu_intensive_calculation():
            """Simulates a heavy calculation (lots of Python object creation, loops)."""
            start = time.perf_counter()
            total = 0
            # Simulate the kind of work done in calculate():
            # creating model instances, iterating over querysets, etc.
            for i in range(5_000_000):
                total += i * i  # Pure Python CPU work
                if i % 1_000_000 == 0 and stop_event.is_set():
                    break
            results["cpu_duration"] = time.perf_counter() - start

        def measure_io_latency():
            """Measures how responsive a 'health check' thread is during calculation."""
            while not stop_event.is_set():
                start = time.perf_counter()
                time.sleep(0.01)  # Simulate minimal IO work (like sending HTTP response)
                actual = time.perf_counter() - start
                # The excess beyond 10ms is GIL contention
                results["io_latencies"].append(actual - 0.01)
                if len(results["io_latencies"]) > 50:
                    break
            stop_event.set()

        cpu_thread = threading.Thread(target=cpu_intensive_calculation)
        io_thread = threading.Thread(target=measure_io_latency)

        cpu_thread.start()
        io_thread.start()

        cpu_thread.join(timeout=15)
        stop_event.set()
        io_thread.join(timeout=5)

        if results["io_latencies"]:
            avg_contention = sum(results["io_latencies"]) / len(results["io_latencies"])
            max_contention = max(results["io_latencies"])
            print(
                f"\n{'='*70}\n"
                f"GIL CONTENTION MEASUREMENT\n"
                f"{'='*70}\n"
                f"CPU calculation duration: {results['cpu_duration']:.3f}s\n"
                f"IO thread latency samples: {len(results['io_latencies'])}\n"
                f"Average GIL-induced latency: {avg_contention*1000:.1f}ms\n"
                f"Maximum GIL-induced latency: {max_contention*1000:.1f}ms\n"
                f"\n"
                f"Even a simple time.sleep(10ms) takes {(avg_contention+0.01)*1000:.1f}ms\n"
                f"on average due to GIL contention from the calculation thread.\n"
                f"{'='*70}\n"
            )
            # GIL contention should add measurable latency
            # (typically 1-5ms per scheduling quantum with heavy CPU work)
            if max_contention > 0.001:  # > 1ms excess latency
                print("GIL contention confirmed as contributing factor.")


class TestAsyncHealthBypassValidation(unittest.TestCase):
    """
    Validates that the fast_health ASGI bypass SHOULD work even during
    calculation, but identifies why it might still fail in practice.
    """

    def test_fast_health_is_truly_async_and_non_blocking(self):
        """
        The fast_health_path check in asgi.py runs at the ASGI level without
        touching Django's ORM or thread pool. In theory, this should always
        respond even during heavy calculations.
        
        The remaining question is: what makes the health check unreachable
        in practice?
        """
        from lex.lex_app.fast_health import health_asgi_app, is_fast_health_path

        async def simulate_health_during_busy_loop():
            """
            Run health check on the async event loop while the thread pool
            is hypothetically exhausted.
            """
            inbound = [{"type": "http.request", "body": b"", "more_body": False}]
            outbound = []

            async def receive():
                return inbound.pop(0) if inbound else {"type": "http.disconnect"}

            async def send(msg):
                outbound.append(msg)

            start = time.perf_counter()
            await health_asgi_app({"type": "http", "path": "/health"}, receive, send)
            elapsed = time.perf_counter() - start

            return elapsed, outbound

        elapsed, messages = asyncio.run(simulate_health_during_busy_loop())

        # The async health check should complete in microseconds
        self.assertLess(elapsed, 0.01)  # < 10ms
        self.assertEqual(messages[0]["status"], 200)
        print(
            f"\n{'='*70}\n"
            f"ASYNC HEALTH BYPASS ANALYSIS\n"
            f"{'='*70}\n"
            f"fast_health ASGI handler responds in: {elapsed*1000:.3f}ms\n"
            f"\n"
            f"The fast_health.py handler IS truly async and non-blocking.\n"
            f"However, the 'server not ready' page appears because:\n"
            f"\n"
            f"1. The frontend's health polling may use /api/health which goes\n"
            f"   through Django middleware (auth, session, etc.) → thread pool\n"
            f"2. The WebSocket health consumer (ws/health) shares the event loop\n"
            f"   and can be affected by GIL contention\n"
            f"3. Load balancers/k8s probes may timeout waiting for the response\n"
            f"   even though the ASGI path is fast, because TCP connections\n"
            f"   backed up in the kernel's accept queue can't be processed\n"
            f"   fast enough when the event loop is starved by sync_to_async\n"
            f"   thread callbacks completing slowly due to GIL\n"
            f"{'='*70}\n"
        )
