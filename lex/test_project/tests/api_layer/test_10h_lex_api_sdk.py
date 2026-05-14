"""
Sub-cluster 10h — `LexAPI` outbound-client SDK.

Coverage-driven batch (May 12 ROI rank #5 — `lex/api/views/lex_api/LexAPI.py`
14.58% baseline, 38 stmts / 31 missed). Tiny file, easy +0.3% project-wide.

What it is
----------
The SDK customer apps (and framework internals) call to reach back into
the lex platform's central email + Keycloak APIs from inside a worker
or a customer task: `send_email(...)`, `get_client_roles(...)`,
`build_attachments_from_paths(...)` for inline attachments.

Why it matters
--------------
* Every customer-side scheduled report goes through `send_email` —
  a regression that drops `attachments` or flips the auth header
  silently breaks the entire customer-notifications surface.
* The DEPLOYMENT_ENVIRONMENT env-var no-op is the documented "your
  test runs don't blast real emails to customers" guard.
* `get_client_roles` is the only customer-callable Keycloak read API
  in the SDK; an attacker getting a 200 with role-data on a 403 path
  is a privilege-leak surface.

Scenarios 10.17 – 10.23.
"""

from __future__ import annotations

import base64
import os
import unittest
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from lex.api.views.lex_api import LexAPI


# ----------------------------------------------------------------------
# 10.17  build_attachments_from_paths
# ----------------------------------------------------------------------


