#!/usr/bin/env python3
"""Capture a real command's output as a `Shot`.

The command is actually run — this is not a transcript someone typed up, which
is the failure mode every hand-written CLI doc eventually reaches. Output that
no longer matches the tool produces a different picture on the next build, and
an anchor that no longer matches fails the build.

Anchors are regular expressions over the *plain* text (ANSI already stripped),
so `pytest-groups` points at the `pytest-groups` row wherever the row happens
to be. Nothing is anchored to a line number.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess

from docshots.model import Anchor, Box, Shot

# Cell metrics. The renderer positions every glyph individually at a multiple
# of CELL_W, so these are the grid, not a hope about the reader's font.
CELL_W = 8.4
CELL_H = 18.0
PAD_X = 16.0
PAD_Y = 14.0
CHROME_H = 32.0  # title bar

_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_ANSI_ANY = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# The 8 base colours, at the brightness a docs page wants rather than a
# terminal's. Deliberately muted: a screenshot full of pure #00FF00 reads as a
# 1998 hacker film, not as a product.
BASE = {
    30: "#3b4252", 31: "#bf4d5a", 32: "#3f8f5b", 33: "#a07a2c",
    34: "#3b6ea5", 35: "#8a5fa8", 36: "#2f7f88", 37: "#d8dee9",
}
BRIGHT = {
    90: "#6b7280", 91: "#e06c75", 92: "#59b37a", 93: "#c79a3e",
    94: "#5b8fd6", 95: "#a97fd0", 96: "#46a3ad", 97: "#f2f5fa",
}


class Style:
    __slots__ = ("fg", "bold", "dim")

    def __init__(self, fg: str | None = None, bold: bool = False, dim: bool = False):
        self.fg, self.bold, self.dim = fg, bold, dim

    def copy(self) -> "Style":
        return Style(self.fg, self.bold, self.dim)

    def key(self) -> tuple:
        return (self.fg, self.bold, self.dim)


def _apply_sgr(style: Style, params: str) -> Style:
    s = style.copy()
    codes = [int(p) if p else 0 for p in (params.split(";") if params else ["0"])]
    i = 0
    while i < len(codes):
        c = codes[i]
        if c == 0:
            s = Style()
        elif c == 1:
            s.bold = True
        elif c == 2:
            s.dim = True
        elif c == 22:
            s.bold = s.dim = False
        elif c == 39:
            s.fg = None
        elif c in BASE:
            s.fg = BASE[c]
        elif c in BRIGHT:
            s.fg = BRIGHT[c]
        elif c == 38 and i + 1 < len(codes):
            # 38;5;N and 38;2;R;G;B. Consumed so the following numbers are not
            # mistaken for further SGR codes.
            if codes[i + 1] == 5 and i + 2 < len(codes):
                s.fg = _xterm256(codes[i + 2]); i += 2
            elif codes[i + 1] == 2 and i + 4 < len(codes):
                s.fg = "#%02x%02x%02x" % tuple(codes[i + 2:i + 5]); i += 4
        i += 1
    return s


def _xterm256(n: int) -> str:
    if n < 8:
        return BASE[30 + n]
    if n < 16:
        return BRIGHT[90 + n - 8]
    if n < 232:
        n -= 16
        levels = [0, 95, 135, 175, 215, 255]
        return "#%02x%02x%02x" % (levels[n // 36], levels[(n // 6) % 6], levels[n % 6])
    v = 8 + (n - 232) * 10
    return "#%02x%02x%02x" % (v, v, v)


def parse_ansi(text: str) -> list[list[dict]]:
    """Lines of styled runs: [[{text, fg, bold, dim}, ...], ...]."""
    lines: list[list[dict]] = []
    style = Style()
    for raw in text.replace("\r\n", "\n").expandtabs(4).split("\n"):
        runs: list[dict] = []
        pos = 0
        for m in _SGR.finditer(raw):
            chunk = raw[pos:m.start()]
            if chunk:
                runs.append({"text": chunk, "fg": style.fg, "bold": style.bold, "dim": style.dim})
            style = _apply_sgr(style, m.group(1))
            pos = m.end()
        tail = _ANSI_ANY.sub("", raw[pos:])
        if tail:
            runs.append({"text": tail, "fg": style.fg, "bold": style.bold, "dim": style.dim})
        lines.append(runs)
    return lines


def plain(lines: list[list[dict]]) -> list[str]:
    return ["".join(r["text"] for r in line) for line in lines]


def _box_for(row: int, col: int, length: int, *, has_prompt: bool) -> Box:
    """Grid cell -> pixels. `has_prompt` shifts every row down by the command line."""
    top = CHROME_H + PAD_Y + (row + (1 if has_prompt else 0)) * CELL_H
    return Box(x=PAD_X + col * CELL_W, y=top, width=max(length, 1) * CELL_W, height=CELL_H)


def capture(
    command: str,
    *,
    anchors: dict[str, str] | None = None,
    cwd: str | None = None,
    env: dict | None = None,
    title: str = "",
    show_prompt: bool = True,
    max_lines: int = 40,
    timeout: int = 120,
    runner=subprocess.run,
) -> Shot:
    """Run `command` and return a Shot of its output.

    `anchors` maps a name to a regex searched against the plain output. The
    match's line and column become the anchor's box, so the arrow lands on the
    matched text itself. A regex that matches nothing yields `found=False`
    rather than an exception — `Shot.missing()` is where a build decides what
    to do about it, and one report of every miss beats failing on the first.
    """
    run_env = {**os.environ, "COLUMNS": "100", "TERM": "xterm-256color", **(env or {})}
    proc = runner(
        shlex.split(command), cwd=cwd, env=run_env,
        capture_output=True, text=True, timeout=timeout,
    )
    out = (proc.stdout or "") + (proc.stderr or "")

    styled = parse_ansi(out.rstrip("\n"))[:max_lines]
    text_lines = plain(styled)

    cols = max([len(l) for l in text_lines] + [len(command) + 2, 20])
    rows = len(styled) + (1 if show_prompt else 0)

    resolved: list[Anchor] = []
    for name, pattern in (anchors or {}).items():
        rx = re.compile(pattern)
        hit = None
        for row, line in enumerate(text_lines):
            m = rx.search(line)
            if m:
                hit = Anchor(name, _box_for(row, m.start(), m.end() - m.start(),
                                            has_prompt=show_prompt),
                             detail=m.group(0))
                break
        resolved.append(hit or Anchor(name, None, found=False,
                                      detail=f"no line matched /{pattern}/"))

    return Shot(
        width=PAD_X * 2 + cols * CELL_W,
        height=CHROME_H + PAD_Y * 2 + rows * CELL_H,
        anchors=resolved,
        title=title or command,
        cells={
            "lines": styled,
            "command": command if show_prompt else None,
            "cell_w": CELL_W, "cell_h": CELL_H,
            "pad_x": PAD_X, "pad_y": PAD_Y, "chrome_h": CHROME_H,
            "exit_code": proc.returncode,
        },
    )
