"""HTML templates for the ``lex setup-with-ai`` web wizard.

Kept in a separate module to keep ``setup_with_ai.py`` readable. Both
templates are rendered with ``str.format(...)``; literal ``{`` / ``}``
characters in CSS / JS appear as ``{{`` / ``}}``.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import quote


SETUP_WIZARD_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LEX AI Setup</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f0f4f8;
      --card: #ffffff;
      --text: #1a1a2e;
      --muted: #5a6278;
      --line: #d0d7e2;
      --blue: #283067;
      --blue-strong: #1b2050;
      --teal: #24b6bb;
      --teal-soft: rgba(36, 182, 187, 0.12);
      --error: #c0392b;
      --error-soft: rgba(192, 57, 43, 0.08);
      --success: #1f8a4d;
      --success-soft: rgba(31, 138, 77, 0.10);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "Avenir Next", system-ui, sans-serif;
      color: var(--text);
      background: var(--bg);
    }}
    .shell {{
      max-width: 60rem;
      margin: 0 auto;
      padding: 2rem 1.25rem 3rem;
    }}
    .hero {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 1rem;
      padding: 1.5rem;
      box-shadow: 0 2px 12px rgba(40, 48, 103, 0.06);
      margin-bottom: 1.25rem;
    }}
    .hero h1 {{ margin: 0 0 0.4rem; font-size: 1.6rem; color: var(--blue); }}
    .hero p {{ margin: 0.25rem 0; color: var(--muted); font-size: 0.93rem; }}
    .hero code {{ background: var(--bg); padding: 0.1em 0.35em; border-radius: 4px; font-family: monospace; font-size: 0.85em; }}

    .stepper {{
      display: flex;
      gap: 0.5rem;
      margin-bottom: 1.25rem;
      flex-wrap: wrap;
    }}
    .step-pill {{
      flex: 1;
      min-width: 8rem;
      padding: 0.6rem 0.85rem;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 8px;
      font-size: 0.85rem;
      color: var(--muted);
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .step-pill .num {{
      width: 22px; height: 22px; border-radius: 50%;
      background: var(--bg); color: var(--muted);
      display: inline-flex; align-items: center; justify-content: center;
      font-weight: 700; font-size: 0.78rem;
    }}
    .step-pill.active {{ border-color: var(--blue); color: var(--blue); }}
    .step-pill.active .num {{ background: var(--blue); color: #fff; }}
    .step-pill.done {{ border-color: var(--teal); color: var(--blue); }}
    .step-pill.done .num {{ background: var(--teal); color: #fff; }}

    .panel {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 1rem;
      padding: 1.5rem;
      box-shadow: 0 2px 12px rgba(40, 48, 103, 0.06);
    }}
    .panel h2 {{ margin: 0 0 0.5rem; color: var(--blue); font-size: 1.25rem; }}
    .panel p {{ color: var(--muted); line-height: 1.55; font-size: 0.93rem; margin: 0.4rem 0; }}
    .panel.hidden {{ display: none; }}

    .actions {{ display: flex; gap: 0.6rem; margin-top: 1.25rem; flex-wrap: wrap; }}
    .actions .spacer {{ flex: 1; }}

    button, a.button {{
      appearance: none;
      border: 0;
      border-radius: 8px;
      background: var(--blue);
      color: #fff;
      font: inherit;
      font-size: 0.94rem;
      font-weight: 600;
      cursor: pointer;
      padding: 0.7rem 1.2rem;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      transition: background 120ms ease, box-shadow 120ms ease;
    }}
    button:hover:not(:disabled), a.button:hover {{
      background: var(--blue-strong);
      box-shadow: 0 4px 14px rgba(40, 48, 103, 0.18);
    }}
    button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
    button.secondary, a.button.secondary {{
      background: transparent; color: var(--blue);
      border: 1px solid var(--line);
    }}
    button.secondary:hover:not(:disabled), a.button.secondary:hover {{
      background: var(--bg); box-shadow: none;
    }}
    button.ghost {{
      background: transparent; color: var(--muted); padding: 0.5rem 0.75rem;
      font-size: 0.88rem; font-weight: 500;
    }}
    button.ghost:hover:not(:disabled) {{ background: var(--bg); box-shadow: none; color: var(--blue); }}

    label {{ display: grid; gap: 0.35rem; font-weight: 600; font-size: 0.92rem; color: var(--blue); margin-top: 0.85rem; }}
    input[type="password"], input[type="text"] {{
      width: 100%;
      padding: 0.7rem 0.85rem;
      border-radius: 8px;
      border: 1px solid var(--line);
      font: inherit;
      font-size: 0.94rem;
      background: #fff;
    }}
    input:focus {{ outline: none; border-color: var(--blue); box-shadow: 0 0 0 3px rgba(40, 48, 103, 0.10); }}
    .hint {{ color: var(--muted); font-size: 0.85rem; margin: 0.25rem 0 0; }}

    .mode-toggle {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0.85rem; margin-top: 0.5rem; }}
    .mode-card {{
      position: relative;
      border: 2px solid var(--line);
      border-radius: 12px;
      padding: 1rem;
      cursor: pointer;
      transition: border-color 120ms ease, box-shadow 120ms ease;
    }}
    .mode-card:hover {{ border-color: var(--teal); }}
    .mode-card.selected {{ border-color: var(--teal); box-shadow: 0 0 0 3px var(--teal-soft); }}
    .mode-card .mode-title {{ font-weight: 700; color: var(--blue); margin-bottom: 0.2rem; }}
    .mode-card .mode-desc {{ color: var(--muted); font-size: 0.86rem; margin: 0; line-height: 1.4; }}

    .auth-tabs {{ display: flex; gap: 0.5rem; margin-bottom: 1rem; }}
    .tab-btn {{
      padding: 0.55rem 0.95rem;
      background: transparent;
      color: var(--muted);
      border: 1px solid var(--line);
      border-radius: 8px;
      font-size: 0.88rem;
      font-weight: 600;
      cursor: pointer;
    }}
    .tab-btn.active {{ background: var(--blue); color: #fff; border-color: var(--blue); }}
    .tab-btn:hover {{ color: var(--blue); }}
    .tab-btn.active:hover {{ color: #fff; }}

    .device-code {{
      display: inline-block;
      font-family: monospace;
      font-size: 1.6rem;
      letter-spacing: 0.2em;
      padding: 0.6rem 1.1rem;
      background: var(--bg);
      border: 1px dashed var(--line);
      border-radius: 8px;
      color: var(--blue);
      font-weight: 700;
    }}
    .pill {{
      display: inline-block;
      font-size: 0.78rem;
      padding: 0.15rem 0.5rem;
      border-radius: 999px;
      font-weight: 600;
    }}
    .pill.ok {{ background: var(--success-soft); color: var(--success); }}
    .pill.warn {{ background: var(--error-soft); color: var(--error); }}
    .pill.muted {{ background: var(--bg); color: var(--muted); }}

    .alert {{
      padding: 0.7rem 0.95rem;
      border-radius: 8px;
      font-size: 0.9rem;
      margin: 0.5rem 0;
    }}
    .alert.ok {{ background: var(--success-soft); color: var(--success); border: 1px solid rgba(31, 138, 77, 0.25); }}
    .alert.warn {{ background: var(--error-soft); color: var(--error); border: 1px solid rgba(192, 57, 43, 0.25); }}
    .alert.muted {{ background: var(--bg); color: var(--muted); border: 1px solid var(--line); }}

    .review-list {{ list-style: none; padding: 0; margin: 0.75rem 0; }}
    .review-list li {{
      display: flex; justify-content: space-between; gap: 1rem;
      padding: 0.55rem 0; border-bottom: 1px solid var(--line);
      font-size: 0.92rem;
    }}
    .review-list li:last-child {{ border-bottom: 0; }}
    .review-list .key {{ color: var(--muted); }}
    .review-list .val {{ color: var(--blue); font-weight: 600; }}

    .scope-grid {{ display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.4rem; }}
    .scope-grid .pill {{ font-family: monospace; }}

    .spinner {{
      display: inline-block;
      width: 14px; height: 14px;
      border: 2px solid rgba(255,255,255,0.4);
      border-top-color: #fff;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }}
    .spinner.dark {{ border-color: rgba(40, 48, 103, 0.3); border-top-color: var(--blue); }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}

    .small {{ font-size: 0.85rem; color: var(--muted); }}
    .small a {{ color: var(--blue); }}

    @media (max-width: 720px) {{
      .mode-toggle {{ grid-template-columns: 1fr; }}
      .stepper {{ font-size: 0.78rem; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <h1>Connect GitHub Copilot to your LEX MCP setup</h1>
      <p>Project root: <code>{project_root_html}</code></p>
      <p>Credentials are written to <code>{env_file_html}</code> and <code>{server_name_html}</code> is registered in GitHub Copilot's <code>mcp.json</code>.</p>
    </section>

    <nav class="stepper" id="stepper">
      <div class="step-pill" data-step="auth"><span class="num">1</span> GitHub auth</div>
      <div class="step-pill" data-step="mcp"><span class="num">2</span> MCP key</div>
      <div class="step-pill" data-step="mode"><span class="num">3</span> Mode &amp; review</div>
      <div class="step-pill" data-step="done"><span class="num">4</span> Finish</div>
    </nav>

    <section class="panel" id="panel-auth">
      <h2>Sign in to GitHub</h2>
      <p>LEX needs a GitHub token with <code>repo</code>, <code>workflow</code>, <code>admin:org</code>, and <code>user</code> scopes to drive the kickstart workflow.</p>

      <div class="auth-tabs" id="authTabs">
        <button type="button" class="tab-btn" data-tab="device" id="tabDevice">Sign in with GitHub</button>
        <button type="button" class="tab-btn" data-tab="pat">Paste a token instead</button>
      </div>

      <div id="auth-device" class="auth-tab-body">
        <p>Click the button below to start a one-time GitHub device authorisation. We'll open a tab to <code>github.com/login/device</code>.</p>
        <button type="button" id="btnStartDevice">Start GitHub device sign-in</button>
        <div id="deviceStatus" style="margin-top:1rem;"></div>
      </div>

      <div id="auth-pat" class="auth-tab-body" hidden>
        <p>If you can't use the device flow, paste a Classic Personal Access Token. <a href="{github_token_url_html}" target="_blank" rel="noreferrer">Open the GitHub token page with all scopes pre-selected.</a></p>
        <label>
          GitHub token
          <input type="password" id="patInput" autocomplete="off" spellcheck="false">
        </label>
        <div id="patStatus" style="margin-top:0.6rem;"></div>
        <div class="actions">
          <button type="button" id="btnValidatePat" class="secondary">Validate token</button>
          <button type="button" id="btnUsePat" disabled>Use this token</button>
        </div>
      </div>

      <div class="actions">
        <div class="spacer"></div>
        <button type="button" class="secondary" id="btnAuthNext" disabled>Continue</button>
      </div>
    </section>

    <section class="panel hidden" id="panel-mcp">
      <h2>Lex MCP Access Key</h2>
      <p>The same key authenticates the hosted MCP server (<code id="remoteUrlLabel"></code>) and unlocks the Cloudsmith install of <code>lex-mcp-local</code>.</p>
      <details class="small">
        <summary>How do I get this?</summary>
        <p>Your Lex administrator issues this key from the Lex MCP entitlement console. Contact your administrator if you don't have one yet.</p>
      </details>
      <label>
        Lex MCP Access Key
        <input type="password" id="mcpInput" autocomplete="off" spellcheck="false">
      </label>
      <div id="mcpStatus" style="margin-top:0.6rem;"></div>
      <div class="actions">
        <button type="button" class="ghost" data-back="auth">Back</button>
        <div class="spacer"></div>
        <button type="button" id="btnValidateMcp" class="secondary">Validate key</button>
        <button type="button" id="btnMcpNext" disabled>Continue</button>
      </div>
    </section>

    <section class="panel hidden" id="panel-mode">
      <h2>Workflow mode</h2>
      <p>Choose how LEX should approach this project.</p>
      <div class="mode-toggle" id="modeToggle">
        <div class="mode-card" data-mode="forward">
          <div class="mode-title">Create new project</div>
          <p class="mode-desc">AI-assisted planning, implementation, and documentation for a new LEX App.</p>
        </div>
        <div class="mode-card" data-mode="backward">
          <div class="mode-title">Document existing project</div>
          <p class="mode-desc">Generate docs and canonical context for a project that already exists.</p>
        </div>
      </div>

      <h2 style="margin-top:1.5rem;">Review</h2>
      <ul class="review-list" id="reviewList"></ul>

      <div class="actions">
        <button type="button" class="ghost" data-back="mcp">Back</button>
        <div class="spacer"></div>
        <button type="button" id="btnSubmit">Save and finish setup</button>
      </div>
    </section>

    <section class="panel hidden" id="panel-done">
      <h2>Finishing up&hellip;</h2>
      <p><span class="spinner dark"></span> Saving credentials and handing off to the CLI.</p>
    </section>
  </main>

  <script type="application/json" id="lex-bootstrap">{bootstrap_json}</script>
  <script>
  (function() {{
    var BOOT = JSON.parse(document.getElementById('lex-bootstrap').textContent);
    var STATE = BOOT.state;
    var creds = {{
      github_token: '',
      remote_mcp_api_key: '',
      mcp_mode: BOOT.last_used.mcp_mode || 'forward'
    }};
    var preferPat = !!BOOT.last_used.prefer_pat || !BOOT.device_flow_available;
    var pollHandle = null;
    var pollSession = null;
    var stepOrder = ['auth', 'mcp', 'mode', 'done'];
    var stepIndex = 0;

    function $(id) {{ return document.getElementById(id); }}
    function show(id) {{
      stepOrder.forEach(function(s) {{
        var p = $('panel-' + s);
        if (p) p.classList.toggle('hidden', s !== id);
      }});
      stepIndex = stepOrder.indexOf(id);
      paintStepper();
      if (id === 'mode') renderReview();
    }}
    function paintStepper() {{
      document.querySelectorAll('.step-pill').forEach(function(pill, i) {{
        pill.classList.toggle('active', i === stepIndex);
        pill.classList.toggle('done', i < stepIndex);
      }});
    }}
    function setAlert(el, kind, htmlStr) {{
      if (!el) return;
      if (!htmlStr) {{ el.innerHTML = ''; return; }}
      el.innerHTML = '<div class="alert ' + kind + '">' + htmlStr + '</div>';
    }}
    function escapeHtml(s) {{
      return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {{
        return ({{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }})[c];
      }});
    }}
    function postJson(url, body) {{
      return fetch(url, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(Object.assign({{ state: STATE }}, body || {{}}))
      }}).then(function(r) {{
        return r.json().then(function(j) {{ j.__status = r.status; return j; }});
      }});
    }}
    function getJson(url) {{
      return fetch(url).then(function(r) {{
        return r.json().then(function(j) {{ j.__status = r.status; return j; }});
      }});
    }}

    document.querySelectorAll('#authTabs .tab-btn').forEach(function(btn) {{
      btn.addEventListener('click', function() {{
        var tab = btn.getAttribute('data-tab');
        document.querySelectorAll('#authTabs .tab-btn').forEach(function(b) {{
          b.classList.toggle('active', b === btn);
        }});
        $('auth-device').hidden = (tab !== 'device');
        $('auth-pat').hidden = (tab !== 'pat');
        preferPat = (tab === 'pat');
      }});
    }});
    function activateAuthTab(tab) {{
      var btn = document.querySelector('#authTabs .tab-btn[data-tab="' + tab + '"]');
      if (btn) btn.click();
    }}
    if (!BOOT.device_flow_available) {{
      $('tabDevice').disabled = true;
      $('tabDevice').title = 'GitHub Device Flow not configured. Set LEX_GITHUB_OAUTH_CLIENT_ID to enable.';
      activateAuthTab('pat');
    }} else {{
      activateAuthTab(preferPat ? 'pat' : 'device');
    }}

    $('btnStartDevice').addEventListener('click', function() {{
      var btn = $('btnStartDevice');
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> Contacting GitHub&hellip;';
      setAlert($('deviceStatus'), 'muted', '');
      postJson('/api/github/device/start', {{}}).then(function(r) {{
        if (r.__status >= 400) {{
          btn.disabled = false; btn.textContent = 'Start GitHub device sign-in';
          setAlert($('deviceStatus'), 'warn', escapeHtml(r.error || 'Could not start device flow.'));
          return;
        }}
        pollSession = r.session;
        var url = r.verification_uri_complete;
        $('deviceStatus').innerHTML =
          '<p>Enter this code at <a href="' + escapeHtml(r.verification_uri) + '" target="_blank" rel="noreferrer">' +
            escapeHtml(r.verification_uri) + '</a>:</p>' +
          '<p><span class="device-code">' + escapeHtml(r.user_code) + '</span></p>' +
          '<p class="small">Or open the prefilled link: <a href="' + escapeHtml(url) + '" target="_blank" rel="noreferrer">' + escapeHtml(url) + '</a></p>' +
          '<p><span class="spinner dark"></span> Waiting for authorisation&hellip;</p>';
        try {{ window.open(url, '_blank', 'noreferrer'); }} catch (e) {{ /* popup blocked */ }}
        pollDevice(r.interval || 5);
        btn.textContent = 'Start GitHub device sign-in';
      }}).catch(function(err) {{
        btn.disabled = false; btn.textContent = 'Start GitHub device sign-in';
        setAlert($('deviceStatus'), 'warn', escapeHtml(String(err)));
      }});
    }});

    function pollDevice(intervalSec) {{
      if (pollHandle) clearTimeout(pollHandle);
      pollHandle = setTimeout(function() {{
        getJson('/api/github/device/poll?state=' + encodeURIComponent(STATE) + '&session=' + encodeURIComponent(pollSession))
          .then(function(r) {{
            if (r.status === 'authorized') {{
              creds.github_token = r.access_token;
              setAlert($('deviceStatus'), 'ok', 'Authorised. Validating token&hellip;');
              postJson('/api/validate/github-token', {{ github_token: r.access_token }}).then(function(v) {{
                if (v.ok) {{
                  showDeviceTokenInfo(v);
                  $('btnAuthNext').disabled = false;
                }} else {{
                  setAlert($('deviceStatus'), 'warn', 'Token validation failed: ' + escapeHtml(v.error || 'unknown error'));
                }}
              }});
              return;
            }}
            if (r.status === 'pending') {{ pollDevice(intervalSec); return; }}
            if (r.status === 'slow_down') {{ pollDevice((r.interval || intervalSec) + 5); return; }}
            setAlert($('deviceStatus'), 'warn', escapeHtml(r.error || 'Authorisation failed: ' + r.status));
          }})
          .catch(function(err) {{
            setAlert($('deviceStatus'), 'warn', escapeHtml(String(err)));
          }});
      }}, intervalSec * 1000);
    }}

    function showDeviceTokenInfo(v) {{
      var out = '<div class="alert ok">Signed in as <strong>' + escapeHtml(v.login) + '</strong>'
              + (v.name ? ' (' + escapeHtml(v.name) + ')' : '') + '</div>';
      if (v.missing_required_scopes && v.missing_required_scopes.length) {{
        out += '<div class="alert warn">Missing required scopes: '
             + v.missing_required_scopes.map(function(s) {{ return '<span class="pill warn">' + escapeHtml(s) + '</span>'; }}).join(' ')
             + '</div>';
      }}
      $('deviceStatus').innerHTML = out;
    }}

    $('btnValidatePat').addEventListener('click', function() {{
      var token = $('patInput').value.trim();
      if (!token) {{ setAlert($('patStatus'), 'warn', 'Paste a token first.'); return; }}
      var btn = $('btnValidatePat');
      btn.disabled = true; btn.innerHTML = '<span class="spinner dark"></span> Validating&hellip;';
      postJson('/api/validate/github-token', {{ github_token: token }}).then(function(v) {{
        btn.disabled = false; btn.textContent = 'Validate token';
        if (!v.ok) {{
          setAlert($('patStatus'), 'warn', escapeHtml(v.error || 'Validation failed.'));
          $('btnUsePat').disabled = true;
          return;
        }}
        var msg = 'Valid token for <strong>' + escapeHtml(v.login) + '</strong>'
                + (v.name ? ' (' + escapeHtml(v.name) + ')' : '') + '. ';
        if (v.missing_required_scopes && v.missing_required_scopes.length) {{
          msg += '<br>Missing required scopes: '
               + v.missing_required_scopes.map(function(s) {{ return '<span class="pill warn">' + escapeHtml(s) + '</span>'; }}).join(' ');
          setAlert($('patStatus'), 'warn', msg);
        }} else {{
          setAlert($('patStatus'), 'ok', msg);
        }}
        $('btnUsePat').disabled = false;
      }});
    }});

    $('btnUsePat').addEventListener('click', function() {{
      creds.github_token = $('patInput').value.trim();
      if (!creds.github_token) return;
      $('btnAuthNext').disabled = false;
      setAlert($('patStatus'), 'ok', 'Token saved for this session. Continue to the next step.');
    }});

    $('btnAuthNext').addEventListener('click', function() {{
      if (!creds.github_token) return;
      show('mcp');
    }});

    $('remoteUrlLabel').textContent = BOOT.remote_mcp_url;

    $('btnValidateMcp').addEventListener('click', function() {{
      var key = $('mcpInput').value.trim();
      if (!key) {{ setAlert($('mcpStatus'), 'warn', 'Paste a key first.'); return; }}
      var btn = $('btnValidateMcp');
      btn.disabled = true; btn.innerHTML = '<span class="spinner dark"></span> Validating&hellip;';
      postJson('/api/validate/mcp-key', {{ remote_mcp_api_key: key, remote_mcp_url: BOOT.remote_mcp_url }}).then(function(v) {{
        btn.disabled = false; btn.textContent = 'Validate key';
        if (v.ok) {{
          setAlert($('mcpStatus'), 'ok', escapeHtml(v.detail || 'Key accepted.'));
          $('btnMcpNext').disabled = false;
          creds.remote_mcp_api_key = key;
        }} else {{
          setAlert($('mcpStatus'), 'warn', escapeHtml(v.error || 'Validation failed.'));
        }}
      }});
    }});

    $('mcpInput').addEventListener('input', function() {{
      $('btnMcpNext').disabled = true;
      setAlert($('mcpStatus'), 'muted', '');
    }});

    $('btnMcpNext').addEventListener('click', function() {{
      if (!creds.remote_mcp_api_key) creds.remote_mcp_api_key = $('mcpInput').value.trim();
      if (!creds.remote_mcp_api_key) return;
      show('mode');
    }});

    function selectMode(mode) {{
      creds.mcp_mode = mode;
      document.querySelectorAll('.mode-card').forEach(function(c) {{
        c.classList.toggle('selected', c.getAttribute('data-mode') === mode);
      }});
      renderReview();
    }}
    document.querySelectorAll('.mode-card').forEach(function(card) {{
      card.addEventListener('click', function() {{ selectMode(card.getAttribute('data-mode')); }});
    }});
    selectMode(creds.mcp_mode);

    function renderReview() {{
      var rows = [
        ['Project root', BOOT.project_root],
        ['Workflow mode', creds.mcp_mode === 'backward' ? 'Document existing project' : 'Create new project'],
        ['GitHub token', creds.github_token ? '\u2022\u2022\u2022\u2022 (set)' : '(missing)'],
        ['Lex MCP Access Key', creds.remote_mcp_api_key ? '\u2022\u2022\u2022\u2022 (set)' : '(missing)'],
        ['Remote MCP URL', BOOT.remote_mcp_url]
      ];
      $('reviewList').innerHTML = rows.map(function(r) {{
        return '<li><span class="key">' + escapeHtml(r[0]) + '</span><span class="val">' + escapeHtml(r[1]) + '</span></li>';
      }}).join('');
    }}

    document.querySelectorAll('[data-back]').forEach(function(btn) {{
      btn.addEventListener('click', function() {{ show(btn.getAttribute('data-back')); }});
    }});

    $('btnSubmit').addEventListener('click', function() {{
      if (!creds.github_token || !creds.remote_mcp_api_key) {{
        alert('GitHub token and Lex MCP Access Key are both required.');
        return;
      }}
      var btn = $('btnSubmit');
      btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Saving&hellip;';
      postJson('/api/submit', {{
        github_token: creds.github_token,
        remote_mcp_api_key: creds.remote_mcp_api_key,
        mcp_mode: creds.mcp_mode,
        prefer_pat: preferPat
      }}).then(function(r) {{
        if (r.__status >= 400) {{
          btn.disabled = false; btn.textContent = 'Save and finish setup';
          alert(r.error || 'Submission failed.');
          return;
        }}
        show('done');
        if (r.redirect) {{ window.location = r.redirect; }}
      }}).catch(function(err) {{
        btn.disabled = false; btn.textContent = 'Save and finish setup';
        alert(String(err));
      }});
    }});

    show('auth');
    if (BOOT.initial_error) {{
      setAlert($('deviceStatus'), 'warn', escapeHtml(BOOT.initial_error));
    }}
  }})();
  </script>
</body>
</html>
"""


