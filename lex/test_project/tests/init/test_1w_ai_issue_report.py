"""
Cluster 1w: `lex ai_issue_report` raw artifact bundle contract.

Intent
------

`lex ai_issue_report` must preserve Copilot/MCP support artifacts as raw bytes
without parser-side mutation, and it must fail in strict mode when no artifacts
could be captured.

Scenario numbering continues after 1v (1.176-1.178), so this batch starts at
1.179.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock
import zipfile

import pytest

from lex.tools.ai_issue_report import create_ai_issue_report

pytestmark = pytest.mark.init


class TestCluster01w_AiIssueReportRawArtifacts(TestCase):
    """Cluster 1w: keep raw artifact capture byte-exact and deterministic."""

    def test_1_179_off_mode_writes_manifest_and_empty_inventory(self):
        """Scenario 1.179: artifact-mode `off` emits a valid report shell.

        Given: report generation with artifact capture disabled
        When: the report is created
        Then: manifest/inventory are present and `copied_files` is zero
        """
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            out = project_root / "out.zip"

            result = create_ai_issue_report(
                project_root=project_root,
                output=out,
                artifact_mode="off",
            )

            self.assertEqual(result.copied_files, 0, "off mode must skip artifact capture")
            self.assertTrue(out.exists(), "report archive should be created")

            with zipfile.ZipFile(out, "r") as archive:
                names = set(archive.namelist())
                self.assertIn("manifest.json", names, "manifest must always exist")
                self.assertIn("inventory.json", names, "inventory must always exist")

                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                self.assertEqual(manifest["artifact_mode"], "off")
                self.assertEqual(manifest["copied_files"], 0)

                inventory = json.loads(archive.read("inventory.json").decode("utf-8"))
                self.assertEqual(inventory, [], "off mode inventory must be empty")

    def test_1_180_auto_mode_copies_raw_bytes_and_records_inventory(self):
        """Scenario 1.180: collected raw files are stored byte-for-byte.

        Given: a synthetic Copilot artifact source containing a file
        When: report generation runs in auto mode
        Then: the archive contains the exact file bytes and matching inventory row
        """
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            source_root = project_root / "artifacts"
            source_root.mkdir(parents=True, exist_ok=True)
            artifact_file = source_root / "session.jsonl"
            payload = b'{"event":"tool_result","ok":true}\n'
            artifact_file.write_bytes(payload)

            out = project_root / "report.zip"
            with mock.patch(
                "lex.tools.ai_issue_report._iter_copilot_raw_sources",
                return_value=[("synthetic_copilot", source_root)],
            ):
                result = create_ai_issue_report(
                    project_root=project_root,
                    output=out,
                    artifact_mode="auto",
                )

            self.assertEqual(result.copied_files, 1)
            self.assertEqual(result.missing_sources, ())
            self.assertEqual(result.collection_errors, ())

            with zipfile.ZipFile(out, "r") as archive:
                raw_member = "raw/synthetic_copilot/session.jsonl"
                self.assertIn(raw_member, archive.namelist())
                self.assertEqual(
                    archive.read(raw_member),
                    payload,
                    "raw artifact bytes must be preserved exactly",
                )
                inventory = json.loads(archive.read("inventory.json").decode("utf-8"))
                self.assertEqual(len(inventory), 1)
                self.assertEqual(inventory[0]["archive_path"], raw_member)
                self.assertEqual(inventory[0]["size_bytes"], len(payload))

    def test_1_181_strict_mode_fails_when_no_files_are_captured(self):
        """Scenario 1.181: strict mode enforces at least one captured artifact.

        Given: strict mode and no readable artifact source
        When: report generation finishes with zero captured files
        Then: it raises a runtime error to block incomplete support bundles
        """
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            out = project_root / "strict.zip"

            with mock.patch(
                "lex.tools.ai_issue_report._iter_copilot_raw_sources",
                return_value=[],
            ):
                with self.assertRaises(RuntimeError):
                    create_ai_issue_report(
                        project_root=project_root,
                        output=out,
                        artifact_mode="strict",
                    )
