"""Unit tests for the new ``lex setup-with-ai`` web wizard helpers.

Covers:

* ``_setup_ai_state`` — last-used persistence; never writes secrets.
* ``_setup_ai_validation`` — token/MCP-key validators with mocked HTTP.
* ``_setup_ai_github`` — Device Flow start/poll with mocked HTTP.
* ``_setup_ai_templates`` — wizard / success page rendering.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lex.tools._setup_ai_github import (
    DeviceFlowError,
    DeviceFlowUnavailable,
    is_device_flow_available,
    poll_device_flow,
    resolve_client_id,
    start_device_flow,
)
from lex.tools._setup_ai_state import (
    SetupWithAILastUsed,
    load_last_used,
    save_last_used,
)
from lex.tools._setup_ai_templates import render_setup_wizard, render_success_page
from lex.tools._setup_ai_validation import (
    REQUIRED_GITHUB_SCOPES,
    GithubTokenValidation,
    RemoteMcpKeyValidation,
    validate_github_token,
    validate_remote_mcp_key,
)


class _FakeHTTPResponse:
    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None):
        self._body = body
        self.status = status
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class LastUsedSettingsTests(unittest.TestCase):
    def test_round_trip_preserves_non_secret_fields(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            env = {"LEX_SETUP_AI_STATE_PATH": str(path)}
            saved = save_last_used(
                SetupWithAILastUsed(
                    mcp_mode="backward",
                    remote_mcp_url="https://example.test/mcp",
                    last_project_root="/tmp/proj",
                    prefer_pat=True,
                ),
                env=env,
            )
            self.assertTrue(saved.exists())
            loaded = load_last_used(env=env)
            self.assertEqual(loaded.mcp_mode, "backward")
            self.assertEqual(loaded.remote_mcp_url, "https://example.test/mcp")
            self.assertEqual(loaded.last_project_root, "/tmp/proj")
            self.assertTrue(loaded.prefer_pat)

    def test_secret_fields_are_never_persisted(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            env = {"LEX_SETUP_AI_STATE_PATH": str(path)}
            settings = SetupWithAILastUsed(
                mcp_mode="forward",
                extras={"github_token": "ghp_secret", "api_key": "k", "harmless": "ok"},
            )
            save_last_used(settings, env=env)
            raw = json.loads(path.read_text(encoding="utf-8"))
            # Top-level forbidden keys removed.
            self.assertNotIn("github_token", raw)
            # Forbidden keys inside extras also removed.
            self.assertNotIn("github_token", raw.get("extras", {}))
            self.assertNotIn("api_key", raw.get("extras", {}))
            self.assertEqual(raw.get("extras", {}).get("harmless"), "ok")

    def test_load_returns_defaults_when_missing(self):
        with TemporaryDirectory() as tmp:
            env = {"LEX_SETUP_AI_STATE_PATH": str(Path(tmp) / "missing.json")}
            loaded = load_last_used(env=env)
            self.assertEqual(loaded.mcp_mode, "forward")
            self.assertFalse(loaded.prefer_pat)


class GithubTokenValidationTests(unittest.TestCase):
    def test_empty_token_short_circuits(self):
        v = validate_github_token("")
        self.assertFalse(v.ok)
        self.assertIn("empty", v.error.lower())

    def test_valid_token_parses_login_and_scopes(self):
        body = json.dumps({"login": "octocat", "name": "The Octocat"}).encode("utf-8")
        # Granted scopes include all required ones plus an unrelated extra.
        scope_header = ", ".join(list(REQUIRED_GITHUB_SCOPES) + ["read:packages"])
        fake = _FakeHTTPResponse(body, 200, {"X-OAuth-Scopes": scope_header})
        with patch("lex.tools._setup_ai_validation.urllib.request.urlopen", return_value=fake):
            v = validate_github_token("ghp_dummy")
        self.assertTrue(v.ok)
        self.assertEqual(v.login, "octocat")
        self.assertEqual(v.name, "The Octocat")
        self.assertEqual(v.missing_required_scopes, ())

    def test_missing_required_scopes_reported(self):
        body = json.dumps({"login": "octocat"}).encode("utf-8")
        # Only 'user' granted out of the required set.
        fake = _FakeHTTPResponse(body, 200, {"X-OAuth-Scopes": "user"})
        with patch("lex.tools._setup_ai_validation.urllib.request.urlopen", return_value=fake):
            v = validate_github_token("ghp_dummy")
        self.assertTrue(v.ok)
        self.assertIn("repo", v.missing_required_scopes)
        self.assertIn("workflow", v.missing_required_scopes)

    def test_401_marked_unauthorised(self):
        import urllib.error

        err = urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, None)
        with patch("lex.tools._setup_ai_validation.urllib.request.urlopen", side_effect=err):
            v = validate_github_token("ghp_bad")
        self.assertFalse(v.ok)
        self.assertIn("401", v.error)


class RemoteMcpKeyValidationTests(unittest.TestCase):
    def test_empty_key_short_circuits(self):
        v = validate_remote_mcp_key("https://x", "")
        self.assertFalse(v.ok)

    def test_200_means_ok(self):
        fake = _FakeHTTPResponse(b"{}", 200, {})
        with patch("lex.tools._setup_ai_validation.urllib.request.urlopen", return_value=fake):
            v = validate_remote_mcp_key("https://mcp.example/mcp", "k")
        self.assertTrue(v.ok)
        self.assertEqual(v.status_code, 200)

    def test_401_means_rejected(self):
        import urllib.error

        err = urllib.error.HTTPError("http://x", 401, "no", {}, None)
        with patch("lex.tools._setup_ai_validation.urllib.request.urlopen", side_effect=err):
            v = validate_remote_mcp_key("https://mcp.example/mcp", "k")
        self.assertFalse(v.ok)
        self.assertEqual(v.status_code, 401)

    def test_405_treated_as_ok(self):
        import urllib.error

        err = urllib.error.HTTPError("http://x", 405, "no", {}, None)
        with patch("lex.tools._setup_ai_validation.urllib.request.urlopen", side_effect=err):
            v = validate_remote_mcp_key("https://mcp.example/mcp", "k")
        self.assertTrue(v.ok)
        self.assertEqual(v.status_code, 405)


class GithubDeviceFlowTests(unittest.TestCase):
    def test_unavailable_when_no_client_id(self):
        # An empty client_id means the device flow is disabled — the wizard
        # falls back to PAT paste in that case.
        env = {"LEX_GITHUB_OAUTH_CLIENT_ID": ""}
        self.assertFalse(is_device_flow_available(env=env))
        with self.assertRaises(DeviceFlowUnavailable):
            start_device_flow(env=env)
        with self.assertRaises(DeviceFlowUnavailable):
            poll_device_flow("dummy", env=env)

    def test_start_returns_device_code(self):
        env = {"LEX_GITHUB_OAUTH_CLIENT_ID": "Iv1.fake"}
        body = json.dumps({
            "device_code": "secret-dev",
            "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device",
            "verification_uri_complete": "https://github.com/login/device?user_code=ABCD-1234",
            "expires_in": 900,
            "interval": 5,
        }).encode("utf-8")
        fake = _FakeHTTPResponse(body, 200, {})
        with patch("lex.tools._setup_ai_github.urllib.request.urlopen", return_value=fake):
            code = start_device_flow(env=env)
        self.assertEqual(code.user_code, "ABCD-1234")
        self.assertEqual(code.device_code, "secret-dev")
        # to_public_dict must NOT leak the device_code.
        self.assertNotIn("device_code", code.to_public_dict())

    def test_poll_pending(self):
        env = {"LEX_GITHUB_OAUTH_CLIENT_ID": "Iv1.fake"}
        body = json.dumps({"error": "authorization_pending"}).encode("utf-8")
        fake = _FakeHTTPResponse(body, 200, {})
        with patch("lex.tools._setup_ai_github.urllib.request.urlopen", return_value=fake):
            r = poll_device_flow("dev", env=env)
        self.assertEqual(r.status, "pending")

    def test_poll_authorized_returns_token(self):
        env = {"LEX_GITHUB_OAUTH_CLIENT_ID": "Iv1.fake"}
        body = json.dumps({
            "access_token": "gho_test",
            "token_type": "bearer",
            "scope": "repo,workflow,user",
        }).encode("utf-8")
        fake = _FakeHTTPResponse(body, 200, {})
        with patch("lex.tools._setup_ai_github.urllib.request.urlopen", return_value=fake):
            r = poll_device_flow("dev", env=env)
        self.assertEqual(r.status, "authorized")
        self.assertEqual(r.access_token, "gho_test")
        self.assertIn("repo", r.scopes)

    def test_poll_slow_down(self):
        env = {"LEX_GITHUB_OAUTH_CLIENT_ID": "Iv1.fake"}
        body = json.dumps({"error": "slow_down", "interval": 10}).encode("utf-8")
        fake = _FakeHTTPResponse(body, 200, {})
        with patch("lex.tools._setup_ai_github.urllib.request.urlopen", return_value=fake):
            r = poll_device_flow("dev", env=env)
        self.assertEqual(r.status, "slow_down")
        self.assertEqual(r.interval, 10)


class SetupWizardTemplateTests(unittest.TestCase):
    def test_wizard_renders_with_bootstrap(self):
        out = render_setup_wizard(
            state="state-123",
            project_root=Path("/tmp/proj"),
            env_file_path=Path("/tmp/proj/.env"),
            remote_mcp_url="https://mcp.example/mcp",
            github_token_url="https://github.com/settings/tokens/new?scopes=repo",
            server_name="lex-mcp-local",
            last_used_mcp_mode="backward",
            last_used_prefer_pat=True,
            device_flow_available=False,
        )
        self.assertIn('id="lex-bootstrap"', out)
        self.assertIn("panel-auth", out)
        self.assertIn("Sign in with GitHub", out)
        self.assertIn("backward", out)

    def test_success_page_includes_deep_links(self):
        out = render_success_page(
            project_root=Path("/tmp/my proj"),
            env_file_path=Path("/tmp/my proj/.env"),
            server_name="lex-mcp-local",
        )
        self.assertIn("vscode://file/", out)
        self.assertIn("cursor://file/", out)
        self.assertIn("jetbrains://idea", out)
        # Spaces in the path must be URL-encoded.
        self.assertIn("my%20proj", out)


if __name__ == "__main__":
    unittest.main()