class TestCluster10h_BuildAttachments(SimpleTestCase):
    """`build_attachments_from_paths` — base64 + mimetype + basename."""

    def test_10_17_attachments_carry_basename_b64_and_resolved_mimetype(self):
        """Each path → {name=basename, content_base64, content_type}.

        Three branches in one scenario: known mimetype (`.pdf` →
        application/pdf), missing mimetype (no extension → octet-stream
        fallback per the documented contract), and base64 round-trip.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = os.path.join(tmp, "report.pdf")
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-x")

            blob_path = os.path.join(tmp, "anonymous_blob")
            with open(blob_path, "wb") as f:
                f.write(b"raw-bytes")

            result = LexAPI.build_attachments_from_paths([pdf_path, blob_path])

        self.assertEqual(len(result), 2)

        # PDF — known mimetype.
        self.assertEqual(result[0]["name"], "report.pdf")
        self.assertEqual(result[0]["content_type"], "application/pdf")
        self.assertEqual(
            base64.b64decode(result[0]["content_base64"]), b"%PDF-x",
            "Base64 round-trip must reconstruct the original bytes — a "
            "regression here would silently corrupt every attachment.",
        )

        # Anonymous blob — missing mimetype falls back to octet-stream.
        self.assertEqual(result[1]["name"], "anonymous_blob")
        self.assertEqual(
            result[1]["content_type"], "application/octet-stream",
            "Unknown extension must fall back to the documented "
            "octet-stream default — silent drop to None would crash "
            "every downstream MIME parser.",
        )


# ----------------------------------------------------------------------
# 10.18 – 10.21  send_email
# ----------------------------------------------------------------------


class TestCluster10h_SendEmail(SimpleTestCase):
    """`send_email` — env-gated POST to /api/send_email/."""

    def _env(self, **overrides):
        """Default deploy env so the early-return guard is bypassed."""
        base = {
            "DEPLOYMENT_ENVIRONMENT": "production",
            "DOMAIN_BASE": "lex.example.com",
            "LEX_API_KEY": "test-key",
        }
        base.update(overrides)
        return base

    # 10.18 ------------------------------------------------------------
    def test_10_18_no_op_when_deployment_environment_unset(self) -> None:
        """`DEPLOYMENT_ENVIRONMENT` blank → silent no-op (never POST).

        This is the documented "test runs don't blast real emails"
        guard. A regression that removed the early-return would mean
        every CI run sends production-ish emails — the kind of bug
        you only learn about from a customer screenshot.
        """
        with patch.dict(os.environ, {"DEPLOYMENT_ENVIRONMENT": ""}, clear=False):
            with patch.object(LexAPI.requests, "post") as mock_post:
                result = LexAPI.send_email(
                    subject="x", emails=["a@b"], body="b"
                )
        self.assertIsNone(result)
        mock_post.assert_not_called()

    # 10.19 ------------------------------------------------------------
    def test_10_19_happy_path_posts_with_auth_and_returns_json(self) -> None:
        """200 → returns response.json(); URL/headers/body assembled correctly.

        Pins the `Api-Key` auth-header format and the URL template the
        platform's email service routes on. A drift in either silently
        500s every customer email.
        """
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"queued": True, "id": "msg-1"}

        with patch.dict(os.environ, self._env(), clear=False):
            with patch.object(
                LexAPI.requests, "post", return_value=fake_response
            ) as mock_post:
                result = LexAPI.send_email(
                    subject="Hello",
                    emails=["a@example.com", "b@example.com"],
                    body="Body text",
                )

        self.assertEqual(result, {"queued": True, "id": "msg-1"})
        # Asserted call shape.
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://lex.example.com/api/send_email/")
        self.assertEqual(
            kwargs["headers"]["Authorization"], "Api-Key test-key",
            "Auth header drift breaks every outbound email at the "
            "platform service boundary.",
        )
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(
            kwargs["json"],
            {
                "subject": "Hello",
                "emails": ["a@example.com", "b@example.com"],
                "body": "Body text",
            },
            "No `attachments` key when none supplied — pin so the "
            "platform service doesn't accidentally reject a payload "
            "with a stray empty list.",
        )

    # 10.20 ------------------------------------------------------------
    def test_10_20_attachments_only_included_when_truthy(self) -> None:
        """Attachments key absent for None/[]; present when populated."""
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"ok": True}

        with patch.dict(os.environ, self._env(), clear=False):
            # Empty list → attachments key absent.
            with patch.object(
                LexAPI.requests, "post", return_value=fake_response
            ) as mock_post:
                LexAPI.send_email(
                    subject="x", emails=["a"], body="b", attachments=[]
                )
            self.assertNotIn("attachments", mock_post.call_args.kwargs["json"])

            # Populated list → present verbatim.
            attachments = [
                {"name": "f.pdf", "content_base64": "Zg==",
                 "content_type": "application/pdf"}
            ]
            with patch.object(
                LexAPI.requests, "post", return_value=fake_response
            ) as mock_post:
                LexAPI.send_email(
                    subject="x", emails=["a"], body="b",
                    attachments=attachments,
                )
            self.assertEqual(
                mock_post.call_args.kwargs["json"]["attachments"],
                attachments,
            )

    # 10.21 ------------------------------------------------------------
    def test_10_21_non_200_raises(self) -> None:
        """Non-200 response → raises so callers can route through their
        error handler instead of silently believing the email landed.
        """
        fake_response = MagicMock(status_code=503, text="upstream down")
        with patch.dict(os.environ, self._env(), clear=False):
            with patch.object(
                LexAPI.requests, "post", return_value=fake_response
            ):
                with self.assertRaises(Exception) as ctx:
                    LexAPI.send_email(
                        subject="x", emails=["a"], body="b"
                    )
        self.assertIn("Failed to send email", str(ctx.exception))


# ----------------------------------------------------------------------
# 10.22 – 10.23  get_client_roles
# ----------------------------------------------------------------------


class TestCluster10h_GetClientRoles(SimpleTestCase):
    """`get_client_roles` — Keycloak roles read-through."""

    def _env(self, **overrides):
        base = {
            "DOMAIN_BASE": "lex.example.com",
            "LEX_API_KEY": "test-key",
            "KEYCLOAK_INTERNAL_CLIENT_ID": "abc-123",
        }
        base.update(overrides)
        return base

    # 10.22 ------------------------------------------------------------
    def test_10_22_happy_path_returns_roles_with_correct_url_and_params(self):
        """200 → returns response.json(); URL + params + auth assembled correctly.

        `keycloak_client_id` lands in `params=` (querystring), not the
        body — the platform service's GET-only handler depends on this.
        """
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = [
            {"name": "admin"}, {"name": "user"}
        ]

        with patch.dict(os.environ, self._env(), clear=False):
            with patch.object(
                LexAPI.requests, "get", return_value=fake_response
            ) as mock_get:
                result = LexAPI.get_client_roles()

        self.assertEqual(result, [{"name": "admin"}, {"name": "user"}])
        args, kwargs = mock_get.call_args
        self.assertEqual(
            args[0], "https://lex.example.com/api/get_client_roles/"
        )
        self.assertEqual(
            kwargs["params"],
            {"keycloak_client_id": "abc-123"},
            "client id must travel as querystring, not in the body — "
            "the platform's GET handler ignores body data.",
        )
        self.assertEqual(
            kwargs["headers"]["Authorization"], "Api-Key test-key"
        )

    # 10.23 ------------------------------------------------------------
    def test_10_23_non_200_raises(self) -> None:
        """403/500/etc. raises — never silently returns empty role list.

        A regression that returned `[]` on 403 would silently grant
        zero-role access to every caller, locking customers out of
        every protected page without any visible error.
        """
        fake_response = MagicMock(status_code=403, text="forbidden")
        with patch.dict(os.environ, self._env(), clear=False):
            with patch.object(
                LexAPI.requests, "get", return_value=fake_response
            ):
                with self.assertRaises(Exception) as ctx:
                    LexAPI.get_client_roles()
        self.assertIn("Failed to get client roles", str(ctx.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

