#!/usr/bin/env python3
"""The one thing both capture front-ends produce.

A `Shot` is a picture plus the coordinates of the things worth pointing at. It
is deliberately dumb — no rendering, no ANSI, no DOM — so the terminal capture
and the Playwright capture can meet here without knowing about each other, and
so a shot can be written to JSON, reviewed in a diff, and re-rendered without
re-running the product.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass(frozen=True)
class Box:
    """A rectangle in the shot's own pixel space, top-left origin."""

    x: float
    y: float
    width: float
    height: float

    @property
    def cx(self) -> float:
        return self.x + self.width / 2

    @property
    def cy(self) -> float:
        return self.y + self.height / 2


@dataclass
class Anchor:
    """Something in the shot worth pointing at.

    `name` is how a caption refers to it. `found` is False when the capture
    looked and could not find it — kept rather than dropped, because a missing
    anchor means the documented thing has moved or gone, and that is exactly
    the signal a docs build should fail on rather than silently omit.
    """

    name: str
    box: Box | None = None
    found: bool = True
    detail: str = ""

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "box": asdict(self.box) if self.box else None,
            "found": self.found,
            "detail": self.detail,
        }

    @staticmethod
    def from_json(d: dict) -> "Anchor":
        box = d.get("box")
        return Anchor(
            name=d["name"],
            box=Box(**box) if box else None,
            found=bool(d.get("found", box is not None)),
            detail=d.get("detail", ""),
        )


@dataclass
class Shot:
    """A captured picture and its anchors.

    Exactly one of `image_href` (a browser screenshot, embedded or referenced)
    or `cells` (a terminal grid) carries the picture. Both are rendered onto
    the same SVG canvas by `annotate`, which is why an arrow looks the same
    whether it is pointing at a button or at a line of CLI output.
    """

    width: float
    height: float
    anchors: list[Anchor] = field(default_factory=list)
    image_href: str | None = None
    cells: dict | None = None
    title: str = ""
    surface: str = "dark"

    def anchor(self, name: str) -> Anchor | None:
        for a in self.anchors:
            if a.name == name:
                return a
        return None

    def missing(self) -> list[str]:
        """Anchors the capture could not resolve. A docs build should fail on these."""
        return [a.name for a in self.anchors if not a.found or a.box is None]

    def to_json(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "title": self.title,
            "image_href": self.image_href,
            "cells": self.cells,
            "surface": self.surface,
            "anchors": [a.to_json() for a in self.anchors],
        }

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.write_text(json.dumps(self.to_json(), indent=2), encoding="utf-8")
        return p

    @staticmethod
    def read(path: str | Path) -> "Shot":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return Shot(
            width=d["width"],
            height=d["height"],
            title=d.get("title", ""),
            image_href=d.get("image_href"),
            cells=d.get("cells"),
            surface=d.get("surface", "dark"),
            anchors=[Anchor.from_json(a) for a in d.get("anchors", [])],
        )


def load(path: str | Path) -> Shot:
    """Read a shot and inline its image, if it has one.

    A browser capture writes the PNG beside the JSON and refers to it by
    relative name. Inlining it here is what makes the rendered SVG a single
    portable file — one thing to copy into the docs repo, one thing to review
    in a pull request, and no second asset to lose.
    """
    p = Path(path)
    shot = Shot.read(p)
    href = shot.image_href
    if href and not href.startswith(("data:", "http://", "https://")):
        img = (p.parent / href).resolve()
        if not img.exists():
            raise FileNotFoundError(f"{p}: image_href points at {img}, which does not exist")
        mime = mimetypes.guess_type(img.name)[0] or "image/png"
        shot.image_href = f"data:{mime};base64," + base64.b64encode(img.read_bytes()).decode()
    return shot
