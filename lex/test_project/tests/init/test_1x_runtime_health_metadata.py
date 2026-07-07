"""Health exposes encrypted runtime metadata for the Instance Controller.

Intent: the IC instance-details UI needs to show the lex-app version and the
customer-project commit SHA that the running pod is advertising, not the values
IC last wrote to its DB before Terraform. A failed release can leave the DB on
the attempted target while the old pod keeps serving traffic; reading the old
pod's health response gives IC the running value.

The metadata is encrypted with the per-instance ``LEX_API_KEY`` shared by the
pod and IC. The public health contract stays stable: callers can still rely on
``{"status": "Healthy :)"}``, and probes ignore the optional ``runtime`` token.

Cluster 1x — scenarios 1.187 – 1.194. Type: U + I.
Covers: lex/lex_app/runtime_health.py, fast_health.py, views.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1x_runtime_health_metadata.py -v
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet, InvalidToken
from django.test import Client, SimpleTestCase

from lex.lex_app import fast_health
from lex.lex_app.runtime_health import (
    HEALTH_STATUS,
    build_health_payload,
    encrypt_runtime_metadata,
)

pytestmark = pytest.mark.init

_API_KEY = "test-api-key-deadbeef"
_COMMIT_SHA = "abc123def4567890"


def _decrypt_runtime(token: str, api_key: str = _API_KEY) -> dict[str, str]:
    key = base64.urlsafe_b64encode(hashlib.sha256(api_key.encode("utf-8")).digest())
    return json.loads(Fernet(key).decrypt(token.encode("ascii")).decode("utf-8"))


class TestCluster01x_RuntimeHealthMetadata(SimpleTestCase):
    """Cluster 1x: encrypted health runtime metadata."""

    # -- 1.187 ---------------------------------------------------------
    def test_1_187_health_without_key_keeps_legacy_payload(self) -> None:
        """
        Scenario 1.187: missing LEX_API_KEY returns legacy health only.
        Given: no per-instance API key in the environment
        When: the health payload is built
        Then: the response contains exactly the stable status contract and no
              runtime token, so local dev / CI do not emit misleading metadata.
        """
        with patch.dict("os.environ", {"LEX_API_KEY": "", "COMMIT_SHA": _COMMIT_SHA}, clear=False):
            payload = build_health_payload()

        self.assertEqual(
            payload,
            {"status": HEALTH_STATUS},
            "Without LEX_API_KEY, health must stay the legacy public payload; "
            "an encrypted token would be undecryptable by IC.",
        )

    # -- 1.188 ---------------------------------------------------------
    def test_1_188_health_with_key_adds_encrypted_runtime_token(self) -> None:
        """
        Scenario 1.188: deployed pod health includes encrypted runtime metadata.
        Given: LEX_API_KEY and COMMIT_SHA are present in the running pod env
        When: the health payload is built
        Then: status remains public and runtime decrypts to lex-app version,
              running commit SHA, and source marker ``env``.
        """
        with patch.dict("os.environ", {"LEX_API_KEY": _API_KEY, "COMMIT_SHA": _COMMIT_SHA}, clear=False):
            payload = build_health_payload()

        self.assertEqual(
            payload.get("status"),
            HEALTH_STATUS,
            "The public health status must not change; probes and dashboards "
            "depend on the literal Healthy contract.",
        )
        self.assertIn(
            "runtime",
            payload,
            "Deployed health responses must include encrypted runtime metadata "
            "so IC can show running version/SHA without DB drift.",
        )
        runtime = _decrypt_runtime(payload["runtime"])
        self.assertRegex(
            runtime.get("lex_app_version", ""),
            r"^\d+\.\d+\.\d+",
            "Runtime metadata must carry the installed lex-app package version.",
        )
        self.assertEqual(
            runtime.get("instance_commit_sha"),
            _COMMIT_SHA,
            "Runtime commit SHA must come from the running pod env. After a "
            "failed release, the old pod keeps its old COMMIT_SHA while IC DB "
            "may already contain the attempted target.",
        )
        self.assertEqual(
            runtime.get("commit_sha_source"),
            "env",
            "The source marker must be explicit so the UI/docs do not overstate "
            "this as a build-file attestation.",
        )

    # -- 1.189 ---------------------------------------------------------
    def test_1_189_runtime_token_is_encrypted_not_plaintext(self) -> None:
        """
        Scenario 1.189: runtime values are not readable from the health body.
        Given: an arbitrary runtime metadata dict
        When: it is encrypted for health
        Then: the token contains neither the commit SHA nor lex-app version in
              plaintext, but decrypts with the shared API key.
        """
        metadata = {
            "lex_app_version": "9.9.9-test",
            "instance_commit_sha": _COMMIT_SHA,
            "commit_sha_source": "env",
        }
        token = encrypt_runtime_metadata(_API_KEY, metadata)

        self.assertNotIn(
            _COMMIT_SHA,
            token,
            "The health runtime token must not expose the commit SHA as plaintext.",
        )
        self.assertNotIn(
            "9.9.9-test",
            token,
            "The health runtime token must not expose the lex-app version as plaintext.",
        )
        self.assertEqual(
            _decrypt_runtime(token),
            metadata,
            "IC must be able to recover the exact runtime metadata with the same "
            "per-instance API key.",
        )

    # -- 1.190 ---------------------------------------------------------
    def test_1_190_wrong_api_key_cannot_decrypt_runtime_token(self) -> None:
        """
        Scenario 1.190: runtime token is bound to the per-instance API key.
        Given: metadata encrypted with one LEX_API_KEY
        When: another key attempts to decrypt it
        Then: decryption fails, so one instance cannot read another instance's
              health metadata without its key.
        """
        token = encrypt_runtime_metadata(_API_KEY, {"instance_commit_sha": _COMMIT_SHA})

        with self.assertRaises(
            InvalidToken,
            msg="A different API key must not decrypt this instance's runtime token.",
        ):
            _decrypt_runtime(token, api_key="different-instance-key")

    # -- 1.191 ---------------------------------------------------------
    def test_1_191_encryption_failure_never_breaks_health(self) -> None:
        """
        Scenario 1.191: health degrades to status-only if encryption fails.
        Given: a deployed-looking environment but encryption raises
        When: health payload is built
        Then: it still returns the legacy Healthy status and omits runtime,
              because liveness probes must never fail due to metadata.
        """
        with patch.dict("os.environ", {"LEX_API_KEY": _API_KEY, "COMMIT_SHA": _COMMIT_SHA}, clear=False), \
             patch("lex.lex_app.runtime_health.encrypt_runtime_metadata", side_effect=RuntimeError("boom")):
            payload = build_health_payload()

        self.assertEqual(
            payload,
            {"status": HEALTH_STATUS},
            "Health must degrade to status-only when runtime encryption fails; "
            "metadata must never endanger liveness.",
        )

    # -- 1.192 ---------------------------------------------------------
    def test_1_192_django_health_route_uses_runtime_payload(self) -> None:
        """
        Scenario 1.192: Django /api/health route returns encrypted runtime.
        Given: a deployed-looking environment
        When: a Django test client calls /api/health
        Then: the JSON response includes the stable status and decryptable
              runtime metadata.
        """
        with patch.dict("os.environ", {"LEX_API_KEY": _API_KEY, "COMMIT_SHA": _COMMIT_SHA}, clear=False):
            response = Client().get("/api/health")

        self.assertEqual(response.status_code, 200, "Health route must keep returning HTTP 200.")
        payload = response.json()
        self.assertEqual(payload.get("status"), HEALTH_STATUS, "Django health status changed.")
        self.assertEqual(
            _decrypt_runtime(payload["runtime"]).get("instance_commit_sha"),
            _COMMIT_SHA,
            "Django health must use the same runtime payload as fast ASGI health.",
        )

    # -- 1.193 ---------------------------------------------------------
    def test_1_193_fast_asgi_health_route_uses_runtime_payload(self) -> None:
        """
        Scenario 1.193: fast ASGI /api/health route returns encrypted runtime.
        Given: the production ASGI fast-health path and deployed env vars
        When: the ASGI app handles /api/health without entering Django
        Then: the body contains stable status plus decryptable runtime metadata.
        """
        messages: list[dict] = []

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            messages.append(message)

        with patch.dict("os.environ", {"LEX_API_KEY": _API_KEY, "COMMIT_SHA": _COMMIT_SHA}, clear=False):
            asyncio.run(fast_health.health_asgi_app({"type": "http", "path": "/api/health"}, receive, send))

        start = next(message for message in messages if message["type"] == "http.response.start")
        body = next(message for message in messages if message["type"] == "http.response.body")
        payload = json.loads(body["body"].decode("utf-8"))

        self.assertEqual(start.get("status"), 200, "Fast health must keep returning HTTP 200.")
        self.assertEqual(payload.get("status"), HEALTH_STATUS, "Fast health status changed.")
        self.assertEqual(
            _decrypt_runtime(payload["runtime"]).get("instance_commit_sha"),
            _COMMIT_SHA,
            "Fast health is what production ASGI serves; it must include runtime metadata.",
        )

    # -- 1.194 ---------------------------------------------------------
    def test_1_194_missing_commit_sha_is_marked_unknown(self) -> None:
        """
        Scenario 1.194: absent COMMIT_SHA is explicit in decrypted metadata.
        Given: LEX_API_KEY is present but COMMIT_SHA is empty
        When: the runtime token is decrypted
        Then: the commit SHA is blank and source is ``unknown`` so IC can render
              ``Unknown`` instead of implying a configured value is running.
        """
        with patch.dict("os.environ", {"LEX_API_KEY": _API_KEY, "COMMIT_SHA": ""}, clear=False):
            payload = build_health_payload()

        runtime = _decrypt_runtime(payload["runtime"])
        self.assertEqual(runtime.get("instance_commit_sha"), "", "Missing COMMIT_SHA must stay blank.")
        self.assertEqual(
            runtime.get("commit_sha_source"),
            "unknown",
            "Missing COMMIT_SHA must be labelled unknown, not env.",
        )
