"""
Cluster 16a: MCP embed-view tool — frontend URL resolution & view-type
classification.

Intent
------

``lex/mcp_server/tools/embed.py`` implements the ``lex_embed_view`` MCP tool
(the "MCP Apps" pattern): given a frontend path such as
``/invoices/123/edit``, it must produce a fully-qualified, embeddable URL for
the React frontend (``?embed=true#embed``, with visibility/redirect
overrides applied) plus a human-readable title and a coarse view-type label
("list" / "create" / "detail" / "edit" / "custom"). The module docstring
states the URL-building logic "mirrors
:func:`lex.lex_app.streamlit.embed.lex_view`" and the settings/env priority
is spelled out on ``_resolve_frontend_url``:

1. ``MCP_SERVER["FRONTEND_BASE_URL"]`` (explicit setting) wins.
2. ``REACT_APP_URL`` env var.
3. ``LEX_FRONTEND_URL`` env var.
4. ``http://localhost:8000`` fallback.

A customer-visible failure here is the widget/iframe pointing at the wrong
origin (CSP rejects it, or auth cookies don't match), or the model/host
mislabelling a create form as a detail view.

``lex/mcp_server/config.py``, ``lex/mcp_server/registry.py`` and
``lex/mcp_server/context.py`` — the three sibling modules ``embed.py``
imports — do not exist yet in this checkout (see
``lex/tests/unit/infra/test_mcp_server_sdk_compat.py`` and AGENTS.md: most of
``lex/mcp_server`` lives on an unmerged branch). ``_mcp_server_stubs`` installs
minimal, honest fakes for exactly those three imports so the real
``embed.py`` source runs completely unmodified; nothing about ``embed.py``
itself is mocked.

Scenario numbering starts at 16.1 (new cluster).
"""
from __future__ import annotations

import asyncio
import json
import unittest
import unittest.mock

import pytest

from ._mcp_server_stubs import FakePrincipal, install_mcp_server_stubs

pytestmark = pytest.mark.ai_tooling


# ---------------------------------------------------------------------
# 16.1–16.6 — _resolve_frontend_url / _csp_origins
# ---------------------------------------------------------------------
class TestCluster16a_FrontendUrlResolution(unittest.TestCase):
    """Priority order for resolving the React frontend's base URL."""

    def test_16_1_falls_back_to_localhost_when_nothing_configured(self):
        """16.1: no setting, no env vars -> the documented localhost default."""
        with install_mcp_server_stubs():
            from lex.mcp_server.tools import embed

            with unittest.mock.patch.dict(embed.os.environ, {}, clear=False):
                for key in ("REACT_APP_URL", "LEX_FRONTEND_URL"):
                    embed.os.environ.pop(key, None)
                self.assertEqual(embed._resolve_frontend_url(), "http://localhost:8000")

    def test_16_2_react_app_url_env_var_wins_over_fallback(self):
        """16.2: REACT_APP_URL is used, trailing slash stripped."""
        with install_mcp_server_stubs():
            from lex.mcp_server.tools import embed

            with unittest.mock.patch.dict(
                embed.os.environ,
                {"REACT_APP_URL": "https://app.example.com/"},
                clear=False,
            ):
                embed.os.environ.pop("LEX_FRONTEND_URL", None)
                self.assertEqual(
                    embed._resolve_frontend_url(), "https://app.example.com"
                )

    def test_16_3_lex_frontend_url_used_only_when_react_app_url_absent(self):
        """16.3: LEX_FRONTEND_URL is the second-priority env var."""
        with install_mcp_server_stubs():
            from lex.mcp_server.tools import embed

            with unittest.mock.patch.dict(
                embed.os.environ,
                {"LEX_FRONTEND_URL": "https://legacy.example.com"},
                clear=False,
            ):
                embed.os.environ.pop("REACT_APP_URL", None)
                self.assertEqual(
                    embed._resolve_frontend_url(), "https://legacy.example.com"
                )

    def test_16_4_explicit_setting_outranks_both_env_vars(self):
        """16.4: ``MCP_SERVER["FRONTEND_BASE_URL"]`` beats REACT_APP_URL/LEX_FRONTEND_URL."""
        with install_mcp_server_stubs(
            settings={"FRONTEND_BASE_URL": "https://configured.example.com/"}
        ):
            from lex.mcp_server.tools import embed

            with unittest.mock.patch.dict(
                embed.os.environ,
                {
                    "REACT_APP_URL": "https://react.example.com",
                    "LEX_FRONTEND_URL": "https://legacy.example.com",
                },
            ):
                self.assertEqual(
                    embed._resolve_frontend_url(), "https://configured.example.com"
                )

    def test_16_5_csp_origins_defaults_to_just_the_frontend_origin(self):
        """16.5: with no extra origins configured, CSP allows only the frontend."""
        with install_mcp_server_stubs(
            settings={"FRONTEND_BASE_URL": "https://app.example.com/some/path"}
        ):
            from lex.mcp_server.tools import embed

            self.assertEqual(embed._csp_origins(), ["https://app.example.com"])

    def test_16_6_csp_origins_appends_deduplicated_extra_origins(self):
        """16.6: EMBED_EXTRA_CSP_ORIGINS is appended, trailing slashes stripped,
        duplicates of the frontend origin dropped."""
        with install_mcp_server_stubs(
            settings={
                "FRONTEND_BASE_URL": "https://app.example.com",
                "EMBED_EXTRA_CSP_ORIGINS": [
                    "https://app.example.com",  # duplicate of frontend origin
                    "https://auth.example.com/",
                ],
            }
        ):
            from lex.mcp_server.tools import embed

            self.assertEqual(
                embed._csp_origins(),
                ["https://app.example.com", "https://auth.example.com"],
            )


