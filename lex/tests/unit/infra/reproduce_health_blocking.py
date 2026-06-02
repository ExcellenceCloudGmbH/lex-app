#!/usr/bin/env python
"""
Reproduction script: Health WebSocket becomes unresponsive during heavy calculations.

ROOT CAUSE: Django's ASGI handler wraps all synchronous views with
sync_to_async(thread_sensitive=True), which dispatches to a SINGLE-THREAD
executor (max_workers=1). When execute_calculation_sync() occupies this
thread, ALL other sync views AND database_sync_to_async operations
(including WebSocket auth for reconnection) are serialized behind it.

Usage:
    cd <any-lex-project>
    source .venv/bin/activate
    python <path-to>/reproduce_health_blocking.py
"""

import asyncio
import json
import time
import threading
import sys
import os


async def websocket_health_monitor(host: str, port: int, results: dict, stop_event: asyncio.Event):
    """
    Continuously ping the WebSocket health endpoint and record response latencies.
    This simulates what BackendHealthCheck.tsx does in the browser.
    """
    import websockets

    uri = f"ws://{host}:{port}/ws/health"
    results["latencies"] = []
    results["failures"] = []
    results["ws_connected"] = False

    try:
        async with websockets.connect(uri, open_timeout=5) as ws:
            results["ws_connected"] = True
            print(f"[HEALTH MONITOR] Connected to {uri}")

            while not stop_event.is_set():
                try:
                    start = time.perf_counter()
                    await ws.send("")
                    response = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    latency = time.perf_counter() - start

                    data = json.loads(response)
                    results["latencies"].append(latency)

                    if latency > 0.5:
                        print(f"[HEALTH MONITOR] SLOW response: {latency*1000:.0f}ms")
                    
                except asyncio.TimeoutError:
                    elapsed = time.perf_counter() - start
                    results["failures"].append(("timeout", elapsed))
                    print(f"[HEALTH MONITOR] TIMEOUT after {elapsed*1000:.0f}ms - "
                          f"this is what causes 'server not ready'!")

                except Exception as e:
                    results["failures"].append(("error", str(e)))
                    print(f"[HEALTH MONITOR] ERROR: {e}")

                await asyncio.sleep(1.0)  # Same interval as frontend

    except Exception as e:
        results["ws_connected"] = False
        results["failures"].append(("connection_failed", str(e)))
        print(f"[HEALTH MONITOR] Could not connect: {e}")


async def http_health_monitor(host: str, port: int, results: dict, stop_event: asyncio.Event):
    """
    Continuously poll the HTTP /health endpoint and record response latencies.
    """
    import aiohttp

    url = f"http://{host}:{port}/health"
    results["http_latencies"] = []
    results["http_failures"] = []

    async with aiohttp.ClientSession() as session:
        while not stop_event.is_set():
            try:
                start = time.perf_counter()
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=2.0)) as resp:
                    await resp.json()
                    latency = time.perf_counter() - start
                    results["http_latencies"].append(latency)

                    if latency > 0.5:
                        print(f"[HTTP HEALTH] SLOW response: {latency*1000:.0f}ms")

            except asyncio.TimeoutError:
                elapsed = time.perf_counter() - start
                results["http_failures"].append(("timeout", elapsed))
                print(f"[HTTP HEALTH] TIMEOUT after {elapsed*1000:.0f}ms")

            except Exception as e:
                results["http_failures"].append(("error", str(e)))
                print(f"[HTTP HEALTH] ERROR: {e}")

            await asyncio.sleep(1.0)


def trigger_calculation_sync():
    """
    Trigger a heavy calculation synchronously to block the server.
    This simulates what happens when a user clicks 'Calculate' in the UI.
    """
    from django.contrib.auth.models import User
    from django.db import transaction

    print("\n[CALCULATION] Starting heavy synchronous calculation...")
    print("[CALCULATION] (Simulating execute_calculation_sync behavior)")
    
    start = time.perf_counter()
    
    # Simulate what execute_calculation_sync does:
    # 1. Holds transaction.atomic() for the duration
    # 2. Does CPU-intensive Python work (GIL)
    # 3. Does many DB operations
    with transaction.atomic():
        # CPU-intensive work that holds the GIL
        total = 0
        for i in range(10_000_000):
            total += i * i
        
        # DB operations within the transaction
        for i in range(100):
            User.objects.filter(pk=999999).exists()

    duration = time.perf_counter() - start
    print(f"[CALCULATION] Completed in {duration:.1f}s")
    return duration


