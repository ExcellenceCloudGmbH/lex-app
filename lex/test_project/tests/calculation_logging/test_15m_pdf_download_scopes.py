"""Cluster 15m — the calculation-log PDF can cover one step, a branch, or the whole run.

Intent (customer request LEX-1554, 2026-11-06): "Currently, the calculation
tree can only be downloaded one part at a time, but I would like to download
the whole tree in one step" -- and, for a node such as "Health Check", that
node "with all child nodes" in the same PDF, from both download buttons.

``GET api/download-pdf/<pk>/`` answers three questions, chosen by ``?scope=``:

- ``node`` (default): that log alone -- unchanged for every existing caller.
- ``subtree``: that log and everything under it (``?include_descendants=true``
  is the older spelling that released frontends still send).
- ``calculation``: every log of the calculation the log belongs to -- exactly
  the rows the Execution tree shows, whichever node the reader had selected,
  including a calculation with more than one root.

Golden Rule: we assert what a reader finds in the file (whose logs are in it,
whose are not, how it is sectioned) and what the audit trail records -- not
how the rows were gathered.

Cluster 15m — scenarios 15.56–15.62. Type: E.
Covers: lex/api/views/calculations/DownloadMarkdownPdf.py
Run: python -m lex pytest lex/test_project/tests/calculation_logging/test_15m_pdf_download_scopes.py -v
"""
from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from lex.api.views.calculations import DownloadMarkdownPdf as pdf_view
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.models.CalculationLog import CalculationLog

from . import _CalcLogTestCase