# ---------------------------------------------------------------------
# 16.7–16.13 — _classify_path
# ---------------------------------------------------------------------
class TestCluster16b_ViewTypeClassification(unittest.TestCase):
    """Path segment patterns -> view-type label (docstring on ``_classify_path``)."""

    def test_16_7_no_segments_is_custom(self):
        with install_mcp_server_stubs():
            from lex.mcp_server.tools import embed

            self.assertEqual(embed._classify_path([]), "custom")

    def test_16_8_single_segment_is_list(self):
        with install_mcp_server_stubs():
            from lex.mcp_server.tools import embed

            self.assertEqual(embed._classify_path(["invoices"]), "list")

    def test_16_9_resource_create_is_create(self):
        with install_mcp_server_stubs():
            from lex.mcp_server.tools import embed

            self.assertEqual(embed._classify_path(["invoices", "create"]), "create")

    def test_16_10_resource_numeric_id_is_detail(self):
        with install_mcp_server_stubs():
            from lex.mcp_server.tools import embed

            self.assertEqual(embed._classify_path(["invoices", "123"]), "detail")

    def test_16_11_resource_uuid_id_is_detail(self):
        with install_mcp_server_stubs():
            from lex.mcp_server.tools import embed

            self.assertEqual(
                embed._classify_path(
                    ["invoices", "3fa85f64-5717-4562-b3fc-2c963f66afa6"]
                ),
                "detail",
            )

    def test_16_12_resource_id_edit_is_edit(self):
        with install_mcp_server_stubs():
            from lex.mcp_server.tools import embed

            self.assertEqual(
                embed._classify_path(["invoices", "123", "edit"]), "edit"
            )

    def test_16_13_resource_show_id_is_detail_and_unknown_shapes_are_custom(self):
        with install_mcp_server_stubs():
            from lex.mcp_server.tools import embed

            self.assertEqual(
                embed._classify_path(["invoices", "show", "123"]), "detail"
            )
            self.assertEqual(
                embed._classify_path(["invoices", "not-an-id"]), "custom"
            )
            self.assertEqual(
                embed._classify_path(["a", "b", "c", "d"]), "custom"
            )