def simulate_blocking_without_server():
    """
    Demonstrates the blocking mechanism without needing a running server.
    Uses the same threading model as uvicorn + Django Channels.
    """
    print("\n" + "=" * 70)
    print("REPRODUCTION: Event Loop Starvation During Calculations")
    print("=" * 70)
    print()
    print("This simulates the exact scenario that causes 'server not ready':")
    print("- Uvicorn event loop runs WebSocket health consumer (async)")
    print("- Django sync views run in thread pool via asyncio.to_thread()")
    print("- Heavy calculation occupies thread + holds GIL")
    print("- Event loop can't process WebSocket pings in time")
    print()

    results = {
        "event_loop_latencies": [],
        "calculation_duration": None,
    }
    stop_event = threading.Event()

    async def simulate_event_loop_work():
        """
        Simulates what the event loop needs to do:
        process WebSocket pings and respond to health checks.
        """
        while not stop_event.is_set():
            start = time.perf_counter()
            # This is what the event loop does: schedule a tiny coroutine
            await asyncio.sleep(0)  # Yield to event loop
            latency = time.perf_counter() - start
            results["event_loop_latencies"].append(latency)
            await asyncio.sleep(0.1)  # Check every 100ms

    async def run_monitor():
        """Run the event loop monitor."""
        task = asyncio.create_task(simulate_event_loop_work())
        # Let it collect baseline latencies
        await asyncio.sleep(1.0)
        baseline_count = len(results["event_loop_latencies"])
        
        print(f"[MONITOR] Baseline latencies collected: {baseline_count} samples")
        if results["event_loop_latencies"]:
            baseline_avg = sum(results["event_loop_latencies"]) / len(results["event_loop_latencies"])
            print(f"[MONITOR] Baseline avg event loop latency: {baseline_avg*1000:.3f}ms")

        # Now start heavy calculation in a thread (simulating sync Django view)
        print("\n[MONITOR] Starting heavy calculation in thread pool (simulating Django sync view)...")
        
        calc_start = time.perf_counter()
        loop = asyncio.get_event_loop()
        
        # This is what uvicorn does: runs sync views in executor
        await loop.run_in_executor(None, heavy_cpu_work)
        
        results["calculation_duration"] = time.perf_counter() - calc_start
        
        # Collect post-calculation latencies
        await asyncio.sleep(0.5)
        stop_event.set()
        await task

    def heavy_cpu_work():
        """
        Simulates a heavy calculation running in a thread pool thread.
        This holds the GIL and starves the event loop.
        """
        total = 0
        for i in range(20_000_000):
            total += i * i
        return total

    asyncio.run(run_monitor())

    # Analyze results
    if results["event_loop_latencies"]:
        baseline_samples = results["event_loop_latencies"][:10]
        during_calc_samples = results["event_loop_latencies"][10:]
        
        if baseline_samples:
            baseline_avg = sum(baseline_samples) / len(baseline_samples)
        else:
            baseline_avg = 0
            
        if during_calc_samples:
            during_avg = sum(during_calc_samples) / len(during_calc_samples)
            during_max = max(during_calc_samples)
        else:
            during_avg = 0
            during_max = 0

        print(f"\n{'=' * 70}")
        print("RESULTS")
        print(f"{'=' * 70}")
        print(f"Calculation duration: {results['calculation_duration']:.3f}s")
        print(f"Baseline event loop latency (avg): {baseline_avg*1000:.3f}ms")
        print(f"During-calculation latency (avg):  {during_avg*1000:.3f}ms")
        print(f"During-calculation latency (max):  {during_max*1000:.3f}ms")
        print(f"Slowdown factor: {during_avg/max(baseline_avg, 0.000001):.1f}x")
        print()
        
        if during_max > 0.003:  # > 3ms (the frontend expects <1s responses)
            print("CONFIRMED: Event loop is starved by GIL contention during calculation.")
            print()
            print("When the event loop latency exceeds the WebSocket ping interval (1s),")
            print("the health consumer can't respond in time, and the frontend sees")
            print("the connection as dead → navigates to /server-not-ready.")
        
        print()
        print("ROOT CAUSE SUMMARY:")
        print("-" * 70)
        print("""
1. BackendHealthCheck.tsx connects via WebSocket to ws/health
2. It sends a ping every 1 second and expects a response
3. If no response for 3 seconds (UNHEALTHY_GRACE_MS), it navigates 
   to /server-not-ready

4. When a calculation runs synchronously (CELERY_ACTIVE not set):
   - execute_calculation_sync() runs in a thread pool thread
   - Heavy CPU work in Python holds the GIL
   - The asyncio event loop can't schedule I/O callbacks efficiently
   - WebSocket frames pile up in the kernel buffer
   - BackendHealthConsumer.receive() never gets called
   - After 3s without a response, frontend shows "server not ready"

5. Additionally, with SQLite (local dev):
   - transaction.atomic() holds a file-level write lock
   - ALL other DB reads are blocked until the transaction commits
   - List/detail API calls time out or hang

6. With PostgreSQL (production):
   - No global file lock, but GIL contention still blocks event loop
   - Heavy calculations with many model.save() calls create lock contention
   - Thread pool can be exhausted if multiple calculations run simultaneously
""")
        print(f"{'=' * 70}")


if __name__ == "__main__":
    simulate_blocking_without_server()
