"""Open a local browser page with LEX AI frequently-asked questions."""

from __future__ import annotations

import html
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


def launch_ai_faq(
    reporter: Callable[[str], None] | None = None,
    timeout_seconds: int = 900,
) -> None:
    """Serve the FAQ page on localhost and open the user's browser."""

    closed = threading.Event()
    report = reporter or (lambda message: None)

    class _FAQHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in {"", "/"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            body = _build_faq_html()
            encoded = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _FAQHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    faq_url = f"http://127.0.0.1:{server.server_port}/"
    report(f"LEX AI FAQ is available at: {faq_url}")
    try:
        opened = webbrowser.open(faq_url, new=1, autoraise=True)
        if not opened:
            report(
                "The browser could not be opened automatically. "
                "Paste the URL above into any browser."
            )
    except Exception as exc:
        report(f"Automatic browser launch failed: {exc}")
        report("Paste the FAQ URL into any browser to continue.")

    report("Press Ctrl+C to stop the FAQ server.")
    try:
        closed.wait(timeout=timeout_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


# --------------- HTML builder ---------------

_LEX_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 329.02 78.41">'
    "<defs><style>"
    ".lx1{fill:#24b6bb}.lx2{fill:#283067}.lx3{fill:#282f63}"
    "</style></defs>"
    "<g>"
    '<path class="lx3" d="M269.21,58.25h-77.14c.57.57,1.22,1.06,1.97,1.47l32.26,17.6c2.68,1.46,5.99,1.46,8.66,0l32.28-17.6c.73-.41,1.4-.9,1.96-1.47h0Z"/>'
    '<path class="lx3" d="M269.21,20.16h-77.14c.57-.57,1.22-1.06,1.97-1.47L226.32,1.09c2.68-1.46,5.99-1.46,8.66,0l32.28,17.6c.73.41,1.4.9,1.96,1.47h0Z"/>'
    "</g>"
    "<g>"
    '<path class="lx1" d="M196.83,43.09c1.37,0,2.48-.54,3.35-1.6l1.78,1.81c-1.42,1.57-3.07,2.36-5,2.36s-3.5-.59-4.73-1.79c-1.25-1.2-1.86-2.7-1.86-4.52s.63-3.34,1.9-4.57c1.26-1.22,2.82-1.82,4.64-1.82,2.05,0,3.76.78,5.12,2.31l-1.72,1.94c-.87-1.08-1.96-1.62-3.28-1.62-1.04,0-1.93.34-2.68,1.01-.75.68-1.11,1.59-1.11,2.73s.34,2.06,1.06,2.75c.7.66,1.55,1.01,2.54,1.01h0Z"/>'
    '<path class="lx1" d="M208.56,45.51v-12.3h2.78v9.86h5.31v2.45h-8.09Z"/>'
    '<path class="lx1" d="M233.22,43.82c-1.26,1.22-2.8,1.82-4.64,1.82s-3.38-.61-4.64-1.82c-1.26-1.22-1.88-2.73-1.88-4.54s.63-3.32,1.88-4.54c1.26-1.22,2.8-1.82,4.64-1.82s3.38.61,4.64,1.82c1.26,1.22,1.88,2.73,1.88,4.54s-.63,3.32-1.88,4.54ZM232.27,39.3c0-1.1-.36-2.03-1.08-2.8-.72-.78-1.59-1.16-2.63-1.16s-1.91.39-2.63,1.16-1.08,1.7-1.08,2.8.36,2.03,1.08,2.8c.72.78,1.59,1.15,2.63,1.15s1.91-.39,2.63-1.15c.73-.78,1.08-1.7,1.08-2.8Z"/>'
    '<path class="lx1" d="M245.15,42.33c.46.57,1.09.86,1.86.86s1.4-.29,1.86-.86c.46-.57.68-1.35.68-2.33v-6.8h2.78v6.89c0,1.79-.5,3.16-1.5,4.1-.99.96-2.27,1.43-3.82,1.43s-2.83-.49-3.84-1.45c-1.01-.96-1.5-2.33-1.5-4.1v-6.89h2.78v6.8c.02,1,.24,1.79.7,2.35h0Z"/>'
    '<path class="lx1" d="M269.15,34.82c1.18,1.08,1.78,2.57,1.78,4.47s-.58,3.43-1.74,4.54c-1.16,1.11-2.92,1.67-5.29,1.67h-4.25v-12.3h4.41c2.22.02,3.93.54,5.11,1.62h0ZM267.12,42.13c.68-.64,1.02-1.55,1.02-2.77s-.34-2.14-1.02-2.78c-.68-.66-1.72-.98-3.14-.98h-1.55v7.48h1.76c1.28.02,2.25-.3,2.94-.95h0Z"/>'
    "</g>"
    "<g>"
    '<path class="lx2" d="M8.92,33.22v2.43H2.76v2.51h5.53v2.33H2.76v2.53h6.34v2.41H0v-12.21h8.92Z"/>'
    '<path class="lx2" d="M24.51,33.22h3.32l-3.85,5.88,4.17,6.32h-3.36l-2.63-4.02-2.61,4.02h-3.32l4.15-6.25-3.86-5.96h3.31l2.36,3.62,2.32-3.6Z"/>'
    '<path class="lx2" d="M41.53,43.02c1.36,0,2.46-.54,3.32-1.59l1.76,1.79c-1.41,1.56-3.05,2.35-4.97,2.35s-3.47-.59-4.7-1.78c-1.24-1.19-1.85-2.68-1.85-4.49s.63-3.32,1.88-4.54c1.25-1.21,2.8-1.81,4.61-1.81,2.03,0,3.73.77,5.08,2.3l-1.71,1.93c-.86-1.07-1.95-1.61-3.25-1.61-1.03,0-1.92.34-2.66,1.01-.73.67-1.1,1.57-1.1,2.71s.36,2.04,1.05,2.73c.68.65,1.53,1.01,2.53,1.01h0Z"/>'
    '<path class="lx2" d="M63.68,33.22v2.43h-6.15v2.51h5.53v2.33h-5.53v2.53h6.34v2.41h-9.1v-12.21h8.92Z"/>'
    '<path class="lx2" d="M72.33,45.42v-12.21h2.76v9.78h5.27v2.43h-8.03Z"/>'
    '<path class="lx2" d="M88.36,45.42v-12.21h2.76v9.78h5.27v2.43h-8.03Z"/>'
    '<path class="lx2" d="M113.31,33.22v2.43h-6.15v2.51h5.53v2.33h-5.53v2.53h6.34v2.41h-9.1v-12.21h8.92Z"/>'
    '<path class="lx2" d="M132.14,33.22h2.76v12.21h-2.76l-5.88-7.66v7.66h-2.76v-12.21h2.58l6.07,7.86v-7.86Z"/>'
    '<path class="lx2" d="M149.62,43.02c1.36,0,2.46-.54,3.32-1.59l1.76,1.79c-1.41,1.56-3.05,2.35-4.97,2.35s-3.47-.59-4.7-1.78c-1.24-1.19-1.85-2.68-1.85-4.49s.63-3.32,1.88-4.54c1.25-1.21,2.8-1.81,4.61-1.81,2.03,0,3.73.77,5.08,2.3l-1.71,1.93c-.86-1.07-1.95-1.61-3.25-1.61-1.03,0-1.92.34-2.66,1.01-.73.67-1.1,1.57-1.1,2.71s.34,2.04,1.05,2.73c.68.65,1.53,1.01,2.53,1.01h0Z"/>'
    '<path class="lx2" d="M171.77,33.22v2.43h-6.15v2.51h5.53v2.33h-5.53v2.53h6.34v2.41h-9.1v-12.21h8.92Z"/>'
    "</g>"
    "</svg>"
)

_RECOMMENDED_PROMPT = (
    "Help me create a lex app that will "
    "(list your preliminary description and requirements of the lex app here). "
    "The IO files of the app are provided."
)


def _build_faq_html() -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>LEX AI &ndash; Frequently Asked Questions</title>
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
        --error: #c0392b;
      }}
      * {{
        box-sizing: border-box;
      }}
      body {{
        margin: 0;
        font-family: "Segoe UI", "Avenir Next", system-ui, sans-serif;
        color: var(--text);
        background: var(--bg);
      }}

      /* ---- shell ---- */
      .shell {{
        max-width: 68rem;
        margin: 0 auto;
        padding: 2rem 1.25rem 3rem;
      }}

      /* ---- hero ---- */
      .hero {{
        background: var(--card);
        border: 1px solid var(--line);
        border-radius: 1rem;
        padding: 1.75rem 1.5rem;
        box-shadow: 0 2px 12px rgba(40, 48, 103, 0.06);
        display: flex;
        align-items: center;
        gap: 1.5rem;
      }}
      .hero-logo {{
        flex-shrink: 0;
      }}
      .hero-logo svg {{
        height: 52px;
        width: auto;
      }}
      .hero-text {{
        flex: 1;
        min-width: 0;
      }}
      .hero h1 {{
        margin: 0 0 0.4rem;
        font-size: clamp(1.5rem, 2.5vw, 2rem);
        color: var(--blue);
      }}
      .hero p {{
        margin: 0.3rem 0;
        color: var(--muted);
        line-height: 1.5;
        font-size: 0.95rem;
      }}
      .hero code {{
        background: var(--bg);
        padding: 0.15em 0.4em;
        border-radius: 4px;
        font-size: 0.88em;
        font-family: "SFMono-Regular", "Consolas", "Liberation Mono", monospace;
      }}

      /* ---- faq list ---- */
      .faq-list {{
        display: grid;
        gap: 1.25rem;
        margin-top: 1.25rem;
      }}

      /* ---- faq accordion ---- */
      .faq {{
        background: var(--card);
        border: 1px solid var(--line);
        border-radius: 1rem;
        box-shadow: 0 2px 12px rgba(40, 48, 103, 0.06);
        overflow: hidden;
      }}
      .faq summary {{
        display: flex;
        align-items: center;
        gap: 0.75rem;
        cursor: pointer;
        padding: 1.25rem 1.5rem;
        list-style: none;
        user-select: none;
      }}
      .faq summary::-webkit-details-marker {{ display: none; }}
      .faq summary::before {{
        content: "";
        flex-shrink: 0;
        width: 0.55rem;
        height: 0.55rem;
        border-right: 2px solid var(--teal);
        border-bottom: 2px solid var(--teal);
        transform: rotate(-45deg);
        transition: transform 200ms ease;
      }}
      .faq[open] > summary::before {{
        transform: rotate(45deg);
      }}
      .faq summary .q-label {{
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--teal);
        margin-right: 0.5rem;
        flex-shrink: 0;
      }}
      .faq summary .q-title {{
        font-size: 1.1rem;
        font-weight: 600;
        color: var(--blue);
      }}
      .faq .answer {{
        padding: 0 1.5rem 1.5rem 2.8rem;
      }}
      .faq .answer p {{
        line-height: 1.6;
        color: var(--muted);
        margin: 0.5rem 0;
        font-size: 0.95rem;
      }}
      .faq .answer strong {{
        color: var(--text);
      }}
      .faq .answer code {{
        background: var(--bg);
        padding: 0.1em 0.35em;
        border-radius: 4px;
        font-size: 0.88em;
        font-family: "SFMono-Regular", "Consolas", "Liberation Mono", monospace;
      }}
      .faq .answer ol, .faq .answer ul {{
        margin: 0.5rem 0;
        padding-left: 1.3rem;
        color: var(--muted);
        font-size: 0.95rem;
        line-height: 1.6;
      }}
      .faq .answer li {{
        padding: 0.15rem 0;
      }}
      .faq .answer li::marker {{
        color: var(--teal);
      }}
      .faq .answer h3 {{
        margin: 1rem 0 0.4rem;
        font-size: 1rem;
        color: var(--blue);
      }}

      /* ---- copyable bubble ---- */
      .copy-wrap {{
        position: relative;
        margin: 1rem 0 0.25rem;
      }}
      .copy-bubble {{
        display: block;
        width: 100%;
        background: var(--bg);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 0.85rem 3.5rem 0.85rem 1rem;
        font-family: "SFMono-Regular", "Consolas", "Liberation Mono", monospace;
        font-size: 0.9rem;
        line-height: 1.55;
        color: var(--text);
        word-break: break-word;
        white-space: pre-wrap;
      }}
      .copy-btn {{
        position: absolute;
        top: 0.55rem;
        right: 0.55rem;
        appearance: none;
        border: 1px solid var(--line);
        border-radius: 6px;
        background: var(--card);
        cursor: pointer;
        padding: 0.35rem 0.55rem;
        font-size: 0.82rem;
        font-weight: 600;
        color: var(--blue);
        transition: background 120ms ease, box-shadow 120ms ease;
      }}
      .copy-btn:hover {{
        background: var(--bg);
        box-shadow: 0 2px 8px rgba(40, 48, 103, 0.10);
      }}
      .copy-btn.copied {{
        color: var(--teal);
      }}

      /* ---- responsive ---- */
      @media (max-width: 860px) {{
        .hero {{
          flex-direction: column;
          align-items: flex-start;
          gap: 1rem;
        }}
      }}
    </style>
  </head>
  <body>
    <main class="shell">
      <!-- Hero -->
      <section class="hero">
        <div class="hero-logo">
          {_LEX_LOGO_SVG}
        </div>
        <div class="hero-text">
          <h1>LEX AI &ndash; Frequently Asked Questions</h1>
          <p>Quick answers for the most common questions about the <code>lex setup-with-ai</code> workflow and the LEX AI product.</p>
        </div>
      </section>

      <!-- FAQ accordions -->
      <section class="faq-list">

        <!-- Q1 -->
        <details class="faq" open>
          <summary>
            <span class="q-label">Q1</span>
            <span class="q-title">Clicking the &ldquo;Allow&rdquo; button is a burden?</span>
          </summary>
          <div class="answer">
            <p>Click the arrow next to the <strong>Allow</strong> button. From the dropdown menu select <strong>&ldquo;Allow all commands in this session&rdquo;</strong>.</p>
            <p>You may need to do this a couple of times for different forms of commands, but after that the system will completely run hands-free!</p>
          </div>
        </details>

        <!-- Q2 -->
        <details class="faq">
          <summary>
            <span class="q-label">Q2</span>
            <span class="q-title">What is the most ideal prompt and setup for optimal results?</span>
          </summary>
          <div class="answer">
            <p>Remember, AI is clever and can navigate its way through many scenarios using LEX AI, so you can build your prompt and setup as you like.</p>
            <p>Having said that, the best setup includes the <strong>IO files inside the working directory</strong> when you give the first prompt, and the best prompt is:</p>

            <div class="copy-wrap">
              <span class="copy-bubble" id="recommended-prompt">{html.escape(_RECOMMENDED_PROMPT)}</span>
              <button class="copy-btn" type="button" onclick="copyPrompt(this)">Copy</button>
            </div>
          </div>
        </details>

        <!-- Q3 -->
        <details class="faq">
          <summary>
            <span class="q-label">Q3</span>
            <span class="q-title">When there is a new version, how do I upgrade?</span>
          </summary>
          <div class="answer">
            <p>LEX AI is delivered as a standalone Python package called <code>lex-mcp-local</code>. When a new version is released, you only need to re-install the package inside the same virtual environment you used during <code>lex setup-with-ai</code>.</p>

            <h3>Step 1 &mdash; Update the package</h3>
            <p>Activate your project&rsquo;s virtual environment and run:</p>
            <div class="copy-wrap">
              <span class="copy-bubble" id="update-cmd">pip install --upgrade --no-cache-dir lex-mcp-local</span>
              <button class="copy-btn" type="button" onclick="copyPrompt(this, 'update-cmd')">Copy</button>
            </div>
            <p>This pulls the latest version from the private Cloudsmith registry (the same one <code>lex setup-with-ai</code> configured for you). If you used a custom index URL with an entitlement token, make sure it is still set in your environment or pass <code>--index-url</code> explicitly.</p>

            <h3>Step 2 &mdash; Restart the MCP server</h3>
            <p>After the package is updated you must restart the MCP server so the new code is loaded.</p>

            <h3>In VS Code</h3>
            <ol>
              <li>Open the <strong>Command Palette</strong> (<code>Ctrl+Shift+P</code> / <code>Cmd+Shift+P</code>).</li>
              <li>Type <strong>&ldquo;MCP: List Servers&rdquo;</strong> and select it.</li>
              <li>Find <code>lex-mcp-local</code> in the list and click <strong>Restart</strong>.</li>
            </ol>

            <h3>In PyCharm</h3>
            <ol>
              <li>Go to <strong>Settings &rarr; Tools &rarr; AI Assistant &rarr; MCP Servers</strong> (or search for &ldquo;MCP&rdquo; in the settings search bar).</li>
              <li>From there you can disable and re-enable the <code>lex-mcp-local</code> entry, or use the <strong>Restart</strong> action if available.</li>
              <li><strong>Important:</strong> Due to PyCharm constraints, you must <strong>close the PyCharm window and reopen</strong> the project for the new server version to be fully loaded. Simply restarting the MCP entry from the settings menu alone is not always sufficient &mdash; a full IDE window restart ensures a clean reload.</li>
            </ol>

            <p>After restarting, the updated tools and prompts will be available in your next Copilot Chat session.</p>
          </div>
        </details>

        <!-- Q4 -->
        <details class="faq">
          <summary>
            <span class="q-label">Q4</span>
            <span class="q-title">How to check if the MCP server is running?</span>
          </summary>
          <div class="answer">
            <h3>In PyCharm</h3>
            <ol>
              <li>Open the <strong>GitHub Copilot</strong> tool window (usually in the right-hand sidebar or via <strong>View &rarr; Tool Windows &rarr; GitHub Copilot</strong>).</li>
              <li>In the Copilot Chat panel, look for the small <strong>wrench-and-screwdriver</strong> (&#x1F527;) icon near the chat input area. Click it.</li>
              <li>A list of all registered MCP servers will appear. Each entry shows its current status &mdash; look for <code>lex-mcp-local</code> and confirm it is marked as <strong>running</strong> (a green indicator or &ldquo;Running&rdquo; label).</li>
              <li>If the server is <strong>not</strong> running, you can click on it to start or restart it from the same menu.</li>
            </ol>

            <h3>In VS Code</h3>
            <ol>
              <li>Open the <strong>Command Palette</strong> (<code>Ctrl+Shift+P</code> / <code>Cmd+Shift+P</code>).</li>
              <li>Type <strong>&ldquo;MCP: List Servers&rdquo;</strong> and select it.</li>
              <li>Find <code>lex-mcp-local</code> in the list &mdash; its status will show whether it is currently running or stopped.</li>
            </ol>
          </div>
        </details>

      </section>
    </main>

    <script>
      function copyPrompt(btn, elId) {{
        var el = document.getElementById(elId || "recommended-prompt");
        var text = el.textContent;
        if (navigator.clipboard) {{
          navigator.clipboard.writeText(text).then(function() {{
            btn.textContent = "Copied!";
            btn.classList.add("copied");
            setTimeout(function() {{ btn.textContent = "Copy"; btn.classList.remove("copied"); }}, 2000);
          }});
        }} else {{
          /* fallback */
          var ta = document.createElement("textarea");
          ta.value = text;
          ta.style.position = "fixed";
          ta.style.opacity = "0";
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
          btn.textContent = "Copied!";
          btn.classList.add("copied");
          setTimeout(function() {{ btn.textContent = "Copy"; btn.classList.remove("copied"); }}, 2000);
        }}
      }}
    </script>
  </body>
</html>
"""