# ---------------------------------------------------------------------
# 16.14–16.16 — _build_title
# ---------------------------------------------------------------------
class TestCluster16c_TitleBuilding(unittest.TestCase):
    def test_16_14_uses_container_verbose_name_when_present(self):
        with install_mcp_server_stubs():
            from lex.mcp_server.tools import embed

            class _Container:
                verbose_name = "sales_invoice"

            title = embed._build_title("invoices", "detail", _Container())
            self.assertEqual(title, "Sales Invoice — Detail View (embedded)")

    def test_16_15_falls_back_to_resource_name_without_a_container(self):
        with install_mcp_server_stubs():
            from lex.mcp_server.tools import embed

            title = embed._build_title("sales-invoice", "list", None)
            self.assertEqual(title, "Sales Invoice — List View (embedded)")

    def test_16_16_falls_back_to_application_with_no_resource_or_container(self):
        with install_mcp_server_stubs():
            from lex.mcp_server.tools import embed

            title = embed._build_title(None, "custom", None)
            self.assertEqual(title, "Application — Custom View (embedded)")


# ---------------------------------------------------------------------
# 16.17–16.22 — _build_embed_url
# ---------------------------------------------------------------------
class TestCluster16d_EmbedUrlBuilder(unittest.TestCase):
    def _parse(self, url):
        import urllib.parse

        parsed = urllib.parse.urlparse(url)
        return parsed, urllib.parse.parse_qs(parsed.query)

    def test_16_17_bare_path_gets_embed_flag_and_embed_fragment(self):
        with install_mcp_server_stubs(
            settings={"FRONTEND_BASE_URL": "https://app.example.com"}
        ):
            from lex.mcp_server.tools import embed

            url = embed._build_embed_url("invoices")
            parsed, query = self._parse(url)
            self.assertEqual(parsed.netloc, "app.example.com")
            self.assertEqual(parsed.path, "/invoices")
            self.assertEqual(query["embed"], ["true"])
            self.assertEqual(parsed.fragment, "embed")

    def test_16_18_visibility_toggles_only_appear_when_true(self):
        with install_mcp_server_stubs(
            settings={"FRONTEND_BASE_URL": "https://app.example.com"}
        ):
            from lex.mcp_server.tools import embed

            url = embed._build_embed_url(
                "/invoices", hide_toolbar=True, hide_actions=False,
                hide_actions_column=True,
            )
            _, query = self._parse(url)
            self.assertEqual(query["hide_toolbar"], ["true"])
            self.assertNotIn("hide_actions", query)
            self.assertEqual(query["hide_actions_column"], ["true"])

    def test_16_19_redirect_overrides_are_passed_through(self):
        with install_mcp_server_stubs(
            settings={"FRONTEND_BASE_URL": "https://app.example.com"}
        ):
            from lex.mcp_server.tools import embed

            url = embed._build_embed_url(
                "/invoices/create",
                redirect_after="/invoices",
                redirect_after_create="/invoices/{id}",
                redirect_after_update="/invoices/{id}/show",
            )
            _, query = self._parse(url)
            self.assertEqual(query["redirect_after"], ["/invoices"])
            self.assertEqual(query["redirect_after_create"], ["/invoices/{id}"])
            self.assertEqual(query["redirect_after_update"], ["/invoices/{id}/show"])

    def test_16_20_extra_params_are_stringified_and_included(self):
        with install_mcp_server_stubs(
            settings={"FRONTEND_BASE_URL": "https://app.example.com"}
        ):
            from lex.mcp_server.tools import embed

            url = embed._build_embed_url(
                "/invoices", extra_params={"auth_token": "abc123", "n": 5}
            )
            _, query = self._parse(url)
            self.assertEqual(query["auth_token"], ["abc123"])
            self.assertEqual(query["n"], ["5"])

    def test_16_21_existing_query_params_are_preserved(self):
        with install_mcp_server_stubs(
            settings={"FRONTEND_BASE_URL": "https://app.example.com"}
        ):
            from lex.mcp_server.tools import embed

            url = embed._build_embed_url("/invoices?sort=asc")
            _, query = self._parse(url)
            self.assertEqual(query["sort"], ["asc"])
            self.assertEqual(query["embed"], ["true"])

    def test_16_22_fragment_is_not_duplicated_when_already_present(self):
        """A path that already carries an ``#embed``-containing fragment must
        not gain a second one appended."""
        with install_mcp_server_stubs(
            settings={"FRONTEND_BASE_URL": "https://app.example.com"}
        ):
            from lex.mcp_server.tools import embed

            url = embed._build_embed_url("/invoices#embed-already-here")
            parsed, _ = self._parse(url)
            self.assertEqual(parsed.fragment, "embed-already-here")


