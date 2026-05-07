"""MCP tool + UI resource for embedding React frontend views.

Implements the **MCP Apps** pattern so that any compliant MCP Apps host
(Claude Desktop, Claude.ai, ChatGPT, VS Code Copilot, Goose, …) renders
the React frontend inside a sandboxed iframe widget rather than just
returning a plain URL string.

Architecture
~~~~~~~~~~~~
1. A **UI resource** (``ui://lex/embed-view.html``) is registered with
   MIME type ``text/html;profile=mcp-app``.  Its HTML contains a thin
   widget that listens for ``ui/notifications/tool-result`` over the
   MCP Apps bridge (``postMessage``), reads ``structuredContent.embed_url``
   from the tool result, and sets that as the ``<iframe src>``.

2. The **tool** (``lex_embed_view``) returns a ``CallToolResult`` with:
   - ``structuredContent``  — JSON with ``embed_url``, ``title``, etc.
   - ``content``            — narration text for the model.
   - ``_meta``              — widget-only data (empty for now) plus
     ``ui.resourceUri`` linking the tool to the widget.

3. ``_meta.ui.csp.frameDomains`` declares the React frontend origin so
   the sandbox allows the inner iframe.

URL-building logic mirrors :func:`lex.lex_app.streamlit.embed.lex_view`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import string
import textwrap
import urllib.parse
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import FunctionResource
from mcp.types import TextContent, ToolAnnotations

from lex.mcp_server.config import mcp_setting
from lex.mcp_server.registry import container_is_writable, get_container

logger = logging.getLogger(__name__)

# Known custom routes that don't map to a model container.
_CUSTOM_ROUTES: set = {
    "process-history",
    "calculation_log_tree",
    "delete-unused-files",
    "server-not-ready",
}

# View-type labels for human-readable titles.
_VIEW_TYPE_LABELS: Dict[str, str] = {
    "list": "List View",
    "create": "Create Form",
    "detail": "Detail View",
    "edit": "Edit Form",
    "custom": "Custom View",
}


# --------------------------------------------------------------------------- #
# URL resolution                                                              #
# --------------------------------------------------------------------------- #


def _resolve_frontend_url() -> str:
    """Resolve the base URL of the React frontend.

    Priority:
    1. ``MCP_SERVER["FRONTEND_BASE_URL"]`` (explicit setting).
    2. ``REACT_APP_URL`` environment variable.
    3. ``LEX_FRONTEND_URL`` environment variable.
    4. ``http://localhost:8000`` fallback.
    """
    configured = mcp_setting("FRONTEND_BASE_URL")
    if configured:
        return str(configured).rstrip("/")
    return (
        os.getenv("REACT_APP_URL")
        or os.getenv("LEX_FRONTEND_URL")
        or "http://localhost:8000"
    ).rstrip("/")


def _csp_origins() -> list:
    """Build the list of origins allowed in the widget CSP.

    Always includes the frontend origin.  Additional origins (e.g. the
    Keycloak auth provider) come from ``EMBED_EXTRA_CSP_ORIGINS``.
    """
    frontend = _resolve_frontend_url()
    parsed = urllib.parse.urlparse(frontend)
    origins = [f"{parsed.scheme}://{parsed.netloc}"]
    extra = mcp_setting("EMBED_EXTRA_CSP_ORIGINS")
    if extra:
        for o in extra:
            normed = str(o).rstrip("/")
            if normed and normed not in origins:
                origins.append(normed)
    return origins


# --------------------------------------------------------------------------- #
# View-type detection                                                         #
# --------------------------------------------------------------------------- #

# Matches a path segment that is a numeric id or UUID.
_ID_RE = re.compile(
    r"^(?:\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


def _classify_path(segments: List[str]) -> str:
    """Return the view type for a normalised list of non-empty path segments.

    Patterns:
        /{resource}                → "list"
        /{resource}/create         → "create"
        /{resource}/{id}           → "detail"
        /{resource}/{id}/edit      → "edit"
        /{resource}/show/{id}      → "detail"
        anything else              → "custom"
    """
    n = len(segments)
    if n == 0:
        return "custom"
    if n == 1:
        return "list"
    if n == 2:
        if segments[1] == "create":
            return "create"
        if _ID_RE.match(segments[1]):
            return "detail"
        return "custom"
    if n == 3:
        if _ID_RE.match(segments[1]) and segments[2] == "edit":
            return "edit"
        if segments[1] == "show" and _ID_RE.match(segments[2]):
            return "detail"
    return "custom"


def _build_title(resource: Optional[str], view_type: str, container: Any) -> str:
    """Generate a human-readable title for the embed URL."""
    label = _VIEW_TYPE_LABELS.get(view_type, "View")
    if container is not None:
        name = getattr(container, "verbose_name", None) or resource or "Unknown"
        # Capitalise first letter of verbose_name.
        name = str(name).replace("_", " ").title()
    elif resource:
        name = resource.replace("_", " ").replace("-", " ").title()
    else:
        name = "Application"
    return f"{name} — {label} (embedded)"


# --------------------------------------------------------------------------- #
# URL builder                                                                 #
# --------------------------------------------------------------------------- #


def _build_embed_url(
    path: str,
    *,
    hide_toolbar: bool = False,
    hide_actions: bool = False,
    hide_actions_column: bool = False,
    redirect_after: Optional[str] = None,
    redirect_after_create: Optional[str] = None,
    redirect_after_update: Optional[str] = None,
    extra_params: Optional[Dict[str, str]] = None,
) -> str:
    """Build a fully-qualified embed URL for the React frontend.

    Logic mirrors :func:`lex.lex_app.streamlit.embed.lex_view` but
    returns the URL string instead of rendering an iframe.
    """
    base = _resolve_frontend_url()

    # Normalise path — ensure leading slash.
    if path and not path.startswith("/"):
        path = f"/{path}"

    raw_url = f"{base}{path}"
    parsed = urllib.parse.urlparse(raw_url)

    # ── Build query params ──
    params: Dict[str, list] = urllib.parse.parse_qs(parsed.query)

    # Core embed flag.
    params["embed"] = ["true"]

    # Visibility toggles.
    if hide_toolbar:
        params["hide_toolbar"] = ["true"]
    if hide_actions:
        params["hide_actions"] = ["true"]
    if hide_actions_column:
        params["hide_actions_column"] = ["true"]

    # Post-operation redirect overrides.
    if redirect_after:
        params["redirect_after"] = [redirect_after]
    if redirect_after_create:
        params["redirect_after_create"] = [redirect_after_create]
    if redirect_after_update:
        params["redirect_after_update"] = [redirect_after_update]

    # Extra user-supplied params.
    if extra_params:
        for key, value in extra_params.items():
            params[key] = [str(value)]

    new_query = urllib.parse.urlencode(params, doseq=True)

    # ── Ensure #embed fragment ──
    fragment = parsed.fragment
    if "embed" not in (fragment or ""):
        fragment = f"{fragment}#embed" if fragment else "embed"

    return urllib.parse.urlunparse(
        parsed._replace(query=new_query, fragment=fragment)
    )


# --------------------------------------------------------------------------- #
# Widget HTML template                                                        #
# --------------------------------------------------------------------------- #

_RESOURCE_URI = "ui://lex/embed-view.html"
_RESOURCE_MIME = "text/html;profile=mcp-app"

# Inline widget — implements the MCP Apps postMessage protocol directly
# without external dependencies.  Uses string.Template ($var) to avoid
# the double-brace escaping that str.format() requires.
#
# Iframe-only: renders the React app inside a nested <iframe>.
# Relies on the host honouring ``frameDomains`` in the resource CSP
# metadata (works on ChatGPT, VS Code Copilot, and other compliant
# MCP Apps hosts).
_WIDGET_TEMPLATE = string.Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="color-scheme" content="light dark" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; width: 100%; height: 100%; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    color: #333; background: #fff;
  }
  #loading {
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; min-height: 160px;
    padding: 24px 16px; text-align: center;
  }
  .spinner {
    display: inline-block; width: 24px; height: 24px;
    border: 3px solid #ddd; border-top-color: #0066cc;
    border-radius: 50%; animation: spin 0.8s linear infinite;
    margin-bottom: 12px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  #embed-frame {
    display: none; border: none;
    width: 100%; height: 600px;
  }
</style>
</head>
<body>
<div id="loading">
  <div class="spinner"></div>
  <div>Loading view&hellip;</div>
</div>
<iframe id="embed-frame" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>
<script>
(function() {
  var BASE_URL = '$frontend_base_url';
  var loading = document.getElementById('loading');
  var frame = document.getElementById('embed-frame');
  var loaded = false;
  var host = (window.parent !== window) ? window.parent : null;
  var reqId = 1;
  var PROTO = '2026-01-26';
  var handshakeDone = false;
  var pendingMessages = [];

  function log(m) { console.log('[lex-embed] ' + m); }

  function send(msg) {
    if (!host) return;
    host.postMessage(msg, '*');
  }

  function reportSize(h) {
    var w = Math.ceil(document.documentElement.scrollWidth) || 800;
    h = h || Math.ceil(document.documentElement.scrollHeight) || 200;
    send({ jsonrpc: '2.0', method: 'ui/notifications/size-changed', params: { width: w, height: h } });
  }

  function buildUrl(args) {
    if (!args || !args.path) return null;
    var p = args.path.replace(/^\\/+/, '');
    var url = BASE_URL + '/' + p;
    var qs = ['embed=true'];
    if (args.hide_toolbar) qs.push('hide_toolbar=true');
    if (args.hide_actions) qs.push('hide_actions=true');
    if (args.hide_actions_column) qs.push('hide_actions_column=true');
    if (args.redirect_after) qs.push('redirect_after=' + encodeURIComponent(args.redirect_after));
    if (args.redirect_after_create) qs.push('redirect_after_create=' + encodeURIComponent(args.redirect_after_create));
    if (args.redirect_after_update) qs.push('redirect_after_update=' + encodeURIComponent(args.redirect_after_update));
    if (args.extra_params) {
      for (var k in args.extra_params) {
        qs.push(encodeURIComponent(k) + '=' + encodeURIComponent(args.extra_params[k]));
      }
    }
    return url + '?' + qs.join('&') + '#embed';
  }

  function showIframe(url, title, force) {
    if (loaded && !force) return;
    if (loaded && force && frame.src === url) return;
    loaded = true;
    log('Embedding iframe: ' + url + (force ? ' (updated)' : ''));
    loading.style.display = 'none';
    frame.src = url;
    if (title) frame.title = title;
    frame.style.display = 'block';
    reportSize(620);
  }

  /* ---- Extract embed_url from any value (deep + regex) ---- */
  function extractUrl(val) {
    if (!val) return null;
    if (typeof val === 'object') {
      if (val.embed_url) return val.embed_url;
      for (var k in val) {
        if (!val.hasOwnProperty(k)) continue;
        var r = extractUrl(val[k]);
        if (r) return r;
      }
      return null;
    }
    if (typeof val === 'string') {
      try { var parsed = JSON.parse(val); var r = extractUrl(parsed); if (r) return r; } catch(e) {}
      var m = val.match(/"?embed_url"?\\s*[:=]\\s*"([^"]+)"/);
      if (m) return m[1];
    }
    return null;
  }

  /* ---- Extract args from tool-input params ---- */
  function findArgs(params) {
    if (!params) return null;
    if (params.arguments) return params.arguments;
    if (params.input && params.input.arguments) return params.input.arguments;
    return null;
  }

  /* ---- Process a buffered tool message ---- */
  function processToolMessage(msg) {
    if (msg.method === 'ui/notifications/tool-input-partial' || msg.method === 'ui/notifications/tool-input') {
      var a = findArgs(msg.params);
      log('(buffered) ' + msg.method + ' args: ' + JSON.stringify(a));
      if (!loaded && a && a.path) {
        var u = buildUrl(a);
        if (u) showIframe(u, a.path);
      }
      return;
    }
    if (msg.method === 'ui/notifications/tool-result') {
      var url = extractUrl(msg.params);
      if (url) {
        log('(buffered) Found embed_url: ' + url);
        /* Always apply — tool-result has the full URL with auth_token */
        showIframe(url, '', true);
      }
      return;
    }
  }

  /* ---- Drain pending messages after handshake ---- */
  function drainPending() {
    var msgs = pendingMessages;
    pendingMessages = [];
    log('Draining ' + msgs.length + ' buffered messages');
    for (var i = 0; i < msgs.length; i++) {
      processToolMessage(msgs[i]);
    }
  }

  /* ---- Message handler — registered BEFORE handshake ---- */
  window.addEventListener('message', function(ev) {
    var msg = ev.data;
    if (!msg || typeof msg !== 'object' || msg.jsonrpc !== '2.0') return;
    log('MSG: ' + (msg.method || 'rsp:' + msg.id));

    /* Ping */
    if (msg.method === 'ping' && msg.id != null) {
      send({ jsonrpc: '2.0', id: msg.id, result: {} });
      return;
    }

    /* ui/initialize response */
    if (msg.id != null && msg.id == 1 && msg.result) {
      log('Handshake OK — caps: ' + JSON.stringify(Object.keys((msg.result && msg.result.hostCapabilities) || {})));
      handshakeDone = true;
      send({ jsonrpc: '2.0', method: 'ui/notifications/initialized' });
      reportSize(200);
      drainPending();
      return;
    }

    /* Tool input partial */
    if (msg.method === 'ui/notifications/tool-input-partial') {
      if (!handshakeDone) { pendingMessages.push(msg); return; }
      var pa = findArgs(msg.params);
      log('tool-input-partial args: ' + JSON.stringify(pa));
      if (!loaded && pa && pa.path) {
        var u = buildUrl(pa);
        if (u) showIframe(u, pa.path);
      }
      return;
    }

    /* Tool input */
    if (msg.method === 'ui/notifications/tool-input') {
      if (!handshakeDone) { pendingMessages.push(msg); return; }
      var a = findArgs(msg.params);
      log('tool-input args: ' + JSON.stringify(a));
      if (!loaded && a && a.path) {
        var u = buildUrl(a);
        if (u) showIframe(u, a.path);
      }
      return;
    }

    /* Tool result */
    if (msg.method === 'ui/notifications/tool-result') {
      if (!handshakeDone) { pendingMessages.push(msg); return; }
      var url = extractUrl(msg.params);
      log('tool-result: ' + JSON.stringify(msg.params).substring(0, 300));
      if (url) {
        log('Found embed_url: ' + url);
        /* Always apply — tool-result has the full URL with auth_token */
        showIframe(url, '', true);
      } else if (!loaded) {
        log('embed_url NOT found in tool-result');
      }
      return;
    }

    /* Tool cancelled */
    if (msg.method === 'ui/notifications/tool-cancelled') {
      loading.innerHTML = '<div>Tool call was cancelled.</div>';
      return;
    }

    /* Resource teardown */
    if (msg.method === 'ui/resource-teardown' && msg.id != null) {
      send({ jsonrpc: '2.0', id: msg.id, result: {} });
      return;
    }
  });

  /* ---- Start handshake ---- */
  if (host) {
    log('Init');
    send({
      jsonrpc: '2.0',
      id: reqId++,
      method: 'ui/initialize',
      params: {
        protocolVersion: PROTO,
        appInfo: { name: 'Lex Embed View', version: '1.0.0' },
        appCapabilities: {}
      }
    });
  } else {
    loading.innerHTML = '<div>Not running inside an MCP host.</div>';
  }
})();
</script>
</body>
</html>
""")


