#!/usr/bin/env python3
"""Turn a `Shot` plus captions into an annotated, animated SVG.

One renderer for both capture front-ends. A terminal shot draws its own text;
a browser shot embeds a PNG; from there the callout geometry is identical, so
a CLI walkthrough and a UI walkthrough look like the same document.

Output is a single self-contained `.svg` file, meant to be referenced from
markdown the ordinary way:

    ![Running the test groups](../images/cli/pytest-groups.svg)

Plain image syntax on purpose. Verified for the docs site: nothing in the
Quartz config's `ignorePatterns` excludes `.svg`, so the Assets emitter copies
the file through, and an `<img>` at an SVG renders with the CSS animation
inside it still running — so the figure reveals itself in order without the
site needing to know anything about it.

Colour does NOT come from the reader's theme. `prefers-color-scheme` inside an
`<img>`-loaded SVG is not reliably delivered (measured: Chromium renders both
schemes identically, even with the SVG loaded as the document), so each figure
declares the surface that matches what it is showing and the page behind it
stays transparent.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path

from docshots.model import Box, Shot

BADGE_R = 8.5         # radius of a numbered mark. Small enough that two
                      # marks on adjacent terminal rows (18px apart) clear
                      # each other without a separation pass.
BADGE_GAP = 3.0       # mark to the edge of the thing it marks
HALO_STROKE = 1.6
FIG_PAD = 12.0        # card edge to the picture inside it
MARGIN = 24.0         # reserved lane each side of the picture, for the marks
LEADER_MIN = 4.0      # shorter than this and the line is noise, not a connector
LEGEND_GAP = 18.0     # picture bottom to first legend row
LEGEND_PAD = 4.0
LEGEND_LH = 19.0
LEGEND_ROW_GAP = 12.0
LEGEND_CW = 7.1       # caption advance width, for wrapping only
STEP_DELAY = 0.45     # seconds between one callout appearing and the next


@dataclass
class Callout:
    """A caption pointing at a named anchor.

    `step` renders a numbered badge; leave it None for an unnumbered note.
    Numbering is the reader's promise that order matters, so it is opt-in
    rather than automatic — a page pointing out three independent things on
    one screen should not imply a sequence.
    """

    anchor: str
    text: str
    step: int | None = None


def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if len(candidate) > width and line:
            out.append(line)
            line = word
        else:
            line = candidate
    if line:
        out.append(line)
    return out or [""]


SURFACES = {
    # Each surface is a complete, self-sufficient palette. There is no media
    # query here on purpose: an SVG loaded through `<img>` does not reliably
    # receive the reader's colour-scheme preference — measured in Chromium,
    # both schemes render identically, even with the SVG as the document — so
    # a picture that relied on one would be a coin flip. The surface is chosen
    # to match the thing being annotated instead: dark for a terminal, light
    # for a screenshot of the app in its light theme.
    "dark": {
        "panel": "#20262e", "ink": "#e6ebf2", "line": "#39434f",
        "term_bg": "#20262e", "term_ink": "#dfe5ee",
        "accent": "#3fb8c9", "accent_soft": "rgba(63,184,201,.16)",
    },
    "light": {
        "panel": "#ffffff", "ink": "#1c2430", "line": "#d7dce4",
        "term_bg": "#20262e", "term_ink": "#dfe5ee",
        "accent": "#0f7d8c", "accent_soft": "rgba(15,125,140,.14)",
    },
}


def _theme_css(surface: str) -> str:
    """One palette, written out flat.

    The page itself stays transparent so the figure sits on whatever the docs
    page paints rather than punching a slab of the wrong colour into it.
    """
    try:
        s = SURFACES[surface]
    except KeyError:
        raise ValueError(f"unknown surface {surface!r}; expected one of {sorted(SURFACES)}")
    return (
        "  :root{"
        + "".join(f"--ds-{k.replace('_', '-')}:{v};" for k, v in s.items())
        + "}\n"
    )


def _static_css() -> str:
    """Final state, no motion.

    Not every place a docs picture lands can animate: a PDF export, a release
    email, GitHub's own markdown renderer. The static build is the same
    drawing with every callout already shown, so one source produces both and
    the animated one is never the only readable version.
    """
    return (
        "  .ds-badge,.ds-legend,.ds-leader{opacity:1}\n"
        "  .ds-halo{opacity:.85}\n"
    )


def _anim_css(count: int) -> str:
    """Reveal each mark, and its legend row, in reading order.

    There is nothing to "draw" any more — the guidance is a number on the
    thing itself — so the motion that helps is the one that walks a reader
    through them in order. The halo keeps a slow pulse so the target stays
    findable on a busy screenshot.
    """
    rules = [
        "  @keyframes ds-pop{from{opacity:0;transform:scale(.82)}to{opacity:1;transform:none}}",
        "  @keyframes ds-fade{from{opacity:0}to{opacity:1}}",
        "  @keyframes ds-ring{0%,100%{opacity:.6}50%{opacity:1}}",
        "  .ds-badge{opacity:0;animation:ds-pop .32s cubic-bezier(.2,.9,.3,1.2) forwards;"
        "transform-box:fill-box;transform-origin:center}",
        "  .ds-halo{opacity:0;animation:ds-fade .3s ease-out forwards,"
        "ds-ring 2.6s ease-in-out infinite}",
        "  .ds-legend,.ds-leader{opacity:0;animation:ds-fade .3s ease-out forwards}",
    ]
    for i in range(count):
        d = i * STEP_DELAY
        rules.append(
            f"  .ds-s{i} .ds-badge,.ds-s{i} .ds-leader{{animation-delay:{d:.2f}s}}"
            f"  .ds-s{i}.ds-legend{{animation-delay:{d:.2f}s}}"
            f"  .ds-s{i} .ds-halo{{animation-delay:{d:.2f}s,{d:.2f}s}}"
        )
    rules.append(
        "  @media (prefers-reduced-motion: reduce){"
        ".ds-badge,.ds-halo,.ds-legend,.ds-leader{opacity:1;animation:none}"
        ".ds-halo{opacity:.85}}"
    )
    return "\n".join(rules)


def _terminal_body(shot: Shot) -> str:
    """The terminal grid, one glyph per x position so columns cannot drift."""
    c = shot.cells or {}
    cw, ch = c.get("cell_w", 8.4), c.get("cell_h", 18.0)
    px, py, chrome = c.get("pad_x", 16.0), c.get("pad_y", 14.0), c.get("chrome_h", 32.0)
    parts = [
        f'<rect x="0" y="0" width="{shot.width:.1f}" height="{shot.height:.1f}" '
        f'rx="10" fill="var(--ds-term-bg)"/>',
        f'<rect x="0" y="0" width="{shot.width:.1f}" height="{chrome:.1f}" '
        f'rx="10" fill="var(--ds-term-bg)"/>',
        f'<rect x="0" y="{chrome - 10:.1f}" width="{shot.width:.1f}" height="10" '
        f'fill="var(--ds-term-bg)"/>',
        f'<line x1="0" y1="{chrome:.1f}" x2="{shot.width:.1f}" y2="{chrome:.1f}" '
        f'stroke="rgba(255,255,255,.09)"/>',
    ]
    for i, colour in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        parts.append(f'<circle cx="{18 + i * 17}" cy="{chrome / 2:.1f}" r="5.5" fill="{colour}"/>')
    if shot.title:
        parts.append(
            f'<text x="{shot.width / 2:.1f}" y="{chrome / 2 + 4:.1f}" text-anchor="middle" '
            f'font-size="11.5" fill="rgba(223,229,238,.55)" '
            f'font-family="ui-monospace,SFMono-Regular,Menlo,monospace">'
            f"{escape(shot.title)}</text>"
        )

    def row_y(r: int) -> float:
        return chrome + py + r * ch + ch * 0.72

    def glyph_run(text: str, col: int, y: float, fill: str, weight: str, opacity: str) -> str:
        xs = " ".join(f"{px + (col + i) * cw:.1f}" for i in range(len(text)))
        return (
            f'<text x="{xs}" y="{y:.1f}" fill="{fill}" font-weight="{weight}"{opacity} '
            f'font-size="13" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" '
            f'xml:space="preserve">{escape(text)}</text>'
        )

    r0 = 0
    if c.get("command"):
        y = row_y(0)
        parts.append(glyph_run("$", 0, y, "var(--ds-accent)", "700", ""))
        parts.append(glyph_run(c["command"], 2, y, "var(--ds-term-ink)", "600", ""))
        r0 = 1

    for r, runs in enumerate(c.get("lines", [])):
        y, col = row_y(r + r0), 0
        for run in runs:
            t = run["text"]
            if t.strip():
                parts.append(glyph_run(
                    t, col, y,
                    run.get("fg") or "var(--ds-term-ink)",
                    "700" if run.get("bold") else "400",
                    ' opacity="0.62"' if run.get("dim") else "",
                ))
            col += len(t)
    return "\n".join(parts)


def _image_body(shot: Shot) -> str:
    return (
        f'<rect x="0" y="0" width="{shot.width:.1f}" height="{shot.height:.1f}" rx="10" '
        f'fill="var(--ds-panel)"/>'
        f'<image x="0" y="0" width="{shot.width:.1f}" height="{shot.height:.1f}" '
        f'href="{shot.image_href}" preserveAspectRatio="xMidYMid meet"/>'
        f'<rect x="0.5" y="0.5" width="{shot.width - 1:.1f}" height="{shot.height - 1:.1f}" '
        f'rx="10" fill="none" stroke="var(--ds-line)"/>'
    )


def _legend_rows(resolved, width: float) -> tuple[list[tuple[float, list[str]]], float]:
    """Legend line-wrapping and heights, given the picture's width."""
    text_left = -MARGIN + LEGEND_PAD + BADGE_R * 2 + 10
    chars = max(int((width - text_left - LEGEND_PAD) / LEGEND_CW), 18)
    rows, y = [], 0.0
    for _callout, _box in resolved:
        lines = _wrap(_callout.text, chars)
        rows.append((y, lines))
        y += len(lines) * LEGEND_LH + LEGEND_ROW_GAP
    return rows, y