# ---------------------------------------------------------------------
# 16.23–16.28 — _embed_view (the actual MCP tool entry point)
# ---------------------------------------------------------------------
class TestCluster16e_EmbedViewTool(unittest.TestCase):
    """End-to-end through the public entry point (the async MCP tool
    function), not just the private helpers above."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_16_23_happy_path_returns_narration_and_structured_content(self):
        with install_mcp_server_stubs(
            settings={"FRONTEND_BASE_URL": "https://app.example.com"}
        ):
            from lex.mcp_server.tools import embed

            result = self._run(embed._embed_view(path="/invoices"))
            self.assertEqual(len(result), 2)
            narration, structured = result
            self.assertIn("Invoices", narration.text)
            payload = json.loads(structured.text)
            self.assertEqual(payload["view_type"], "list")
            self.assertEqual(payload["resource"], "invoices")
            self.assertTrue(payload["embed_url"].startswith("https://app.example.com/invoices"))

    def test_16_24_custom_route_forces_custom_view_type(self):
        """16.24: known custom routes (e.g. ``process-history``) are never
        classified by path shape, even though a single segment would
        otherwise mean 'list'."""
        with install_mcp_server_stubs(
            settings={"FRONTEND_BASE_URL": "https://app.example.com"}
        ):
            from lex.mcp_server.tools import embed

            result = self._run(embed._embed_view(path="/process-history"))
            payload = json.loads(result[1].text)
            self.assertEqual(payload["view_type"], "custom")

    def test_16_25_container_metadata_is_attached_when_resolvable(self):
        with install_mcp_server_stubs(
            settings={"FRONTEND_BASE_URL": "https://app.example.com"},
            containers={"invoices": object()},
            writable={"invoices": True},
        ):
            from lex.mcp_server.tools import embed

            result = self._run(embed._embed_view(path="/invoices"))
            payload = json.loads(result[1].text)
            self.assertIn("resource_metadata", payload)
            self.assertTrue(payload["resource_metadata"]["writable"])
            self.assertIn("model:", result[0].text)

    def test_16_26_container_lookup_failure_degrades_without_metadata(self):
        """16.26: a broken registry lookup must not crash the tool — it just
        skips resource_metadata (see the ``try/except`` around
        ``get_container``)."""
        with install_mcp_server_stubs(
            settings={"FRONTEND_BASE_URL": "https://app.example.com"},
            container_lookup_error=RuntimeError("registry unavailable"),
        ):
            from lex.mcp_server.tools import embed

            result = self._run(embed._embed_view(path="/invoices"))
            payload = json.loads(result[1].text)
            self.assertNotIn("resource_metadata", payload)

    def test_16_27_access_token_is_injected_into_embed_url_but_not_narration_link(self):
        """16.27: the model-visible narration link must NOT carry the OIDC
        access token — only the widget-only ``embed_url`` in the structured
        payload does."""
        with install_mcp_server_stubs(
            settings={"FRONTEND_BASE_URL": "https://app.example.com"},
            current_principal=lambda: FakePrincipal(access_token="secret-token"),
        ):
            from lex.mcp_server.tools import embed

            result = self._run(embed._embed_view(path="/invoices"))
            narration, structured = result
            payload = json.loads(structured.text)
            self.assertIn("auth_token=secret-token", payload["embed_url"])
            self.assertNotIn("secret-token", narration.text)

    def test_16_28_unbound_principal_does_not_crash_and_omits_token(self):
        """16.28: the default stub raises RuntimeError('no principal bound'),
        mirroring the "no principal bound" runtime condition the real
        ``current_principal()`` raises outside of a request. The tool
        must swallow it, per the ``except RuntimeError: pass`` in the
        source."""
        with install_mcp_server_stubs(
            settings={"FRONTEND_BASE_URL": "https://app.example.com"}
        ):
            from lex.mcp_server.tools import embed

            result = self._run(embed._embed_view(path="/invoices"))
            payload = json.loads(result[1].text)
            self.assertNotIn("auth_token", payload["embed_url"])


if __name__ == "__main__":
    unittest.main()
