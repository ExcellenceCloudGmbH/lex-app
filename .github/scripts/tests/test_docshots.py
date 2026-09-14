"""Tests for docshots — documentation figures captured from the live product.

The behaviour worth pinning is not "does it draw a rectangle". It is that a
figure fails loudly when the product moves, because the entire reason anchors
are regexes and CSS selectors rather than pixel coordinates is to convert
"this screenshot quietly became a lie" into "the docs build went red".
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from docshots import annotate, model, terminal  # noqa: E402
from docshots.annotate import Callout  # noqa: E402
from docshots.model import Anchor, Box, Shot  # noqa: E402


def _fake_run(stdout: str, returncode: int = 0):
    """A `subprocess.run` stand-in, so no test needs a real binary."""
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, returncode, stdout, "")
    return run


# ── capture ──────────────────────────────────────────────────────────────

def test_an_anchor_matches_the_text_not_a_line_number():
    # The point of regex anchors: inserting a command above `streamlit` must
    # not move its arrow onto the wrong row.
    before = terminal.capture("lex --help", anchors={"s": r"^\s+streamlit\b"},
                              runner=_fake_run("Commands:\n  start\n  streamlit\n"))
    after = terminal.capture("lex --help", anchors={"s": r"^\s+streamlit\b"},
                             runner=_fake_run("Commands:\n  aaa\n  start\n  streamlit\n"))
    assert before.anchor("s").found and after.anchor("s").found
    # One row further down, and the renderer learns that from the capture.
    assert after.anchor("s").box.y > before.anchor("s").box.y


def test_an_anchor_that_matches_nothing_is_recorded_not_raised():
    # One capture should report every miss, not die on the first — otherwise a
    # docs build is fixed one anchor per run.
    shot = terminal.capture("lex --help",
                            anchors={"gone": r"^\s+retired-command\b", "there": r"Commands:"},
                            runner=_fake_run("Commands:\n  start\n"))
    assert shot.missing() == ["gone"]
    assert shot.anchor("gone").detail.startswith("no line matched")
    assert shot.anchor("there").found


def test_the_box_covers_the_matched_text_only():
    shot = terminal.capture("x", anchors={"m": r"needle"},
                            runner=_fake_run("hay needle hay"), show_prompt=False)
    box = shot.anchor("m").box
    assert box.x == pytest.approx(terminal.PAD_X + 4 * terminal.CELL_W)
    assert box.width == pytest.approx(len("needle") * terminal.CELL_W)


def test_ansi_colour_is_parsed_and_stripped_from_the_plain_text():
    shot = terminal.capture("x", anchors={"m": r"^error: nope$"},
                            runner=_fake_run("\x1b[31merror\x1b[0m: nope"), show_prompt=False)
    # The anchor matched, so the regex saw plain text with no escape codes.
    assert shot.anchor("m").found
    runs = shot.cells["lines"][0]
    assert runs[0]["text"] == "error" and runs[0]["fg"] == terminal.BASE[31]
    assert runs[1]["fg"] is None          # reset applied


def test_a_truecolour_sequence_does_not_leak_its_numbers_into_the_text():
    # 38;2;R;G;B carries four numbers that are NOT further SGR codes. Consuming
    # them wrongly used to print the colour as text.
    shot = terminal.capture("x", anchors={"m": r"^hello$"},
                            runner=_fake_run("\x1b[38;2;255;0;0mhello"), show_prompt=False)
    assert shot.anchor("m").found
    assert shot.cells["lines"][0][0]["fg"] == "#ff0000"


def test_the_exit_code_is_kept():
    shot = terminal.capture("x", runner=_fake_run("boom", returncode=2))
    assert shot.cells["exit_code"] == 2


# ── rendering ────────────────────────────────────────────────────────────

def _shot_with(box: Box | None, *, found: bool = True, w: float = 400, h: float = 200) -> Shot:
    return Shot(width=w, height=h, image_href="data:image/png;base64,AA==",
                anchors=[Anchor("target", box, found=found)])


def test_a_missing_anchor_fails_the_render():
    with pytest.raises(ValueError, match="did not resolve"):
        annotate.render(_shot_with(None, found=False), [Callout("target", "gone")])


def test_a_callout_naming_an_unknown_anchor_fails_the_render():
    with pytest.raises(ValueError, match="no such anchor"):
        annotate.render(_shot_with(Box(1, 1, 10, 10)), [Callout("typo", "oops")])


def test_a_missing_anchor_can_be_tolerated_deliberately():
    svg = annotate.render(_shot_with(None, found=False), [Callout("target", "gone")], strict=False)
    assert svg.startswith("<svg")


def test_a_box_wider_than_the_picture_is_clamped_to_it():
    # A grid row is as wide as every column, including those scrolled out of
    # sight; unclamped, its highlight runs across the captions.
    svg = annotate.render(_shot_with(Box(320, 100, 2083, 25), w=1280, h=720),
                          [Callout("target", "the row")])
    widths = [w for _x, _y, w, _h in _halos(svg)]
    assert widths and max(widths) <= 1280


def test_the_animated_and_static_builds_differ_only_in_motion():
    shot = _shot_with(Box(10, 10, 40, 20))
    call = [Callout("target", "a caption", step=1)]
    animated = annotate.render(shot, call, animate=True)
    static = annotate.render(shot, call, animate=False)
    assert "@keyframes" in animated and "@keyframes" not in static
    assert "prefers-reduced-motion" in animated
    for fragment in ('class="ds-halo"', "ds-badge", "a caption"):
        assert fragment in animated and fragment in static


def test_the_figure_declares_no_colour_that_depends_on_the_reader():
    # An <img>-loaded SVG does not reliably receive prefers-color-scheme, so a
    # figure that relied on it would be a coin flip.
    svg = annotate.render(_shot_with(Box(10, 10, 40, 20)), [Callout("target", "x")])
    assert "prefers-color-scheme" not in svg


def test_each_surface_paints_its_own_palette():
    dark = annotate.render(_shot_with(Box(1, 1, 9, 9)), [Callout("target", "x")], surface="dark")
    light = annotate.render(_shot_with(Box(1, 1, 9, 9)), [Callout("target", "x")], surface="light")
    assert annotate.SURFACES["dark"]["panel"] in dark
    assert annotate.SURFACES["light"]["panel"] in light
    with pytest.raises(ValueError, match="unknown surface"):
        annotate.render(_shot_with(Box(1, 1, 9, 9)), [Callout("target", "x")], surface="beige")


def test_the_legend_sits_on_the_figure_own_surface():
    # The legend used to be drawn straight onto the page. With a dark picture
    # that meant near-white caption text on whatever the docs page painted —
    # invisible on a light one. The figure carries its own card instead.
    svg = annotate.render(_shot_with(Box(1, 1, 9, 9)), [Callout("target", "a caption")],
                          surface="dark")
    card = re.search(r'<rect x="0.5" y="0.5"[^>]*fill="var\(--ds-panel\)"', svg)
    assert card, "no surface card behind the figure"
    assert svg.index(card.group(0)) < svg.index("a caption")


def _halos(svg: str) -> list[tuple[float, float, float, float]]:
    return [tuple(map(float, m)) for m in re.findall(
        r'class="ds-halo" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg)]


def _badges(svg: str) -> list[tuple[float, float]]:
    """Marks in picture coordinates. Negative x means the left lane."""
    body = svg[svg.index("<g transform="):]
    return [(float(x), float(y)) for x, y in re.findall(
        r'<circle cx="(-?[\d.]+)" cy="(-?[\d.]+)" r="[\d.]+" fill="var\(--ds-accent\)" stroke=', body)]


def test_highlights_on_adjacent_rows_do_not_overlap():
    # Terminal rows are 18px apart. The first version outset every highlight by
    # 3px on each side, so consecutive rows overlapped by 6px and the figure
    # looked like one smeared box.
    shot = Shot(width=400, height=200, image_href="data:image/png;base64,AA==", anchors=[
        Anchor("a", Box(10, 100, 300, 18)),
        Anchor("b", Box(10, 118, 300, 18)),
    ])
    boxes = _halos(annotate.render(shot, [Callout("a", "first"), Callout("b", "second")]))
    assert len(boxes) == 2
    (_, y1, _, h1), (_, y2, _, _) = boxes
    assert y1 + h1 <= y2 + 0.01, f"highlights overlap: {boxes}"


def test_a_mark_is_placed_outside_the_picture_entirely():
    # "Outside the box" was not enough: on the settings panel the space beside
    # a switch is that switch's own label, so numbers landed on the words they
    # were explaining. The lanes are outside the picture, where nothing is
    # drawn, so a mark cannot cover anything.
    shot = Shot(width=400, height=200, image_href="data:image/png;base64,AA==",
                anchors=[Anchor("a", Box(120, 40, 100, 20))])
    (cx, _), = _badges(annotate.render(shot, [Callout("a", "x")]))
    assert cx < 0 or cx > 400, f"mark at {cx} is inside the picture"


def test_a_mark_goes_to_the_lane_nearest_its_target():
    shot = Shot(width=400, height=200, image_href="data:image/png;base64,AA==", anchors=[
        Anchor("near_left", Box(10, 40, 40, 20)),
        Anchor("near_right", Box(340, 100, 50, 20)),
    ])
    left, right = _badges(annotate.render(
        shot, [Callout("near_left", "l"), Callout("near_right", "r")]))
    assert left[0] < 0, "a target hugging the left edge should be marked from the left"
    assert right[0] > 400, "a target hugging the right edge should be marked from the right"


def test_a_roomy_target_carries_its_mark_in_a_corner():
    # A tab is 98x72. Sending it to a lane produced a leader across the whole
    # screenshot — five tabs in a row meant five leaders over the sidebar.
    shot = Shot(width=400, height=300, image_href="data:image/png;base64,AA==",
                anchors=[Anchor("tab", Box(120, 40, 98, 72))])
    (cx, cy), = _badges(annotate.render(shot, [Callout("tab", "x")]))
    assert 120 <= cx <= 218 and 40 <= cy <= 112, "a roomy target should hold its own mark"


def test_a_roomy_target_needs_no_leader():
    # The mark is ON the thing, so a line to it would go nowhere.
    shot = Shot(width=400, height=300, image_href="data:image/png;base64,AA==",
                anchors=[Anchor("tab", Box(120, 40, 98, 72))])
    assert 'class="ds-leader"' not in annotate.render(shot, [Callout("tab", "x")])


def test_a_control_whose_label_fills_it_is_not_roomy():
    # 119x39 fits a 17px mark and still has its label centred exactly where the
    # mark would land. Fitting is not the test; having an empty corner is.
    shot = Shot(width=400, height=300, image_href="data:image/png;base64,AA==",
                anchors=[Anchor("button", Box(120, 40, 119, 39))])
    (cx, _), = _badges(annotate.render(shot, [Callout("button", "x")]))
    assert cx < 0 or cx > 400, "a label-filled control should be marked from a lane"


def test_a_leader_connects_a_mark_to_a_target_it_is_not_touching():
    shot = Shot(width=400, height=200, image_href="data:image/png;base64,AA==",
                anchors=[Anchor("a", Box(120, 40, 60, 20))])
    svg = annotate.render(shot, [Callout("a", "x")])
    assert 'class="ds-leader"' in svg, "a distant mark needs a line back to its target"


def test_marks_in_one_lane_never_overlap():
    # Three targets close together on the same side. Nudging along the lane is
    # allowed now — the leader keeps showing which row each mark belongs to —
    # but two marks may still never sit on top of each other.
    shot = Shot(width=400, height=300, image_href="data:image/png;base64,AA==", anchors=[
        Anchor("a", Box(300, 100, 90, 18)),
        Anchor("b", Box(300, 118, 90, 18)),
        Anchor("c", Box(300, 136, 90, 18)),
    ])
    marks = _badges(annotate.render(
        shot, [Callout("a", "1"), Callout("b", "2"), Callout("c", "3")]))
    assert len(marks) == 3
    ys = sorted(y for _x, y in marks)
    gaps = [b - a for a, b in zip(ys, ys[1:])]
    assert min(gaps) >= annotate.BADGE_R * 2, f"marks overlap in the lane: {ys}"


def test_an_anchor_outside_the_captured_area_fails_rather_than_being_dragged_back():
    # The table-settings popover scrolls, and COLUMN FORMATS sits below its
    # fold. Clamping produced a highlight pinned to the bottom edge marking a
    # control that is not in the picture — a confidently wrong figure.
    shot = Shot(width=356, height=550, image_href="data:image/png;base64,AA==",
                anchors=[Anchor("below_fold", Box(13, 596, 326, 21))])
    with pytest.raises(ValueError, match="outside the captured area"):
        annotate.render(shot, [Callout("below_fold", "formats")])


def test_a_box_that_merely_overflows_is_still_trimmed_and_kept():
    # The other half of the same rule: an AG Grid row is wider than the
    # screenshot but genuinely visible, so it is trimmed rather than rejected.
    shot = Shot(width=1280, height=720, image_href="data:image/png;base64,AA==",
                anchors=[Anchor("row", Box(320, 100, 2083, 25))])
    svg = annotate.render(shot, [Callout("row", "the row")])
    widths = [w for _x, _y, w, _h in _halos(svg)]
    assert widths and max(widths) <= 1280


def test_a_shot_survives_being_written_and_read():
    shot = Shot(width=3, height=4, title="t", surface="light",
                anchors=[Anchor("a", Box(1, 2, 3, 4)), Anchor("b", None, found=False, detail="why")])
    path = Path(__file__).parent / "_tmp_shot.json"
    try:
        shot.write(path)
        back = Shot.read(path)
        assert back.surface == "light" and back.title == "t"
        assert back.anchor("a").box == Box(1, 2, 3, 4)
        assert back.anchor("b").found is False and back.anchor("b").detail == "why"
    finally:
        path.unlink(missing_ok=True)


def test_loading_a_shot_inlines_its_image(tmp_path):
    png = tmp_path / "x.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "x.json").write_text(json.dumps({
        "width": 10, "height": 10, "image_href": "x.png", "anchors": [],
    }), encoding="utf-8")
    shot = model.load(tmp_path / "x.json")
    assert shot.image_href.startswith("data:image/png;base64,")


def test_a_shot_pointing_at_a_lost_image_says_so(tmp_path):
    (tmp_path / "x.json").write_text(json.dumps({
        "width": 10, "height": 10, "image_href": "missing.png", "anchors": [],
    }), encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="missing.png"):
        model.load(tmp_path / "x.json")