SUCCESS_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>LEX AI Setup &middot; Done</title>
    <style>
      body {{
        margin: 0; min-height: 100vh; display: grid; place-items: center;
        background: #f0f4f8; color: #1a1a2e;
        font-family: "Segoe UI", "Avenir Next", system-ui, sans-serif;
      }}
      .card {{
        width: min(40rem, calc(100vw - 2rem));
        background: #fff; border: 1px solid #d0d7e2; border-radius: 1rem;
        padding: 2rem; box-shadow: 0 2px 12px rgba(40,48,103,0.06);
      }}
      h1 {{ margin: 0 0 0.5rem; color: #283067; font-size: 1.5rem; }}
      h2 {{ font-size: 1rem; margin-top: 1.25rem; color: #283067; }}
      p {{ color: #5a6278; line-height: 1.55; }}
      code {{ background: #f0f4f8; padding: 0.1em 0.35em; border-radius: 4px; font-family: monospace; font-size: 0.88em; }}
      .check {{
        display: inline-flex; align-items: center; justify-content: center;
        width: 44px; height: 44px; border-radius: 50%;
        background: rgba(36,182,187,0.12); margin-bottom: 0.75rem;
      }}
      .actions {{ display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.5rem; }}
      .actions a {{
        padding: 0.6rem 1rem; border-radius: 8px; text-decoration: none;
        background: #283067; color: #fff; font-weight: 600; font-size: 0.92rem;
      }}
      .actions a.secondary {{ background: transparent; color: #283067; border: 1px solid #d0d7e2; }}
      pre {{
        background: #f0f4f8; padding: 0.75rem 0.95rem; border-radius: 8px;
        overflow-x: auto; font-size: 0.86rem; color: #283067;
      }}
      .small {{ font-size: 0.86rem; color: #5a6278; }}
    </style>
  </head>
  <body>
    <section class="card">
      <div class="check">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#24b6bb" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
      </div>
      <h1>Credentials saved</h1>
      <p>The CLI is now installing <code>lex-mcp-local</code> and writing <code>{env_file_html}</code> plus the GitHub Copilot <code>mcp.json</code> entry for <code>{server_name_html}</code>.</p>
      <p class="small">Project root: <code>{project_root_html}</code></p>

      <h2>Open the project in your editor</h2>
      <div class="actions">
        <a href="{vscode_url}">VS Code</a>
        <a href="{cursor_url}" class="secondary">Cursor</a>
        <a href="{jetbrains_url}" class="secondary">JetBrains</a>
      </div>

      <h2>Or from the terminal</h2>
      <pre>cd {project_root_html}
code .
# or: cursor .
# or: idea .</pre>
      <p class="small">You can close this tab once the terminal prints the final setup success message.</p>
    </section>
  </body>
</html>
"""


def render_setup_wizard(
    *,
    state: str,
    project_root: Path,
    env_file_path: Path,
    remote_mcp_url: str,
    github_token_url: str,
    server_name: str,
    last_used_mcp_mode: str,
    last_used_prefer_pat: bool,
    device_flow_available: bool,
    initial_error: str = "",
) -> str:
    bootstrap = {
        "state": state,
        "project_root": str(project_root),
        "env_file_path": str(env_file_path),
        "remote_mcp_url": remote_mcp_url,
        "github_token_url": github_token_url,
        "device_flow_available": bool(device_flow_available),
        "last_used": {
            "mcp_mode": last_used_mcp_mode or "forward",
            "prefer_pat": bool(last_used_prefer_pat),
        },
        "initial_error": initial_error or "",
    }
    return SETUP_WIZARD_HTML_TEMPLATE.format(
        bootstrap_json=html.escape(json.dumps(bootstrap), quote=False),
        project_root_html=html.escape(str(project_root)),
        env_file_html=html.escape(str(env_file_path)),
        server_name_html=html.escape(server_name),
        github_token_url_html=html.escape(github_token_url),
    )


def render_success_page(
    *,
    project_root: Path,
    env_file_path: Path,
    server_name: str,
) -> str:
    project_root_str = str(project_root)
    quoted_root = quote(project_root_str, safe="/:")
    return SUCCESS_HTML_TEMPLATE.format(
        env_file_html=html.escape(str(env_file_path)),
        server_name_html=html.escape(server_name),
        project_root_html=html.escape(project_root_str),
        vscode_url=html.escape(f"vscode://file/{quoted_root}"),
        cursor_url=html.escape(f"cursor://file/{quoted_root}"),
        jetbrains_url=html.escape(
            f"jetbrains://idea/navigate/reference?project={quote(Path(project_root_str).name)}"
        ),
    )