# --------------------------------------------------------------------------- #
# Tool implementation                                                         #
# --------------------------------------------------------------------------- #


async def _embed_view(
    path: str = "",
    hide_toolbar: bool = False,
    hide_actions: bool = False,
    hide_actions_column: bool = False,
    redirect_after: Optional[str] = None,
    redirect_after_create: Optional[str] = None,
    redirect_after_update: Optional[str] = None,
    extra_params: Optional[Dict[str, str]] = None,
) -> list:
    """Generate an embeddable URL for a React frontend view.

    Returns a list of ``TextContent`` blocks.  The embed widget (registered
    via ``_meta.ui.resourceUri`` on the tool definition) receives the full
    tool result via the MCP Apps bridge and extracts ``embed_url`` from it.
    """
    try:
        return await _embed_view_inner(
            path=path,
            hide_toolbar=hide_toolbar,
            hide_actions=hide_actions,
            hide_actions_column=hide_actions_column,
            redirect_after=redirect_after,
            redirect_after_create=redirect_after_create,
            redirect_after_update=redirect_after_update,
            extra_params=extra_params,
        )
    except Exception:
        logger.exception("lex_embed_view failed")
        raise


async def _embed_view_inner(
    path: str = "",
    hide_toolbar: bool = False,
    hide_actions: bool = False,
    hide_actions_column: bool = False,
    redirect_after: Optional[str] = None,
    redirect_after_create: Optional[str] = None,
    redirect_after_update: Optional[str] = None,
    extra_params: Optional[Dict[str, str]] = None,
) -> list:
    from lex.mcp_server.context import current_principal

    # Normalise path for analysis.
    clean = path.strip().strip("/")
    segments = [s for s in clean.split("/") if s] if clean else []

    # ── Resolve resource / container ──
    resource: Optional[str] = segments[0] if segments else None
    container = None
    resource_metadata: Optional[Dict[str, Any]] = None
    is_custom_route = resource in _CUSTOM_ROUTES if resource else False

    if resource and not is_custom_route:
        try:
            container = get_container(resource)
        except Exception:
            logger.warning("get_container(%s) failed, skipping metadata", resource)
            container = None
        if container is not None:
            resource_metadata = {
                "verbose_name": getattr(container, "verbose_name", resource),
                "writable": container_is_writable(resource),
            }

    # ── Classify view type ──
    if is_custom_route:
        view_type = "custom"
    else:
        view_type = _classify_path(segments)

    # ── Inject access token for iframe auth ──
    # The MCP principal carries the caller's OIDC access token.
    # Pass it as auth_token so the iframe can bootstrap a session
    # without requiring the user to log in again.
    _extra = dict(extra_params) if extra_params else {}
    try:
        principal = current_principal()
        if principal.access_token:
            _extra["auth_token"] = principal.access_token
    except RuntimeError:
        pass  # No principal bound (shouldn't happen in normal flow)

    # ── Build URL ──
    embed_url = _build_embed_url(
        path,
        hide_toolbar=hide_toolbar,
        hide_actions=hide_actions,
        hide_actions_column=hide_actions_column,
        redirect_after=redirect_after,
        redirect_after_create=redirect_after_create,
        redirect_after_update=redirect_after_update,
        extra_params=_extra,
    )

    title = _build_title(resource, view_type, container)

    logger.debug("lex_embed_view → %s (token injected: %s)", embed_url.split("auth_token")[0], "auth_token" in embed_url)

    # ── Build a clean URL for the model (strip auth_token) ──
    # The model/LLM should NOT see the access token.
    display_url = _build_embed_url(
        path,
        hide_toolbar=hide_toolbar,
        hide_actions=hide_actions,
        hide_actions_column=hide_actions_column,
        redirect_after=redirect_after,
        redirect_after_create=redirect_after_create,
        redirect_after_update=redirect_after_update,
        extra_params=extra_params,
    )

    # ── Build JSON payload for the widget + model ──
    structured: Dict[str, Any] = {
        "embed_url": embed_url,
        "title": title,
        "view_type": view_type,
        "resource": resource,
    }
    if resource_metadata:
        structured["resource_metadata"] = resource_metadata

    # ── Narration for the model (uses clean URL, no token) ──
    narration = f"Showing **{title}**"
    if resource_metadata:
        narration += f" (model: {resource_metadata['verbose_name']})"

    # The narration text block is shown to the model / user as context.
    # The structured JSON block is consumed by the widget to extract
    # embed_url (which includes the auth_token for iframe session
    # bootstrap).  The model should not echo the auth_token.
    narration += f"\n\n[View: {display_url}]"

    return [
        TextContent(type="text", text=narration),
        TextContent(
            type="text",
            text=json.dumps(structured, ensure_ascii=False),
        ),
    ]


