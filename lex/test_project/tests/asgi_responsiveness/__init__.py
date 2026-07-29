"""
Cluster 15: Async Calculation Dispatch & ASGI Responsiveness.

The customer-visible promise: heavy calculations no longer take the
rest of the application down with them.  The frontend's
``BackendHealthCheck`` keeps responding, other API calls keep
returning, and concurrent calculations don't serialize behind the
asgiref single-thread executor.

See ``lex/test_project/test-plan/test-clusters.md`` § 15 for the
intent statement and the five features this cluster pins.
"""

