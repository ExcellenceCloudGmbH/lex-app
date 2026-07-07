"""Open the hosted LEX AI frequently asked questions page."""

from __future__ import annotations

import html
import os
import webbrowser
from typing import Callable


DEFAULT_FAQ_PAGE_URL = "https://excellencecloudgmbh.github.io/lex-ai-faq-pages/"


def launch_ai_faq(
    reporter: Callable[[str], None] | None = None,
    timeout_seconds: int = 900,
) -> None:
    """Open the hosted FAQ page in the user's browser."""

    _ = timeout_seconds  # kept for backward compatibility with older call sites
    report = reporter or (lambda message: None)

    faq_url = (os.getenv("LEX_AI_FAQ_URL") or DEFAULT_FAQ_PAGE_URL).strip()
    report(f"Opening LEX AI FAQ: {faq_url}")
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
        background: linear-gradient(135deg, #f0f4f8 0%, #e8f0f7 50%, #f5f7fc 100%);
        background-attachment: fixed;
        min-height: 100vh;
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
        box-shadow: 0 4px 16px rgba(40, 48, 103, 0.08);
        display: flex;
        align-items: center;
        gap: 1.5rem;
        transition: box-shadow 300ms ease, border-color 300ms ease;
      }}

      .hero:hover {{
        box-shadow: 0 8px 24px rgba(36, 182, 187, 0.12);
        border-color: rgba(36, 182, 187, 0.3);
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
        gap: 1.5rem;
        margin-top: 2rem;
      }}

      /* ---- faq accordion ---- */
      .faq {{
        background: var(--card);
        border: 1px solid var(--line);
        border-radius: 1rem;
        box-shadow: 0 2px 12px rgba(40, 48, 103, 0.05);
        overflow: hidden;
        transition: all 250ms cubic-bezier(0.4, 0, 0.2, 1);
      }}

      .faq:hover {{
        box-shadow: 0 6px 20px rgba(36, 182, 187, 0.15);
        border-color: rgba(36, 182, 187, 0.2);
      }}

      .faq[open] {{
        box-shadow: 0 8px 28px rgba(40, 48, 103, 0.12);
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

      /* ---- prompt builder ---- */
      .pb-tabs {{
        display: flex;
        gap: 0.75rem;
        flex-wrap: wrap;
        margin-bottom: 1.5rem;
      }}
      .pb-tab {{
        appearance: none;
        border: 1.5px solid var(--line);
        border-radius: 10px;
        background: var(--card);
        cursor: pointer;
        padding: 0.85rem 1.25rem;
        text-align: left;
        transition: all 180ms cubic-bezier(0.4, 0, 0.2, 1);
        flex: 1 1 160px;
        min-width: 160px;
      }}
      .pb-tab:hover {{
        border-color: var(--teal);
        box-shadow: 0 4px 12px rgba(36, 182, 187, 0.16);
        transform: translateY(-2px);
      }}
      .pb-tab.active {{
        background: linear-gradient(135deg, #24b6bb 0%, #1a9a9e 100%);
        border-color: var(--teal);
        box-shadow: 0 6px 20px rgba(36, 182, 187, 0.3);
        color: #fff;
      }}
      .pb-tab .tab-title {{
        display: block;
        font-size: 0.92rem;
        font-weight: 700;
        color: var(--blue);
        margin-bottom: 0.15rem;
      }}
      .pb-tab .tab-desc {{
        display: block;
        font-size: 0.76rem;
        color: var(--muted);
        font-style: italic;
        line-height: 1.35;
      }}
      .pb-tab.active .tab-title,
      .pb-tab.active .tab-desc {{
        color: #fff;
      }}
      .pb-scenario-desc {{
        background: linear-gradient(135deg, rgba(36,182,187,0.06) 0%, rgba(40,48,103,0.04) 100%);
        border-left: 3px solid var(--teal);
        border-radius: 0 6px 6px 0;
        padding: 0.75rem 1rem;
        margin-bottom: 1.25rem;
        font-size: 0.9rem;
        color: var(--text);
        line-height: 1.55;
      }}
      .pb-scenario-desc strong {{
        color: var(--blue);
      }}
      .pb-scenario-desc em {{
        color: var(--teal);
        font-style: italic;
      }}
      .pb-fields {{
        display: grid;
        gap: 1rem;
      }}
      .pb-field {{
        display: none;
      }}
      .pb-field.visible {{
        display: block;
      }}
      .pb-field label {{
        display: block;
        font-size: 0.78rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--teal);
        margin-bottom: 0.3rem;
      }}
      .pb-field input,
      .pb-field textarea,
      .pb-field select {{
        display: block;
        width: 100%;
        border: 1px solid var(--line);
        border-radius: 6px;
        padding: 0.6rem 0.8rem;
        font-size: 0.9rem;
        font-family: inherit;
        color: var(--text);
        background: var(--bg);
        transition: border-color 150ms ease, box-shadow 150ms ease;
      }}
      .pb-field input:focus,
      .pb-field textarea:focus,
      .pb-field select:focus {{
        outline: none;
        border-color: var(--teal);
        box-shadow: 0 0 0 3px rgba(36, 182, 187, 0.12);
      }}
      .pb-field textarea {{
        resize: vertical;
        min-height: 2.6rem;
      }}
      .pb-field select {{
        cursor: pointer;
      }}
      .pb-field .hint {{
        font-size: 0.78rem;
        color: var(--muted);
        margin-top: 0.25rem;
        font-style: italic;
      }}
      .pb-preview-label {{
        font-size: 0.78rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--teal);
        margin: 1.5rem 0 0.4rem;
      }}

      /* ---- responsive ---- */
      @media (max-width: 860px) {{
        .hero {{
          flex-direction: column;
          align-items: flex-start;
          gap: 1rem;
        }}
        .flow-tabs {{ flex-direction: column; }}
        .flow-tab {{ min-width: unset; }}
      }}

      /* ---- behavior map flow tabs ---- */
      .flow-tabs {{
        display: flex;
        gap: 0.65rem;
        flex-wrap: wrap;
        margin: 1.25rem 0;
      }}
      .flow-tab {{
        appearance: none;
        border: 1.5px solid var(--line);
        border-radius: 10px;
        background: var(--card);
        cursor: pointer;
        padding: 0.75rem 1.15rem;
        text-align: left;
        flex: 1 1 140px;
        min-width: 140px;
        transition: all 180ms cubic-bezier(0.4, 0, 0.2, 1);
      }}
      .flow-tab:hover {{
        border-color: var(--teal);
        box-shadow: 0 4px 12px rgba(36, 182, 187, 0.16);
        transform: translateY(-2px);
      }}
      .flow-tab.active {{
        background: linear-gradient(135deg, #24b6bb 0%, #1a9a9e 100%);
        border-color: var(--teal);
        box-shadow: 0 6px 20px rgba(36, 182, 187, 0.3);
      }}
      .flow-tab .ft-icon {{
        font-size: 1.2rem;
        display: block;
        margin-bottom: 0.2rem;
      }}
      .flow-tab .ft-label {{
        display: block;
        font-size: 0.82rem;
        font-weight: 700;
        color: var(--blue);
      }}
      .flow-tab.active .ft-label {{
        color: #fff;
      }}
      .flow-panel {{
        display: none;
      }}
      .flow-panel.active {{
        display: block;
      }}
      .flow-desc {{
        background: linear-gradient(135deg, rgba(36,182,187,0.06) 0%, rgba(40,48,103,0.04) 100%);
        border-left: 3px solid var(--teal);
        border-radius: 0 6px 6px 0;
        padding: 0.65rem 1rem;
        margin-bottom: 1.25rem;
        font-size: 0.9rem;
        color: var(--text);
        line-height: 1.5;
      }}
      .flow-desc strong {{ color: var(--blue); }}
      .flow-desc em {{ color: var(--teal); font-style: italic; }}

      /* ---- timeline ---- */
      .timeline {{
        position: relative;
        padding: 0 0 0 2.2rem;
        margin: 0;
        list-style: none;
      }}
      .timeline::before {{
        content: "";
        position: absolute;
        left: 0.7rem;
        top: 0;
        bottom: 0;
        width: 2px;
        background: linear-gradient(180deg, var(--teal) 0%, var(--blue) 100%);
        border-radius: 1px;
      }}
      .tl-item {{
        position: relative;
        padding: 0.5rem 0 1rem;
      }}
      .tl-item:last-child {{
        padding-bottom: 0;
      }}
      .tl-dot {{
        position: absolute;
        left: -1.85rem;
        top: 0.65rem;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background: var(--teal);
        border: 2px solid var(--card);
        box-shadow: 0 0 0 2px var(--teal);
        z-index: 1;
      }}
      .tl-dot.blue {{ background: var(--blue); box-shadow: 0 0 0 2px var(--blue); }}
      .tl-dot.gold {{ background: #e6a817; box-shadow: 0 0 0 2px #e6a817; }}
      .tl-dot.green {{ background: #27ae60; box-shadow: 0 0 0 2px #27ae60; }}
      .tl-dot.purple {{ background: #8e44ad; box-shadow: 0 0 0 2px #8e44ad; }}
      .tl-phase {{
        display: inline-block;
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        padding: 0.15rem 0.5rem;
        border-radius: 4px;
        margin-bottom: 0.3rem;
      }}
      .tl-phase.setup {{ background: rgba(36,182,187,0.12); color: var(--teal); }}
      .tl-phase.plan {{ background: rgba(40,48,103,0.1); color: var(--blue); }}
      .tl-phase.build {{ background: rgba(230,168,23,0.12); color: #c48f00; }}
      .tl-phase.harden {{ background: rgba(39,174,96,0.12); color: #1e8449; }}
      .tl-phase.docs {{ background: rgba(142,68,173,0.12); color: #8e44ad; }}
      .tl-phase.finish {{ background: rgba(40,48,103,0.08); color: var(--blue); }}
      .tl-phase.user {{ background: rgba(230,168,23,0.15); color: #c48f00; }}
      .tl-title {{
        font-size: 0.92rem;
        font-weight: 600;
        color: var(--text);
        margin: 0.15rem 0 0.15rem;
      }}
      .tl-detail {{
        font-size: 0.82rem;
        color: var(--muted);
        line-height: 1.5;
        margin: 0;
      }}
      .tl-detail code {{
        background: var(--bg);
        padding: 0.1em 0.3em;
        border-radius: 3px;
        font-size: 0.85em;
        font-family: "SFMono-Regular", "Consolas", "Liberation Mono", monospace;
      }}

      /* ---- mode pill ---- */
      .mode-pill {{
        display: inline-block;
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        padding: 0.15rem 0.5rem;
        border-radius: 4px;
        margin-right: 0.3rem;
      }}
      .mode-pill.fwd {{ background: rgba(36,182,187,0.15); color: var(--teal); }}
      .mode-pill.bwd {{ background: rgba(40,48,103,0.1); color: var(--blue); }}
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

        <!-- Prompt Builder -->
        <details class="faq">
          <summary>
            <span class="q-label">BUILDER</span>
            <span class="q-title">Prompt Builder &mdash; craft the ideal prompt for your scenario</span>
          </summary>
          <div class="answer">
            <p><strong>Pick your scenario below</strong>, fill in the fields that appear, and <strong>copy the generated prompt</strong>. Each scenario only shows the fields <em>you</em> need to fill &mdash; the rest is handled automatically.</p>

            <div class="pb-tabs">
              <button class="pb-tab active" type="button" data-scenario="feature" onclick="pbSwitchScenario('feature')">
                <span class="tab-title">&#x2795; Add Feature</span>
                <span class="tab-desc">Extend an existing Lex project with a new capability</span>
              </button>
              <button class="pb-tab" type="button" data-scenario="revision" onclick="pbSwitchScenario('revision')">
                <span class="tab-title">&#x1F4DD; Revise Plan</span>
                <span class="tab-desc">You changed a planning step &mdash; propagate it downstream</span>
              </button>
              <button class="pb-tab" type="button" data-scenario="forward" onclick="pbSwitchScenario('forward')">
                <span class="tab-title">&#x1F680; New Project</span>
                <span class="tab-desc">Start a brand-new Lex app from scratch</span>
              </button>
              <button class="pb-tab" type="button" data-scenario="docs" onclick="pbSwitchScenario('docs')">
                <span class="tab-title">&#x1F4D6; Document</span>
                <span class="tab-desc">Auto-generate docs for an existing Lex project</span>
              </button>
            </div>

            <div class="pb-scenario-desc" id="pb-scenario-desc"></div>

            <div class="pb-fields">
              <!-- All possible fields — visibility toggled per scenario -->
              <div class="pb-field" id="pb-field-project">
                <label for="pb-project" id="pb-label-project">Project</label>
                <input type="text" id="pb-project" placeholder="">
                <div class="hint" id="pb-hint-project"></div>
              </div>
              <div class="pb-field" id="pb-field-feature">
                <label for="pb-feature">Feature Description</label>
                <textarea id="pb-feature" rows="2" placeholder="e.g. add a bulk-import wizard for CSV files"></textarea>
                <div class="hint"><em>What</em> do you want to add? Be as specific as you can.</div>
              </div>
              <div class="pb-field" id="pb-field-mode">
                <label for="pb-mode">MCP Mode</label>
                <select id="pb-mode">
                  <option value="FORWARD">FORWARD — Full Re-Planning</option>
                  <option value="BACKWARD">BACKWARD — Reverse & Docs</option>
                  <option value="EDIT">EDIT — Code Modifications</option>
                  <option value="REVIEW">REVIEW — Code Quality & Audit</option>
                  <option value="MVP_GENERATOR">MVP GENERATOR — Minimal Viable Product</option>
                </select>
                <div class="hint">FORWARD re-runs planning; BACKWARD reverses & documents; EDIT modifies code; REVIEW audits quality; MVP GENERATOR creates lightweight versions</div>
              </div>
              <div class="pb-field" id="pb-field-revision">
                <label for="pb-revision">What Did You Change?</label>
                <textarea id="pb-revision" rows="2" placeholder="e.g. I rewrote step 3 to use a queue-based architecture instead of synchronous calls"></textarea>
                <div class="hint">Describe <em>which step</em> you revised and <em>what changed</em></div>
              </div>
              <div class="pb-field" id="pb-field-overview">
                <label for="pb-overview">Project Idea &amp; Requirements</label>
                <textarea id="pb-overview" rows="3" placeholder="e.g. A fleet-management dashboard that tracks vehicle locations, maintenance schedules, and driver assignments"></textarea>
                <div class="hint">Describe the <em>purpose</em>, <em>intended users</em>, and <em>core capabilities</em></div>
              </div>
              <div class="pb-field" id="pb-field-scope">
                <label for="pb-scope" id="pb-label-scope">Focus Areas</label>
                <textarea id="pb-scope" rows="2" placeholder=""></textarea>
                <div class="hint" id="pb-hint-scope"></div>
              </div>
              <div class="pb-field" id="pb-field-audience">
                <label for="pb-audience">Documentation Audience</label>
                <textarea id="pb-audience" rows="1" placeholder="e.g. new developers joining the team"></textarea>
                <div class="hint">Who will <em>read</em> these docs? This shapes tone, depth, and examples.</div>
              </div>
              <div class="pb-field" id="pb-field-constraints">
                <label for="pb-constraints" id="pb-label-constraints">Constraints</label>
                <textarea id="pb-constraints" rows="2" placeholder=""></textarea>
                <div class="hint" id="pb-hint-constraints"></div>
              </div>
              <div class="pb-field" id="pb-field-done">
                <label for="pb-done" id="pb-label-done">Done When&hellip;</label>
                <textarea id="pb-done" rows="2" placeholder=""></textarea>
                <div class="hint" id="pb-hint-done"></div>
              </div>
            </div>

            <div class="pb-preview-label">Generated Prompt</div>
            <div class="copy-wrap">
              <span class="copy-bubble" id="pb-output" style="min-height:4rem"></span>
              <button class="copy-btn" type="button" onclick="copyPrompt(this, 'pb-output')">Copy</button>
            </div>
          </div>
        </details>

        <!-- Q1 -->
        <details class="faq">
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

        <!-- Q5 -->
        <details class="faq">
          <summary>
            <span class="q-label">Q5</span>
            <span class="q-title">Which AI models work best with LEX AI?</span>
          </summary>
          <div class="answer">
            <p>We evaluated how well leading AI models perform when guided by our MCP workflow &mdash; a structured process that instructs AI to plan, build, and validate complete applications autonomously. Here are the current rankings:</p>

            <ol>
              <li><strong>Claude 4.7 (Sonnet)</strong> &mdash; Best overall. Production-ready code with perfect framework compliance and zero corrections needed.</li>
              <li><strong>GPT 5.5</strong> &mdash; Close second. Most thorough testing infrastructure with strong planning-to-code alignment.</li>
              <li><strong>Claude 4.6 (Opus)</strong> &mdash; Most detailed planner. Produced 50 requirements and implemented all with zero compliance corrections.</li>
              <li><strong>GPT 5.3 Codex</strong> &mdash; Most elegant design. Simpler, more auditable architecture while maintaining full compliance.</li>
              <li><strong>Claude Sonnet 4.6</strong> &mdash; Strong implementation with realistic agent-based simulation. Complete code and dashboards.</li>
              <li><strong>GPT-4 Mini</strong> &mdash; Minimal but functional. Proves the MCP workflow can guide even smaller models.</li>
              <li><strong>Gemini 3.1</strong> &mdash; Completed all steps including bonus documentation enrichment. Fully compliant.</li>
              <li><strong>Gemini 3.1 Flash</strong> &mdash; Working code with good file organization. Documentation left partially incomplete.</li>
              <li><strong>Grok (Fast)</strong> &mdash; Did not produce any project output. Only framework scaffolding was generated.</li>
            </ol>

            <h3>Key Takeaways</h3>
            <ul>
              <li><strong>The MCP workflow works.</strong> Every model that completed the process produced a structurally valid, framework-compliant application.</li>
              <li><strong>Top models deliver production-ready output.</strong> Claude 4.7 and GPT 5.5 produced immediately deployable code with tests, documentation, and full requirement traceability.</li>
              <li><strong>Planning depth pays off.</strong> Models that invested more in the planning phase produced higher-quality implementations.</li>
              <li><strong>Testing is the differentiator.</strong> The gap between top performers and the rest comes down to automated test coverage.</li>
            </ul>

            <p>We recommend <strong>Claude 4.7 (Sonnet)</strong> or <strong>GPT 5.5</strong> as the primary models for production workflows.</p>

            <p>This evaluation is updated regularly. Visit the live rankings page for the latest results:<br>
            <a href="https://excellencecloudgmbh.github.io/lex-mcp-model-evals" target="_blank" rel="noopener noreferrer">excellencecloudgmbh.github.io/lex-mcp-model-evals</a></p>
          </div>
        </details>

        <!-- Q6 -->
        <details class="faq">
          <summary>
            <span class="q-label">Q6</span>
            <span class="q-title">How does the Lex AI workflow actually work under the hood?</span>
          </summary>
          <div class="answer">
            <p>This is the <strong>Lex AI Behavior Map</strong>. Pick a workflow type to see its <em>step-by-step timeline</em>.</p>

            <p style="margin:0.5rem 0 0.2rem">
              <span class="mode-pill fwd">FORWARD</span> planning, implementation &amp; hardening &nbsp;
              <span class="mode-pill bwd">BACKWARD</span> scanning, docs &amp; reverse questionnaire
            </p>

            <div class="flow-tabs">
              <button class="flow-tab active" type="button" data-flow="new" onclick="flowSwitch('new')">
                <span class="ft-icon">&#x1F680;</span>
                <span class="ft-label">New Project</span>
              </button>
              <button class="flow-tab" type="button" data-flow="feat" onclick="flowSwitch('feat')">
                <span class="ft-icon">&#x2795;</span>
                <span class="ft-label">Add Feature</span>
              </button>
              <button class="flow-tab" type="button" data-flow="rev" onclick="flowSwitch('rev')">
                <span class="ft-icon">&#x1F4DD;</span>
                <span class="ft-label">Plan Revision</span>
              </button>
              <button class="flow-tab" type="button" data-flow="docs" onclick="flowSwitch('docs')">
                <span class="ft-icon">&#x1F4D6;</span>
                <span class="ft-label">Documentation</span>
              </button>
            </div>

            <!-- Flow 1: New Project -->
            <div class="flow-panel active" id="flow-new">
              <div class="flow-desc"><span class="mode-pill fwd">FORWARD</span> <strong>Create a brand-new Lex app</strong> &mdash; from repo creation to merged PR, fully automated.</div>
              <ol class="timeline">
                <li class="tl-item">
                  <span class="tl-dot"></span>
                  <span class="tl-phase setup">Setup</span>
                  <div class="tl-title">Kickstart Workflow</div>
                  <p class="tl-detail"><code>kickstart_workflow(...)</code> &mdash; validates GitHub credentials, creates the repo, initializes git, writes <code>AGENTS.md</code>, pushes initial commit, creates workflow branch &amp; tracking issue.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot blue"></span>
                  <span class="tl-phase plan">Plan</span>
                  <div class="tl-title">Steps 0 &ndash; 8: Build Planning Artifacts</div>
                  <p class="tl-detail">Requirements, IO analysis, data models, business rules, UI mockups, calculation specs, test strategy, and the master plan.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot gold"></span>
                  <span class="tl-phase build">Build</span>
                  <div class="tl-title">Steps 9 &ndash; 11: Implement the Code</div>
                  <p class="tl-detail">Models, views, calculations, templates, and all application code. Each step commits &amp; pushes via <code>notify_step_complete(...)</code>.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot green"></span>
                  <span class="tl-phase harden">Harden</span>
                  <div class="tl-title">Steps 12 &ndash; 14: Test &amp; Harden</div>
                  <p class="tl-detail">Automated tests, edge-case coverage, compliance checks, and code cleanup.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot purple"></span>
                  <span class="tl-phase docs">Docs</span>
                  <div class="tl-title">Steps 15 &ndash; 18: Generate &amp; Enrich Wiki</div>
                  <p class="tl-detail">Build <code>technical-map/</code>, per-module <code>CONTEXT.md</code> files, and enriched documentation.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot blue"></span>
                  <span class="tl-phase finish">Sync</span>
                  <div class="tl-title">Step 19: Cross-Check</div>
                  <p class="tl-detail">Validates that forward docs, backward docs, and code are all consistent.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot green"></span>
                  <span class="tl-phase finish">Finish</span>
                  <div class="tl-title">Audit &amp; Merge</div>
                  <p class="tl-detail">First <code>finalize_workflow()</code> &rarr; write <code>audit-report.md</code>. Second <code>finalize_workflow(audit_complete=True)</code> &rarr; PR &amp; squash-merge.</p>
                </li>
              </ol>
            </div>

            <!-- Flow 2: Add Feature -->
            <div class="flow-panel" id="flow-feat">
              <div class="flow-desc"><span class="mode-pill bwd">BACKWARD</span> <em>optional scan first</em>, then <span class="mode-pill fwd">FORWARD</span> <strong>implement the feature</strong> in an existing codebase.</div>
              <ol class="timeline">
                <li class="tl-item">
                  <span class="tl-dot blue"></span>
                  <span class="tl-phase plan">Assess</span>
                  <div class="tl-title">Optional: Map the Current System</div>
                  <p class="tl-detail">If the project is poorly understood, start in <strong>backward mode</strong>: <code>reverse_kickstart(...)</code> &rarr; <code>scan_project()</code> &rarr; <code>generate_wiki()</code> &rarr; reverse steps 3&ndash;6 &rarr; questionnaire &rarr; <code>finalize_reverse()</code>.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot"></span>
                  <span class="tl-phase setup">Setup</span>
                  <div class="tl-title">Switch to Forward &amp; Kickstart Run</div>
                  <p class="tl-detail"><code>kickstart_run(...)</code> on the existing repo. Creates a fresh workflow branch &amp; tracking issue.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot blue"></span>
                  <span class="tl-phase plan">Plan</span>
                  <div class="tl-title">Steps 0 &ndash; 10: Re-Plan the Delta</div>
                  <p class="tl-detail">Analyzes the existing code and plans only what needs to change for the new feature.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot gold"></span>
                  <span class="tl-phase build">Build</span>
                  <div class="tl-title">Step 11: Refactor Agent</div>
                  <p class="tl-detail">Routes to the <em>refactor agent</em> (not the greenfield agent) &mdash; incremental changes, not a rewrite.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot green"></span>
                  <span class="tl-phase harden">Harden &amp; Docs</span>
                  <div class="tl-title">Steps 12 &ndash; 19: Harden, Enrich &amp; Sync</div>
                  <p class="tl-detail">Tests, doc enrichment, artifact sync &mdash; same pipeline as a new project.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot green"></span>
                  <span class="tl-phase finish">Finish</span>
                  <div class="tl-title">Audit &amp; Merge</div>
                  <p class="tl-detail">Two-stage <code>finalize_workflow(...)</code> &rarr; audit report &rarr; PR &amp; squash-merge.</p>
                </li>
              </ol>
            </div>

            <!-- Flow 3: Plan Revision -->
            <div class="flow-panel" id="flow-rev">
              <div class="flow-desc"><span class="mode-pill fwd">FORWARD</span> <strong>Propagate a plan change</strong> &mdash; the server detects which steps are affected and re-runs only what&rsquo;s needed.</div>
              <ol class="timeline">
                <li class="tl-item">
                  <span class="tl-dot"></span>
                  <span class="tl-phase setup">Setup</span>
                  <div class="tl-title">Resume or Start a Run</div>
                  <p class="tl-detail">If a workflow is in progress: <code>resume_workflow(...)</code>. Otherwise: <code>kickstart_run(...)</code> on the existing repo.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot gold"></span>
                  <span class="tl-phase user">User Edit</span>
                  <div class="tl-title">You Modify a Planning Artifact</div>
                  <p class="tl-detail">Change a requirements doc, IO spec, or any planning file &mdash; then call <code>get_plan_step(step=N)</code>.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot blue"></span>
                  <span class="tl-phase plan">Detect</span>
                  <div class="tl-title">Server Detects Changes</div>
                  <p class="tl-detail">Checks the working tree, maps changed files to originating steps via <code>.lex-workflow/manifest.json</code>, and returns <code>user_changes_detected</code>.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot gold"></span>
                  <span class="tl-phase build">Re-Run</span>
                  <div class="tl-title">Re-Execute Affected Steps</div>
                  <p class="tl-detail">Re-runs from the <strong>earliest affected step</strong> through the target. If new <code>.csv</code>/<code>.xlsx</code> files were added, restarts from <strong>step 2</strong>. Step 11 uses the refactor agent.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot green"></span>
                  <span class="tl-phase harden">Continue</span>
                  <div class="tl-title">Complete Remaining Steps</div>
                  <p class="tl-detail">After catching up, continues through the remaining forward steps (hardening, docs, sync).</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot green"></span>
                  <span class="tl-phase finish">Finish</span>
                  <div class="tl-title">Audit &amp; Merge</div>
                  <p class="tl-detail">Standard audit, PR, and auto-merge flow.</p>
                </li>
              </ol>
            </div>

            <!-- Flow 4: Documentation -->
            <div class="flow-panel" id="flow-docs">
              <div class="flow-desc"><span class="mode-pill bwd">BACKWARD</span> <strong>Generate comprehensive documentation</strong> for an existing project &mdash; no code changes, purely docs.</div>
              <ol class="timeline">
                <li class="tl-item">
                  <span class="tl-dot"></span>
                  <span class="tl-phase setup">Setup</span>
                  <div class="tl-title">Reverse Kickstart</div>
                  <p class="tl-detail"><code>reverse_kickstart(project_path=... or github_url=...)</code>. Resumes automatically if <code>.lex-reverse/manifest.json</code> exists.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot blue"></span>
                  <span class="tl-phase plan">Scan</span>
                  <div class="tl-title">Project Scan (R-00 &ndash; R-02)</div>
                  <p class="tl-detail"><code>scan_project()</code> writes scan artifacts under <code>plans/business_docs/</code>.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot purple"></span>
                  <span class="tl-phase docs">Wiki</span>
                  <div class="tl-title">Generate Technical Wiki</div>
                  <p class="tl-detail"><code>generate_wiki()</code> creates <code>technical-map/</code> and per-module <code>CONTEXT.md</code> files.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot purple"></span>
                  <span class="tl-phase docs">Enrich</span>
                  <div class="tl-title">Steps 3 &ndash; 6: Enrich Wiki</div>
                  <p class="tl-detail">AI reviews and enriches the technical documentation.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot gold"></span>
                  <span class="tl-phase user">User Input</span>
                  <div class="tl-title">Questionnaire</div>
                  <p class="tl-detail"><code>generate_questionnaire()</code> creates <code>discovery-questionnaire.md</code>. <strong>Workflow pauses</strong> &mdash; you validate <code>[LLM-FILLED]</code> answers and fill <code>[USER-REQUIRED]</code> placeholders. Then <code>submit_questionnaire(...)</code>.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot purple"></span>
                  <span class="tl-phase docs">Generate</span>
                  <div class="tl-title">Steps 8 &ndash; 16: Business Docs</div>
                  <p class="tl-detail">Generates the full business-facing documentation set.</p>
                </li>
                <li class="tl-item">
                  <span class="tl-dot green"></span>
                  <span class="tl-phase finish">Finish</span>
                  <div class="tl-title">Gap Report &amp; Complete</div>
                  <p class="tl-detail"><code>finalize_reverse()</code> &rarr; step 17 writes the final gap report &rarr; <code>notify_reverse_complete(step=17)</code>. <em>No PR is created &mdash; this is a documentation workflow.</em></p>
                </li>
              </ol>
            </div>
          </div>
        </details>

      </section>
    </main>

    <script>
      /* ---- Flow tab switcher ---- */
      function flowSwitch(id) {{
        document.querySelectorAll(".flow-tab").forEach(function(t) {{
          t.classList.toggle("active", t.getAttribute("data-flow") === id);
        }});
        document.querySelectorAll(".flow-panel").forEach(function(p) {{
          p.classList.toggle("active", p.id === "flow-" + id);
        }});
      }}

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

      /* ---- Prompt Builder Logic ---- */
      var pbScenarios = {{
        feature: {{
          mode: "FORWARD",
          visibleFields: ["project", "feature", "scope", "constraints", "done"],
          projectRequired: false,
          desc: "<strong>Add a feature</strong> to an existing Lex project. The MCP server runs in <em>FORWARD</em> mode &mdash; it plans the new capability, implements it, and validates it within your existing codebase.",
          labels: {{
            project: "Project (optional)",
            scope: "Files & Modules to Touch",
            constraints: "Constraints",
            done: "Done When\u2026"
          }},
          hints: {{
            project: "Optional \u2014 the AI auto-detects the project from the .git folder in your workspace",
            scope: "Which files, modules, or areas should the AI focus on?",
            constraints: "e.g. must be backward-compatible, no new pip dependencies",
            done: "How will you know this feature is complete?"
          }},
          placeholders: {{
            project: "auto-detected from .git \u2014 or type a name / URL if you prefer",
            scope: "models.py, views.py, calculations/",
            constraints: "must be backward-compatible, no new dependencies",
            done: "the feature works end-to-end with tests passing"
          }}
        }},
        revision: {{
          mode: "FORWARD",
          visibleFields: ["project", "mode", "revision", "scope", "constraints", "done"],
          projectRequired: false,
          desc: "<strong>Propagate a plan change.</strong> You revised a planning step &mdash; this tells the AI to <em>re-evaluate everything downstream</em> and update code, docs, and artifacts to match your new plan.",
          labels: {{
            project: "Project (optional)",
            scope: "Stable Areas (don\u2019t touch unless required)",
            constraints: "Risks or Priorities",
            done: "Done When\u2026"
          }},
          hints: {{
            project: "Optional \u2014 the AI auto-detects the project from the .git folder in your workspace",
            scope: "Areas that should NOT change unless the revision explicitly requires it",
            constraints: "Any risks, edge cases, or priorities the AI should watch for",
            done: "How will you know the reconfiguration is complete?"
          }},
          placeholders: {{
            project: "auto-detected from .git \u2014 or type a name / URL if you prefer",
            scope: "authentication module, database schema",
            constraints: "preserve existing API contracts",
            done: "downstream plan, code, docs, and artifacts all reflect the revision"
          }}
        }},
        forward: {{
          mode: "FORWARD",
          visibleFields: ["project", "overview", "scope", "constraints", "done"],
          projectRequired: true,
          desc: "<strong>Create a brand-new Lex project</strong> from scratch. The MCP server runs in <em>FORWARD</em> mode &mdash; it walks through the full planning-to-implementation workflow, generating everything from models to UI.",
          labels: {{
            project: "Project Name",
            scope: "Core Features",
            constraints: "Environment & Constraints",
            done: "Done When\u2026"
          }},
          hints: {{
            project: "Give your new project a name (kebab-case works best)",
            scope: "The main features or modules the app needs",
            constraints: "Python version, database, deployment target, etc.",
            done: "What does \u201csuccess\u201d look like?"
          }},
          placeholders: {{
            project: "fleet-tracker",
            scope: "vehicle tracking, maintenance schedules, driver management",
            constraints: "Python 3.11+, PostgreSQL, deploy to Docker",
            done: "a fully scaffolded, running Lex application with all core features"
          }}
        }},
        docs: {{
          mode: "BACKWARD",
          visibleFields: ["project", "audience", "scope", "constraints", "done"],
          projectRequired: false,
          desc: "<strong>Auto-generate documentation</strong> for an existing Lex project. The MCP server runs in <em>BACKWARD</em> mode &mdash; it reverse-engineers your codebase and produces structured docs, not a quick manual scan.",
          labels: {{
            project: "Project (optional)",
            scope: "Areas to Document",
            constraints: "Known Gaps or Risks",
            done: "Done When\u2026"
          }},
          hints: {{
            project: "Optional \u2014 the AI auto-detects the project from the .git folder in your workspace",
            scope: "Which modules, endpoints, or workflows need documentation?",
            constraints: "Any known gaps, outdated docs, or areas of concern",
            done: "What must the documentation clearly explain?"
          }},
          placeholders: {{
            project: "auto-detected from .git \u2014 or type a name / URL if you prefer",
            scope: "API endpoints, data models, calculation logic",
            constraints: "existing README is outdated, no docs for the reporting module",
            done: "every public module, endpoint, and workflow has clear, accurate documentation"
          }}
        }}
      }};

      var pbNonGoals = {{
        feature: "Do not bypass the MCP by free-exploring Lex docs or by starting a new project.",
        revision: "Do not ignore my revised planning document, do use it to guide your actions, and do not improvise a separate plan outside the Lex MCP flow, do follow the path laid out by the Lex MCP completely.",
        forward: "Do not create a generic Django, FastAPI, React, or other non-Lex project unless the Lex MCP workflow explicitly calls for it.",
        docs: "Do not write ad hoc docs from a quick manual scan; use the MCP-guided reverse flow."
      }};

      function pbBuildPrompt(sc, v) {{
        var cfg = pbScenarios[sc];
        var m = cfg.mode;
        if (sc === "revision") {{
          var modeEl = document.getElementById("pb-mode");
          m = modeEl ? modeEl.value : cfg.mode;
        }}
        var ng = pbNonGoals[sc];
        var parts = [];
        var proj = v.project;

        if (sc === "feature") {{
          if (proj) {{
            parts.push("I need to add a feature to the existing Lex AI project " + proj + ".");
          }} else {{
            parts.push("I need to add a feature to the existing Lex AI project in the current workspace.");
          }}
          if (v.feature) parts.push("The feature: " + v.feature + ".");
          parts.push("Mandatory: use the Lex MCP server as the controlling workflow and switch it to " + m + " mode before doing anything else. " + ng + " Work from the existing project structure and follow the MCP-guided process for modifying an existing Lex app.");
          if (v.scope) parts.push("Focus on these areas first: " + v.scope + ".");
          if (v.constraints) parts.push("Respect these constraints: " + v.constraints + ".");
          if (v.done) parts.push("The feature is complete when: " + v.done + ".");
        }} else if (sc === "revision") {{
          if (proj) {{
            parts.push("I updated a planning document in my Lex project " + proj + ".");
          }} else {{
            parts.push("I updated a planning document in the Lex project in the current workspace.");
          }}
          parts.push("Mandatory: use the Lex MCP server as the controlling workflow and switch it to " + m + " mode before doing anything else.");
          if (v.revision) parts.push("Here is what changed: " + v.revision + ".");
          parts.push("Treat my revised planning document as the source of truth. " + ng + " Re-evaluate the downstream plan, identify which later steps are now affected, and update the code, docs, and supporting artifacts accordingly.");
          if (v.scope) parts.push("Preserve these stable areas unless the revision requires changes: " + v.scope + ".");
          if (v.constraints) parts.push("Pay special attention to: " + v.constraints + ".");
          if (v.done) parts.push("The reconfiguration is complete when: " + v.done + ".");
        }} else if (sc === "forward") {{
          parts.push("I want to start a new Lex AI project called " + (proj || "my-new-project") + ".");
          parts.push("Mandatory: use the Lex MCP server in " + m + " mode from the very beginning. " + ng);
          if (v.overview) parts.push("Project idea and requirements: " + v.overview + ".");
          if (v.scope) parts.push("Core features: " + v.scope + ".");
          if (v.constraints) parts.push("Constraints: " + v.constraints + ".");
          if (v.done) parts.push("The workflow should be considered successful when: " + v.done + ".");
        }} else if (sc === "docs") {{
          if (proj) {{
            parts.push("I want to document the existing Lex project " + proj + ".");
          }} else {{
            parts.push("I want to document the existing Lex project in the current workspace.");
          }}
          parts.push("Mandatory: use the Lex MCP server in " + m + " mode and follow the reverse-documentation workflow. " + ng);
          if (v.audience) parts.push("The main documentation audience is: " + v.audience + ".");
          if (v.scope) parts.push("I especially need coverage for: " + v.scope + ".");
          if (v.constraints) parts.push("Note these known gaps or risks: " + v.constraints + ".");
          if (v.done) parts.push("The documentation is complete when: " + v.done + ".");
        }}

        return parts.join(" ");
      }}

      function pbUserVal(id) {{
        var el = document.getElementById(id);
        if (!el) return "";
        return el.value.trim();
      }}

      var pbCurrentScenario = "feature";

      function pbSwitchScenario(sc) {{
        pbCurrentScenario = sc;
        var cfg = pbScenarios[sc];

        /* tabs */
        document.querySelectorAll(".pb-tab").forEach(function(tab) {{
          tab.classList.toggle("active", tab.getAttribute("data-scenario") === sc);
        }});

        /* scenario description */
        document.getElementById("pb-scenario-desc").innerHTML = cfg.desc;

        /* show/hide fields */
        document.querySelectorAll(".pb-field").forEach(function(f) {{
          f.classList.remove("visible");
        }});
        cfg.visibleFields.forEach(function(f) {{
          var fieldDiv = document.getElementById("pb-field-" + f);
          if (fieldDiv) fieldDiv.classList.add("visible");
        }});

        /* update labels, hints, placeholders for shared fields */
        ["project", "scope", "constraints", "done"].forEach(function(f) {{
          var labelEl = document.getElementById("pb-label-" + f);
          if (labelEl && cfg.labels[f]) labelEl.textContent = cfg.labels[f];
          var hintEl = document.getElementById("pb-hint-" + f);
          if (hintEl && cfg.hints[f]) hintEl.textContent = cfg.hints[f];
          var inputEl = document.getElementById("pb-" + f);
          if (inputEl && cfg.placeholders[f]) inputEl.placeholder = cfg.placeholders[f];
        }});

        /* clear all input values */
        ["project","feature","mode","revision","overview","scope","audience","constraints","done"].forEach(function(f) {{
          var el = document.getElementById("pb-" + f);
          if (el && el.tagName !== "SELECT") el.value = "";
        }});

        /* mode selector */
        var modeEl = document.getElementById("pb-mode");
        modeEl.value = cfg.mode;

        pbUpdatePreview();
      }}

      function pbUpdatePreview() {{
        var sc = pbCurrentScenario;
        var cfg = pbScenarios[sc];
        var v = {{
          project: pbUserVal("pb-project"),
          feature: pbUserVal("pb-feature"),
          revision: pbUserVal("pb-revision"),
          overview: pbUserVal("pb-overview"),
          audience: pbUserVal("pb-audience"),
          scope: pbUserVal("pb-scope"),
          constraints: pbUserVal("pb-constraints"),
          done: pbUserVal("pb-done")
        }};
        var prompt = pbBuildPrompt(sc, v);
        document.getElementById("pb-output").textContent = prompt;
      }}

      document.addEventListener("DOMContentLoaded", function() {{
        pbSwitchScenario("feature");
        var ids = ["pb-project","pb-feature","pb-mode","pb-revision","pb-overview","pb-scope","pb-audience","pb-constraints","pb-done"];
        ids.forEach(function(id) {{
          var el = document.getElementById(id);
          if (el) {{
            el.addEventListener("input", pbUpdatePreview);
            el.addEventListener("change", pbUpdatePreview);
          }}
        }});
      }});
    </script>
  </body>
</html>
"""