def _badge_positions(resolved, shot: Shot) -> list[tuple[float, float, str]]:
    """Place every mark, and say how it was placed.

    Two placements, chosen by the target's own size:

    * **Inside a corner**, for a target big enough to hold a mark without
      covering what it is marking — a tab, a card, a panel. No leader is
      needed: the mark is on the thing.
    * **In a lane beside the picture**, with a short leader, for anything too
      small to sit a mark on — a switch, a table row, a column header.

    Size decides it rather than position, and that is the correction: an
    earlier version sent everything to the lanes, so a horizontal row of five
    tabs produced five leaders cutting straight across the sidebar to reach
    them. Lanes assume targets are spread vertically; a toolbar is the case
    where that assumption is exactly wrong.
    """
    # Roughly 3.5x the mark in BOTH dimensions. "Big enough to fit" is the
    # wrong test: a 39px-tall button fits a 17px mark and still has its label
    # centred right where the mark lands. A control only has a genuinely
    # empty corner once it is several times the mark's size — a tab (98x72)
    # does, a button (119x39) and a column header (194x56) do not.
    ROOMY = BADGE_R * 7
    picked: list[list] = []

    for _callout, box in resolved:
        if box.width >= ROOMY and box.height >= ROOMY:
            picked.append([box.x + BADGE_R + BADGE_GAP, box.y + BADGE_R + BADGE_GAP, "inside"])
            continue
        side = "left" if box.x <= shot.width - (box.x + box.width) else "right"
        cx = -MARGIN / 2 if side == "left" else shot.width + MARGIN / 2
        picked.append([cx, min(max(box.cy, BADGE_R), shot.height - BADGE_R), side])

    # Lane marks are nudged apart along their lane; the leader keeps showing
    # which row each belongs to. Inside marks sit on distinct boxes and are
    # left alone.
    for side in ("left", "right"):
        lane = sorted((i for i, p in enumerate(picked) if p[2] == side),
                      key=lambda i: picked[i][1])
        floor = -1e9
        for i in lane:
            picked[i][1] = max(picked[i][1], floor + BADGE_R * 2 + 2)
            floor = picked[i][1]
    return [(cx, cy, side) for cx, cy, side in picked]