pytestmark = pytest.mark.calculation_logging


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Concatenated text of every page (pypdf)."""
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return "".join(page.extract_text() for page in reader.pages)


class TestCluster15m_PdfDownloadScopes(_CalcLogTestCase):
    """GET api/download-pdf/<pk>/?scope=node|subtree|calculation."""

    def setUp(self):
        super().setUp()
        # These logs belong to no record, so only an operator may read them
        # (LEX_SEC_INTERNAL) -- the same footing as batch 15l.
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])

    # -- helpers --------------------------------------------------------

    def _log(self, body, *, parent=None, heading=None, calc_id=None):
        return CalculationLog.objects.create(
            calculationId=calc_id or self.calc_id,
            calculation_log=body,
            parent_log=parent,
            heading=heading,
        )

    def _get(self, pk, **params):
        return self.client.get(self._url("download-markdown-pdf", pk=pk), params)

    def _pdf_text(self, pk, **params) -> str:
        response = self._get(pk, **params)
        self.assertEqual(
            response.status_code, 200,
            f"PDF download with {params!r} must succeed; got {response.status_code}.",
        )
        self.assertTrue(response.content.startswith(b"%PDF"), "Response is not a PDF.")
        return _extract_pdf_text(response.content)

    def _download_audit(self):
        rows = AuditLog.objects.filter(resource="calculationlog", action="download")
        self.assertEqual(
            rows.count(), 1,
            f"One download must write exactly one audit row; got {rows.count()}.",
        )
        return rows.first()

    def _health_check_tree(self):
        """The shape from the customer's screenshot: a consolidated root,
        a "Health Check" branch with units under it, and a sibling branch."""
        root = self._log("Consolidated body.")
        health = self._log("Health body.", parent=root, heading="Health Check")
        ltip = self._log("LTIP body.", parent=health, heading="PE LTIP 2")
        year = self._log("Year body.", parent=ltip, heading="2017")
        details = self._log("Details body.", parent=root, heading="Calculation details")
        return root, health, ltip, year, details

    # -- 15.56 ---------------------------------------------------------
    def test_15_56_whole_calculation_from_any_node(self) -> None:
        """
        Scenario 15.56: the whole tree downloads in one step, from any node.

        Given: a calculation tree (root → Health Check → PE LTIP 2 → 2017,
               plus a sibling "Calculation details" branch).
        When: the PDF is requested for the deepest leaf with
              ``?scope=calculation``.
        Then: one PDF holds every log of the tree -- the root, both
              branches and every level below them -- each section under its
              title, named ``document_<pk>_calculation.pdf``.
        """
        root, health, ltip, year, details = self._health_check_tree()

        response = self._get(year.pk, scope="calculation")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f"document_{year.pk}_calculation.pdf", response["Content-Disposition"],
            "A whole-calculation file must be named apart from a single-step one.",
        )
        text = _extract_pdf_text(response.content)

        for expected in (
            "Consolidated body.",
            "Health Check", "Health body.",
            "PE LTIP 2", "LTIP body.",
            "2017", "Year body.",
            "Calculation details", "Details body.",
        ):
            self.assertIn(
                expected, text,
                f"{expected!r} missing: the whole-calculation PDF must hold every "
                f"log of the tree, not only the selected node's branch.",
            )
        self.assertLess(
            text.index("Health body."), text.index("LTIP body."),
            "Sections must read in tree order: a parent before its children.",
        )
        self.assertLess(
            text.index("Year body."), text.index("Details body."),
            "Sections must read in tree order: a whole branch before its sibling.",
        )

    # -- 15.57 ---------------------------------------------------------
    def test_15_57_every_root_is_included_and_titled_other_runs_are_not(self) -> None:
        """
        Scenario 15.57: "the whole calculation" is what the Execution tree
        shows -- every root of that calculation, and nothing from any other.

        Given: one calculation with two root logs (each with a child), and a
               second calculation's log.
        When: ``?scope=calculation`` is requested from the second root's child.
        Then: both roots and their children are in the PDF, each root under
              its own title so the reader can tell the runs apart, and the
              other calculation's log is absent.
        """
        first = self._log("First root body.", heading="Run A")
        self._log("First child body.", parent=first, heading="A step")
        second = self._log("Second root body.", heading="Run B")
        second_child = self._log("Second child body.", parent=second, heading="B step")
        self._log("Foreign body.", heading="Other run", calc_id=f"{self.calc_id}_other")

        text = self._pdf_text(second_child.pk, scope="calculation")

        for expected in (
            "Run A", "First root body.", "First child body.",
            "Run B", "Second root body.", "Second child body.",
        ):
            self.assertIn(
                expected, text,
                f"{expected!r} missing: a calculation with two roots must export "
                f"both, each titled.",
            )
        self.assertNotIn(
            "Foreign body.", text,
            "A log of a different calculation must never be swept into the PDF.",
        )

    # -- 15.58 ---------------------------------------------------------
    def test_15_58_subtree_scope_matches_the_older_flag(self) -> None:
        """
        Scenario 15.58: ``scope=subtree`` is "this node and everything below
        it" -- the same document ``include_descendants=true`` produces.

        Given: the Health Check tree.
        When: the "Health Check" node is downloaded with ``?scope=subtree``,
              and again with ``?include_descendants=true``.
        Then: both PDFs hold Health Check and all of its descendants, neither
              holds the root's or the sibling branch's log, and both are
              named ``document_<pk>_full.pdf``.
        """
        root, health, ltip, year, details = self._health_check_tree()

        for params in ({"scope": "subtree"}, {"include_descendants": "true"}):
            response = self._get(health.pk, **params)
            self.assertEqual(response.status_code, 200)
            self.assertIn(f"document_{health.pk}_full.pdf", response["Content-Disposition"])
            text = _extract_pdf_text(response.content)
            for expected in ("Health body.", "PE LTIP 2", "LTIP body.", "2017", "Year body."):
                self.assertIn(
                    expected, text,
                    f"{expected!r} missing from the {params!r} subtree PDF.",
                )
            for absent in ("Consolidated body.", "Details body."):
                self.assertNotIn(
                    absent, text,
                    f"{absent!r} is outside the Health Check branch but appeared "
                    f"in the {params!r} subtree PDF.",
                )

    # -- 15.59 ---------------------------------------------------------
    def test_15_59_scope_wins_over_the_older_flag(self) -> None:
        """
        Scenario 15.59: an explicit ``scope`` decides, whatever else is sent.

        Given: a parent with one child.
        When: ``?scope=node&include_descendants=true`` is requested.
        Then: only the parent's own log is in the PDF -- ``scope`` is the
              newer, explicit question and must not be overridden.
        """
        parent = self._log("Parent body.")
        self._log("Child body.", parent=parent, heading="Child step")

        text = self._pdf_text(parent.pk, scope="node", include_descendants="true")

        self.assertIn("Parent body.", text)
        self.assertNotIn(
            "Child body.", text,
            "scope=node asked for one log; include_descendants must not widen it.",
        )

    # -- 15.60 ---------------------------------------------------------
    def test_15_60_unknown_scope_is_refused_and_audited(self) -> None:
        """
        Scenario 15.60: an unknown scope is a 400, never a quiet single log.

        Given: a log.
        When: ``?scope=everything`` is requested.
        Then: the response is 400 naming the valid scopes, and the attempt is
              audited as a ``failure`` recording the scope that was asked
              for -- a reader who asked for more and silently received one
              step could not tell from the file.
        """
        log = self._log("Body.")

        response = self._get(log.pk, scope="everything")

        self.assertEqual(
            response.status_code, 400,
            f"An unknown scope must be rejected; got {response.status_code}.",
        )
        self.assertIn("calculation", response.content.decode(), "The 400 must name the valid scopes.")
        row = self._download_audit()
        self.assertEqual(
            AuditLogStatus.objects.filter(audit_log=row).first().status, "failure",
            "A refused download must be audited as a failure.",
        )
        self.assertEqual(row.payload.get("scope"), "everything")
        self.assertIs(
            row.payload.get("include_descendants"), False,
            "Nothing beyond one log was granted, so the audit must not claim "
            "descendants were included.",
        )

    # -- 15.61 ---------------------------------------------------------
    def test_15_61_whole_calculation_download_is_audited_with_its_scope(self) -> None:
        """
        Scenario 15.61: the audit trail tells a whole-calculation download
        apart from a single step or a branch.

        Given: the five-log Health Check tree.
        When: ``?scope=calculation`` is downloaded from the "Health Check" node.
        Then: one ``success`` audit row against that node records
              ``scope: calculation``, that descendants were included, all
              five sections, no truncation, and the file's name and size.
        """
        root, health, ltip, year, details = self._health_check_tree()

        response = self._get(health.pk, scope="calculation")
        self.assertEqual(response.status_code, 200)

        row = self._download_audit()
        self.assertEqual(AuditLogStatus.objects.filter(audit_log=row).first().status, "success")
        self.assertEqual(row.object_id, health.pk, "The entry must name the node it was asked for.")
        self.assertEqual(row.payload.get("scope"), "calculation")
        self.assertIs(row.payload.get("include_descendants"), True)
        self.assertEqual(
            row.payload.get("section_count"), 5,
            f"All five logs of the calculation were rendered; the audit said "
            f"{row.payload.get('section_count')!r}.",
        )
        self.assertIs(row.payload.get("truncated"), False)
        self.assertEqual(row.payload.get("filename"), f"document_{health.pk}_calculation.pdf")
        self.assertGreater(row.payload.get("size_bytes") or 0, 0)

    # -- 15.62 ---------------------------------------------------------
    def test_15_62_an_oversized_calculation_says_it_was_cut(self) -> None:
        """
        Scenario 15.62: a calculation larger than the cap is cut visibly.

        Given: the section cap lowered to 3 and a five-log calculation.
        When: ``?scope=calculation`` is downloaded.
        Then: the PDF still renders, says that it stops at the cap, and the
              audit records ``truncated: true`` -- a silently cut log reads
              exactly like a calculation that stopped early.
        """
        root, health, ltip, year, details = self._health_check_tree()

        with patch.object(pdf_view, "MAX_SUBTREE_NODES", 3):
            text = self._pdf_text(root.pk, scope="calculation")

        self.assertIn("Consolidated body.", text)
        self.assertIn(
            "stops at 3 sections", text,
            "A truncated whole-calculation PDF must say where and why it stops.",
        )
        self.assertNotIn(
            "Details body.", text,
            "The cap allowed three sections; the fifth log must not be rendered.",
        )
        row = self._download_audit()
        self.assertIs(row.payload.get("truncated"), True)
        self.assertEqual(row.payload.get("section_count"), 3)
