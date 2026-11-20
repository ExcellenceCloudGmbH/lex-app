"""lex-app's grids and dashboard pages take the page's width, not their content's.

Intent: a dashboard's grid, tables and cards fill the page, as in lex-app. Reflex's ``rx.vstack``
aligns its children to the start by default, which shrinks each to its content's width; RFDS sizes
the grid's frame at 100% of its block, and the block has no width of its own -- so a grid inside
such a stack collapsed to its first column, and every record and table dashboard (and ``/``)
rendered in a narrow strip. Both stacks lex-app draws around project content now stretch it.
A regression is a grid that shows one column and no visible rows, or an Analytics-tab dashboard
squeezed to the width of its widest word.

Cluster 1cb -- scenario 1.746. Type: U.
Covers: lex/lex_app/reflex/data.py (lex_grid), lex/lex_app/reflex/dashboards.py (dashboard_page).
Run: python -m lex pytest lex/test_project/tests/init/test_1cb_reflex_full_width.py -v
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex.lex_app.reflex import dashboards, data

pytestmark = pytest.mark.init


class TestCluster01cb_FullWidth(SimpleTestCase):
    """1.746: the stacks lex-app draws around a grid and around a dashboard stretch their content."""

    # -- 1.746 ---------------------------------------------------------
    def test_1_746_grids_and_dashboards_fill_the_page_width(self) -> None:
        """
        Scenario 1.746: a grid and a dashboard page take the page's width.
        Given: lex_grid over a model, and the dashboard page lex-app builds for ``/`` and the
               Analytics tab.
        When: they are built.
        Then: the stack around the grid stretches its children (not Reflex's default "start"),
              and RFDS's grid frame inside it stays 100% wide -- together, the grid spans the
              page; the stack lex-app puts directly inside the shell's main region stretches
              too, so a project's page, tables and cards are as wide as the frame.
        """
        grid = data.lex_grid(Group)
        self.assertEqual(type(grid).__name__, "VStack")
        self.assertEqual(str(grid.align), '"stretch"', "a start-aligned stack collapses the grid to one column")
        rendered = str(grid)
        self.assertIn("rfds-grid-frame", rendered)
        self.assertRegex(rendered, r'"rfds-grid-frame",css:\(\{[^}]*\["width"\] : "100%"',
                         "RFDS's frame keeps its full width inside the block")

        page = str(dashboards.dashboard_page(None, models=lambda: {})())
        inside_main = re.search(r'ref:ref_main,tabIndex:-1\},jsx\(RadixThemesFlex,\{align:"([a-z]+)"', page)
        self.assertIsNotNone(inside_main, "the dashboard's stack sits directly in the shell's main region")
        self.assertEqual(inside_main.group(1), "stretch", "a start-aligned stack squeezes every dashboard")
