"""More than one hostname per instance: ``LEX_EXTRA_ALLOWED_HOSTS``.

Intent
------
* ``DOMAIN_HOSTED`` is a single name, so until this existed ``ALLOWED_HOSTS``
  held exactly one real host. Any other hostname pointing at the same pod — a
  customer domain attached but not yet promoted, a vanity host put in front of
  one public route — was routed by the ingress and then refused by Django with
  a 400. ``USE_X_FORWARDED_HOST`` is on, so Django judges the host the CLIENT
  asked for and no ingress-side rewrite can stand in for this list.
* The same names are trusted for CSRF, because a host that serves the app
  serves its forms; listed in one and not the other is a half-working state
  whose only symptom is "Origin checking failed" on every POST.
* ``*`` is refused on a deployed instance. An instance that accepts any Host
  accepts Host-header poisoning, and the refusal has to come from startup
  rather than from a reviewer noticing.

Cluster 1bl — scenarios 1.630–1.631. Type: U.
Covers: lex/lex_app/settings.py (``_lex_extra_allowed_hosts``, ``ALLOWED_HOSTS``,
``CSRF_TRUSTED_ORIGINS``, the ``DEPLOYMENT_ENVIRONMENT`` guard).

Each scenario reads the settings module in a FRESH interpreter, because
``ALLOWED_HOSTS`` is built once at import: re-importing in this process would
hand back the already-populated module and assert nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import SimpleTestCase

from lex.tools import project_root as project_root_module

pytestmark = pytest.mark.init

#: The checkout holding the ``lex`` package, so a fresh interpreter imports this lex.
_CHECKOUT = Path(project_root_module.__file__).resolve().parents[2]

#: Variables a fresh interpreter must not inherit from the test process: each one
#: changes what the settings module builds.
_CLEARED = (
    "DJANGO_SETTINGS_MODULE",
    "PROJECT_ROOT",
    "PYTHONPATH",
    "GIT_CEILING_DIRECTORIES",
    "LEX_EXTRA_ALLOWED_HOSTS",
    "DOMAIN_HOSTED",
    "DEPLOYMENT_ENVIRONMENT",
    "CELERY_ACTIVE",
    "C_FORCE_ROOT",
)

#: Presence is all the startup guard checks, so these only have to exist.
_REDIS = {"REDIS_HOST": "redis.invalid", "REDIS_USERNAME": "u", "REDIS_PASSWORD": "p"}

_READ = (
    "import json\n"
    "from lex.lex_app import settings as s\n"
    "print(json.dumps([s.ALLOWED_HOSTS, s.CSRF_TRUSTED_ORIGINS]))\n"
)

_IMPORT_OUTCOME = (
    "import json\n"
    "try:\n"
    "    from lex.lex_app import settings  # noqa: F401\n"
    "except Exception as error:\n"
    "    print(json.dumps([type(error).__name__, str(error)]))\n"
    "else:\n"
    "    print(json.dumps(['NO_ERROR', '']))\n"
)


def _fresh(code: str, **env_overrides) -> list:
    """Run ``code`` in a fresh interpreter; it prints one JSON value last."""
    env = {k: v for k, v in os.environ.items() if k not in _CLEARED}
    env["PYTHONPATH"] = _CHECKOUT.as_posix()
    env.update(env_overrides)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_CHECKOUT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"fresh interpreter failed:\n{result.stderr[-3000:]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


class TestCluster01bl_ExtraAllowedHosts(SimpleTestCase):
    """D1: LEX_EXTRA_ALLOWED_HOSTS."""

    # -- 1.630 ---------------------------------------------------------
    def test_1_630_extra_hosts_reach_allowed_hosts_and_csrf(self) -> None:
        """
        Scenario 1.630: the listed hosts are added to ALLOWED_HOSTS and trusted for CSRF.
        Given: a fresh interpreter with and without LEX_EXTRA_ALLOWED_HOSTS
        When: lex's settings module is imported
        Then: without it ALLOWED_HOSTS is exactly the four it always held and
              CSRF_TRUSTED_ORIGINS the two it always held; with it each name is
              appended (trimmed, no trailing slash) and carried into
              CSRF_TRUSTED_ORIGINS as an https origin, a leading-dot host as
              the "https://*.domain" wildcard an origin has to be written as.
              The canonical DOMAIN_HOSTED entry stays first either way.
        """
        before_hosts, before_csrf = _fresh(_READ, DOMAIN_HOSTED="canonical.example.com")
        self.assertEqual(before_hosts[0], "canonical.example.com")
        self.assertEqual(len(before_hosts), 4, msg=f"unexpected baseline: {before_hosts}")
        self.assertEqual(len(before_csrf), 2, msg=f"unexpected baseline: {before_csrf}")

        after_hosts, after_csrf = _fresh(
            _READ,
            DOMAIN_HOSTED="canonical.example.com",
            LEX_EXTRA_ALLOWED_HOSTS=" sign.example.com/, .customer.example ",
        )
        self.assertEqual(after_hosts[0], "canonical.example.com")
        self.assertEqual(after_hosts[len(before_hosts):], ["sign.example.com", ".customer.example"])
        self.assertEqual(
            after_csrf[len(before_csrf):],
            ["https://sign.example.com", "https://*.customer.example"],
        )
        # The baseline is untouched, so an instance that sets nothing is unchanged.
        self.assertEqual(after_hosts[: len(before_hosts)], before_hosts)
        self.assertEqual(after_csrf[: len(before_csrf)], before_csrf)

    # -- 1.631 ---------------------------------------------------------
    def test_1_631_a_deployed_instance_refuses_the_star(self) -> None:
        """
        Scenario 1.631: "*" is refused on a deployed instance, a named host is not.
        Given: DEPLOYMENT_ENVIRONMENT and DOMAIN_HOSTED set, Redis configured
        When: LEX_EXTRA_ALLOWED_HOSTS is "*", and then a real hostname
        Then: the first raises ImproperlyConfigured naming the variable; the
              second imports cleanly. Off a deployed instance "*" is not the
              framework's business and startup is unaffected.
        """
        deployed = dict(_REDIS, DEPLOYMENT_ENVIRONMENT="prod", DOMAIN_HOSTED="canonical.example.com")

        name, message = _fresh(_IMPORT_OUTCOME, **deployed, LEX_EXTRA_ALLOWED_HOSTS="*")
        self.assertEqual(name, "ImproperlyConfigured", msg=message)
        self.assertIn("LEX_EXTRA_ALLOWED_HOSTS", message)

        name, message = _fresh(_IMPORT_OUTCOME, **deployed, LEX_EXTRA_ALLOWED_HOSTS="sign.example.com")
        self.assertEqual(name, "NO_ERROR", msg=message)

        name, message = _fresh(_IMPORT_OUTCOME, LEX_EXTRA_ALLOWED_HOSTS="*")
        self.assertEqual(name, "NO_ERROR", msg=message)
