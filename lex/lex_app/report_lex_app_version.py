"""Reports the running lex-app package version to the LEX Instance Controller.

Intent
------
The Instance Controller (IC) UI shows a "LEX APP VERSION" field per instance
in the instance details panel. The version must reflect what's actually
installed and running in the customer pod — not what IC's database thinks was
deployed (which can be stale after a failed release).

This module pushes the installed ``lex._version.__version__`` to IC over the
already-existing instance→IC HTTP channel:

* env var ``DOMAIN_BASE``     — IC domain (set by IC's Terraform/Helm)
* env var ``LEX_API_KEY``     — per-instance API key (set by IC's Terraform/Helm)
* header  ``Authorization: Api-Key <key>``
* endpoint ``POST /api/report_lex_app_version/`` with body ``{"version": ...}``

This is the same pattern used by ``lex.api.views.lex_api.LexAPI.send_email``
and ``get_client_roles``. No new credentials, no new cluster permissions, no
public exposure on the customer instance.

Failure mode
------------
A failed POST is logged and swallowed — IC unreachable, slow, returning 4xx —
must never crash or slow customer pod startup. If IC never receives the
report, its DB keeps the previously-known version (or empty for first deploy),
which the UI renders as ``Unknown``.

Backwards compatibility
-----------------------
Customer instances running older lex-app versions (without this module) do not
report at all → IC keeps ``lex_app_version = ""`` for those instances → UI
shows ``Unknown``. No coupled rollout required.
"""

from __future__ import annotations

import logging
import os
import threading

import requests

from lex._version import __version__

logger = logging.getLogger(__name__)

# Module-level guard so multiple worker processes / replicas / restarts don't
# spam IC. Each Python process reports at most once.
_REPORTED = False
_REPORT_LOCK = threading.Lock()

# How long we wait for IC to acknowledge before giving up. Kept short because
# this runs on the startup path; if IC is slow we don't care, we just won't
# update the version until next startup.
_REQUEST_TIMEOUT_SECONDS = 10


def _is_deployed_instance() -> bool:
    """Return True when running inside a customer LEX instance pod.

    Local dev / CI / pytest do not push — the same gate ``LexAPI.send_email``
    uses (``DEPLOYMENT_ENVIRONMENT``), plus the two env vars we need.
    """
    return bool(
        os.getenv("DEPLOYMENT_ENVIRONMENT")
        and os.getenv("DOMAIN_BASE")
        and os.getenv("LEX_API_KEY")
    )


def _do_report(version: str) -> None:
    """POST the version to IC. Never raises — failures are logged only."""
    domain_base = os.getenv("DOMAIN_BASE")
    api_key = os.getenv("LEX_API_KEY")
    url = f"https://{domain_base}/api/report_lex_app_version/"
    headers = {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            url,
            json={"version": version},
            headers=headers,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.info("lex-app version push to IC failed (network): %s", exc)
        return

    if response.status_code == 200:
        logger.info("Reported lex-app version %s to IC.", version)
        return

    logger.info(
        "lex-app version push to IC returned %s: %s",
        response.status_code,
        (response.text or "")[:200],
    )


def report_lex_app_version_to_ic(*, force: bool = False) -> bool:
    """Push the installed lex-app version to IC in a background thread.

    Returns True if a push was scheduled, False if it was skipped (not a
    deployed instance, or already reported by this process).

    The actual HTTP call runs on a daemon thread so it cannot block server
    startup — even a hung IC will not delay pod readiness.

    Args:
        force: when True, push even if this process already pushed once.
            Used by tests.
    """
    global _REPORTED

    if not _is_deployed_instance():
        return False

    with _REPORT_LOCK:
        if _REPORTED and not force:
            return False
        _REPORTED = True

    thread = threading.Thread(
        target=_do_report,
        args=(__version__,),
        name="lex-app-version-reporter",
        daemon=True,
    )
    thread.start()
    return True


def _reset_for_tests() -> None:
    """Reset the once-per-process guard so tests can exercise the push path."""
    global _REPORTED
    with _REPORT_LOCK:
        _REPORTED = False
