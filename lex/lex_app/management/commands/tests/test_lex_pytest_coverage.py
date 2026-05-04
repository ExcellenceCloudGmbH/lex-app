from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from lex.tools.test_groups import (
    _build_delivery_template_context,
    GroupConfig,
    GroupResult,
    LexGroupsPlugin,
    LexTestConfig,
)


class _FakeCoverage:
    def __init__(self):
        self.contexts = []

    def switch_context(self, context):
        self.contexts.append(context)


def _marker(name):
    return SimpleNamespace(name=name)


def _item(nodeid, marker_name):
    return SimpleNamespace(
        nodeid=nodeid,
        name=nodeid.rsplit("::", 1)[-1],
        location=(nodeid.split("::", 1)[0], 0, nodeid.rsplit("::", 1)[-1]),
        iter_markers=lambda: [_marker(marker_name)],
    )


def _config():
    return LexTestConfig(
        project_root=Path.cwd(),
        source="test",
        tests_entrypoint="Tests",
        report_output_dir="reports",
        global_receivers=[],
        email={},
        groups=[
            GroupConfig(
                name="alpha",
                description="Alpha group",
                receivers=[],
            )
        ],
        group_assignments={},
    )


class LexPytestCoverageTests(TestCase):
    def test_switches_coverage_context_per_test_nodeid(self):
        plugin = LexGroupsPlugin(_config())
        coverage = _FakeCoverage()
        plugin.set_coverage_runner(coverage)
        item = _item("Tests/test_alpha.py::test_one", "alpha")

        plugin.pytest_collection_modifyitems(None, [item])
        plugin._counted["alpha"].add(item.nodeid)
        plugin.pytest_runtest_setup(item)
        plugin.pytest_runtest_logfinish(item.nodeid, item.location)

        self.assertEqual(
            coverage.contexts,
            [plugin.coverage_context_for_nodeid(item.nodeid), ""],
        )
        self.assertEqual(
            plugin.coverage_contexts_for_group("alpha"),
            [plugin.coverage_context_for_nodeid(item.nodeid)],
        )

    def test_delivery_template_context_exposes_group_coverage(self):
        config = _config()
        result = GroupResult(name="alpha", description="Alpha group", passed=1)
        result.test_count = 1

        context = _build_delivery_template_context(
            config=config,
            group_results=[result],
            pytest_exit_code=0,
            recipient_name=None,
            recipient_email=None,
            report_title="Report",
            subject_context="alpha",
            headline=None,
            intro=None,
            outro=None,
            coverage_summary={
                "label": "Framework-wide code coverage",
                "display": "55.5%",
                "percentage": 55.5,
                "groups": {
                    "alpha": {
                        "label": "Group code coverage",
                        "display": "12.3%",
                        "percentage": 12.3,
                    }
                },
            },
            run_duration="1.0 s",
        )

        self.assertTrue(context["has_group_coverage"])
        self.assertEqual(context["coverage"]["display"], "55.5%")
        self.assertEqual(context["groups"][0]["coverage"]["display"], "12.3%")