# --------------------------------------------------------------------------- #
# Registration                                                                #
# --------------------------------------------------------------------------- #


def register(server: FastMCP) -> None:
    # ── UI resource (widget template) ──
    csp_origins = _csp_origins()
    frontend_base = _resolve_frontend_url()

    # Inject the frontend base URL into the widget HTML template.
    widget_html = _WIDGET_TEMPLATE.substitute(frontend_base_url=frontend_base)

    server.add_resource(
        FunctionResource(
            uri=_RESOURCE_URI,
            name="Lex embed view widget",
            description=(
                "HTML widget that renders a React frontend view inside "
                "an iframe. Receives the embed URL via the MCP Apps "
                "bridge (ui/notifications/tool-result)."
            ),
            mime_type=_RESOURCE_MIME,
            fn=lambda: widget_html,
            meta={
                "ui": {
                    "csp": {
                        "connectDomains": csp_origins,
                        "resourceDomains": csp_origins,
                        "frameDomains": csp_origins,
                    },
                },
            },
        )
    )

    # ── Tool ──
    server.add_tool(
        _embed_view,
        name="lex_embed_view",
        description=(
            "Embed a React frontend view as an interactive widget. "
            "Supports any route: model list (e.g. 'quarter'), detail "
            "('quarter/42'), create form ('quarter/create'), edit "
            "('quarter/42/edit'), or custom routes like "
            "'calculation_log_tree'. Use hide_toolbar / hide_actions "
            "to strip additional UI elements. The view renders inside "
            "the chat as an interactive iframe widget."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            openWorldHint=False,
            destructiveHint=False,
        ),
        meta={
            "ui": {
                "resourceUri": _RESOURCE_URI,
            },
            # Legacy flat key for older hosts
            "ui/resourceUri": _RESOURCE_URI,
        },
    )