def render(shot: Shot, callouts: list[Callout], *, strict: bool = True,
           animate: bool = True, surface: str = "dark") -> str:
    """The annotated SVG: numbered marks on the picture, captions beneath it.

    The marks sit ON their targets and the captions sit under the picture,
    keyed by number. An earlier version put captions in a side gutter and drew
    a curved arrow to each target; every caption for anything on the left then
    dragged an arrow across the whole screenshot, over the content it was
    trying to explain. There is no placement rule that fixes that — the
    geometry is wrong — so the arrows are gone. What is left cannot be
    misplaced: a number cannot cross the image, and a legend row cannot
    overlap a control.

    `strict` raises when a callout names an anchor the capture could not
    resolve. That is the whole point of anchoring to selectors and regexes:
    when the product moves, the build says so instead of shipping a picture
    that quietly points at nothing.
    """
    # Minimum of a target that must actually be in the picture. Below this the
    # anchor resolved to something the capture did not photograph.
    VISIBLE_MIN = 4.0

    def clip(b: Box) -> Box | None:
        """The part of `b` inside the picture, or None if none of it is.

        Two different situations that used to be conflated, to this figure's
        cost:

        * A box that OVERFLOWS the picture is still visible — an AG Grid row is
          as wide as every column including those scrolled out of view — so it
          is trimmed to what the reader can see.
        * A box ENTIRELY outside was never photographed. The table-settings
          popover scrolls, and `COLUMN FORMATS` sits below its fold; clamping
          it produced a highlight pinned to the bottom edge, marking a control
          that is not in the picture at all. That has to fail, so `--check`
          reports it and the figure gets scrolled or re-clipped.
        """
        x, y = max(b.x, 0.0), max(b.y, 0.0)
        w = min(b.x + b.width, shot.width) - x
        h = min(b.y + b.height, shot.height) - y
        if w < VISIBLE_MIN or h < VISIBLE_MIN:
            return None
        return Box(x=x, y=y, width=w, height=h)

    resolved: list[tuple[Callout, Box]] = []
    problems: list[str] = []
    for c in callouts:
        a = shot.anchor(c.anchor)
        if a is None:
            problems.append(f"{c.anchor!r}: no such anchor in the shot")
        elif not a.found or a.box is None:
            problems.append(f"{c.anchor!r}: {a.detail or 'not found in the capture'}")
        else:
            visible = clip(a.box)
            if visible is None:
                problems.append(
                    f"{c.anchor!r}: resolved at "
                    f"({a.box.x:.0f},{a.box.y:.0f} {a.box.width:.0f}x{a.box.height:.0f}) "
                    f"but the picture is {shot.width:.0f}x{shot.height:.0f} — it is outside "
                    f"the captured area, so nothing can point at it"
                )
            else:
                resolved.append((c, visible))
    if problems and strict:
        raise ValueError("annotation anchors did not resolve:\n  " + "\n  ".join(problems))

    # Reading order: down the picture, then across. The legend follows the
    # same order, so the numbers a reader meets going down the image are the
    # order they are explained in.
    resolved.sort(key=lambda cb: (cb[1].y, cb[1].x))

    rows, legend_h = _legend_rows(resolved, shot.width + MARGIN * 2)
    total_w = shot.width + (FIG_PAD + MARGIN) * 2
    total_h = shot.height + FIG_PAD * 2 + (LEGEND_GAP + legend_h if resolved else 0)

    body = _terminal_body(shot) if shot.cells else _image_body(shot)
    marks, legend = [], []
    badge_xy = _badge_positions(resolved, shot)
    text_left = -MARGIN + LEGEND_PAD + BADGE_R * 2 + 10

    for i_pos, ((callout, box), (row_y, lines)) in enumerate(zip(resolved, rows)):
        n = callout.step if callout.step is not None else i_pos + 1

        # Inset by half the stroke so the whole outline lands INSIDE the box.
        # Outsetting it — the first version added 3px on every side — makes
        # highlights on adjacent 18px terminal rows overlap by construction.
        i = HALO_STROKE / 2
        halo = (
            f'<rect class="ds-halo" x="{box.x + i:.1f}" y="{box.y + i:.1f}" '
            f'width="{max(box.width - HALO_STROKE, 1):.1f}" '
            f'height="{max(box.height - HALO_STROKE, 1):.1f}" rx="4" '
            f'fill="var(--ds-accent-soft)" stroke="var(--ds-accent)" '
            f'stroke-width="{HALO_STROKE}"/>'
        )
        bx, by, side = badge_xy[i_pos]
        leader = ""
        if side != "inside":
            edge = box.x if side == "left" else box.x + box.width
            tip = bx + BADGE_R if side == "left" else bx - BADGE_R
            if abs(edge - tip) > LEADER_MIN:
                leader = (
                    f'<path class="ds-leader" d="M{tip:.1f},{by:.1f} L{edge:.1f},{box.cy:.1f}" '
                    f'fill="none" stroke="var(--ds-accent)" stroke-width="1.6" '
                    f'stroke-linecap="round" opacity="0.85"/>'
                )
        mark = (
            f'{leader}<g class="ds-badge">'
            f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="{BADGE_R}" fill="var(--ds-accent)" '
            f'stroke="var(--ds-panel)" stroke-width="1.5"/>'
            f'<text x="{bx:.1f}" y="{by + 3.9:.1f}" text-anchor="middle" font-size="11" '
            f'font-weight="700" fill="#fff" '
            f'font-family="system-ui,-apple-system,Segoe UI,sans-serif">{n}</text>'
            f'</g>'
        )
        marks.append(f'<g class="ds-s{i_pos}">{halo}{mark}</g>')

        ly = shot.height + LEGEND_GAP + row_y
        tspans = "".join(
            f'<tspan x="{text_left:.1f}" dy="{0 if j == 0 else LEGEND_LH}">{escape(l)}</tspan>'
            for j, l in enumerate(lines)
        )
        legend.append(
            f'<g class="ds-s{i_pos} ds-legend">'
            f'<circle cx="{-MARGIN + LEGEND_PAD + BADGE_R:.1f}" cy="{ly + BADGE_R:.1f}" r="{BADGE_R}" '
            f'fill="var(--ds-accent)"/>'
            f'<text x="{-MARGIN + LEGEND_PAD + BADGE_R:.1f}" y="{ly + BADGE_R + 3.9:.1f}" '
            f'text-anchor="middle" font-size="11" font-weight="700" fill="#fff" '
            f'font-family="system-ui,-apple-system,Segoe UI,sans-serif">{n}</text>'
            f'<text y="{ly + BADGE_R + 4.5:.1f}" font-size="13.5" fill="var(--ds-ink)" '
            f'font-family="system-ui,-apple-system,Segoe UI,Roboto,sans-serif">{tspans}</text>'
            f'</g>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_w:.0f}" height="{total_h:.0f}" \
viewBox="0 0 {total_w:.0f} {total_h:.0f}" role="img" \
aria-label="{escape(shot.title or 'annotated screenshot')}">
<style>
{_theme_css(surface)}{_anim_css(len(resolved)) if animate else _static_css()}
</style>
<rect x="0.5" y="0.5" width="{total_w - 1:.0f}" height="{total_h - 1:.0f}" rx="12" \
fill="var(--ds-panel)" stroke="var(--ds-line)"/>
<g transform="translate({FIG_PAD + MARGIN},{FIG_PAD})">
<g>{body}</g>
{"".join(marks)}
{"".join(legend)}
</g>
</svg>
"""


def write(shot: Shot, callouts: list[Callout], path: str | Path, *,
          strict: bool = True, animate: bool = True, surface: str = "dark") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render(shot, callouts, strict=strict, animate=animate, surface=surface),
                 encoding="utf-8")
    return p
