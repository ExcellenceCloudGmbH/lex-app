# .github/scripts/test_plan_split.py
"""One-shot migration: split the test-plan monoliths into the sharded layout.

Design: docs/superpowers/specs/2026-07-07-test-plan-scalability-restructure-design.md §7.
Committed for audit; delete after the pointer stubs are retired.

Usage:
    python .github/scripts/test_plan_split.py --plan-dir lex/test_project/test-plan [--apply]

Without --apply it is a dry run: prints what it would write + the fact audit.
The fact audit HARD-FAILS the run if the {scenario-id, letter, BUG, date} sets
extracted from the old monoliths differ from the new tree.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

CLUSTER_SLUGS = {
    1: "init", 2: "crud_api", 3: "validation_hooks", 4: "permissions",
    5: "history", 6: "audit_logging", 7: "calculations", 8: "celery_async",
    9: "signals_ws", 10: "api_layer", 11: "stress", 12: "serializers",
    13: "exports", 14: "queries",
}
EXTRA_TEST_DIRS = {7: ["calculations", "calculation_logging"]}

CLUSTER_HEAD_RE = re.compile(r"^## (\d+)\.\s+(.*)$", re.M)
WP_CLUSTER_HEAD_RE = re.compile(r"^## Cluster (\d+)\s*[—-]?\s*(.*)$", re.M)
BATCH_HEAD_RE = re.compile(r"^### (?:Batch )?(\d+)([a-z])[.\s]*[—-]?\s*(.*)$", re.M)
SCENARIO_ID_RE = re.compile(r"\b(\d+)\.(\d+[a-z]?)\b")
SCENARIO_RANGE_RE = re.compile(r"(\d+)\.(\d+)\s*[–—-]\s*(?:\d+\.)?(\d+)")
LETTER_HEAD_RE = re.compile(r"^###+ .*?\b(\d+)([a-z])\b", re.M)
BUG_RE = re.compile(r"\bBUG-\d+\b")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
SESSION_ROW_RE = re.compile(
    r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(\d+)\s*\|(.*)\|([^|]*)\|([^|]*)\|([^|]*)\|\s*$",
    re.M,
)


def _split_at(regex: re.Pattern, text: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Return (preamble, [(number, title, body)]) cutting text at heading matches."""
    matches = list(regex.finditer(text))
    if not matches:
        return text, []
    preamble = text[: matches[0].start()]
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((int(m.group(1)), m.group(2).strip(), text[m.start():end].rstrip() + "\n"))
    return preamble, out


def split_clusters_md(text: str) -> tuple[str, dict[int, tuple[str, str]]]:
    preamble, parts = _split_at(CLUSTER_HEAD_RE, text)
    return preamble, {n: (title, body) for n, title, body in parts}


def split_writing_plan_md(text: str) -> tuple[str, dict[int, str]]:
    """Cluster blocks by number; everything else (preamble, conventions,
    LATER/pending/order/forecast/rules sections) is returned as misc."""
    matches = list(WP_CLUSTER_HEAD_RE.finditer(text))
    blocks: dict[int, str] = {}
    misc_parts: list[str] = []
    all_heads = sorted(
        [(m.start(), m) for m in matches]
        + [(m.start(), None) for m in re.finditer(r"^## \d+\.\s", text, re.M)],
        key=lambda t: t[0],
    )
    boundaries = [s for s, _ in all_heads] + [len(text)]
    head_at = {s: m for s, m in all_heads}
    if boundaries:
        misc_parts.append(text[: boundaries[0]])
    for i, start in enumerate(boundaries[:-1]):
        chunk = text[start : boundaries[i + 1]]
        m = head_at.get(start)
        if m is not None:
            blocks[int(m.group(1))] = chunk.rstrip() + "\n"
        else:
            misc_parts.append(chunk)
    return "".join(misc_parts), blocks


def split_sessions_md(text: str) -> list[dict]:
    frags = []
    for m in SESSION_ROW_RE.finditer(text):
        date, session, prose, clusters, added, tally = (g.strip() for g in m.groups())
        frags.append(
            {
                "date": date,
                "session": int(session),
                "prose": prose,
                "clusters": clusters,
                "tests_added": added,
                "suite_tally": tally,
            }
        )
    return frags


def seed_allocation(number: int, slug: str, title: str, sources: str) -> dict:
    """Best-effort allocation.yaml seed for one cluster from any concatenation of
    that cluster's plan prose (batches block + cluster section). Counts are
    zeroed with a 'seeded' note — Task 4's review step fills them from the
    pre-migration dashboard."""
    letters: dict[str, dict] = {}
    for m in BATCH_HEAD_RE.finditer(sources):
        if int(m.group(1)) != number:
            continue
        letter, rest = m.group(2), m.group(3)
        done = "✅" in rest or "✅" in sources[m.start(): m.start() + 200]
        letters.setdefault(
            letter,
            {
                "title": rest.replace("✅", "").strip(" —-"),
                "scenarios": "",
                "status": "complete" if done else "planned",
                "tests": {"pass": 0, "skip": 0, "xfail": 0},
                "note": "seeded by test_plan_split.py — verify",
            },
        )
    max_scenario = 0
    for m in SCENARIO_ID_RE.finditer(sources):
        if int(m.group(1)) == number:
            max_scenario = max(max_scenario, int(re.match(r"\d+", m.group(2)).group()))
    for m in SCENARIO_RANGE_RE.finditer(sources):
        if int(m.group(1)) == number:
            max_scenario = max(max_scenario, int(m.group(3)))
    return {
        "cluster": number,
        "slug": slug,
        "title": title,
        **({"test_dirs": EXTRA_TEST_DIRS[number]} if number in EXTRA_TEST_DIRS else {}),
        "max_scenario": max_scenario,
        "letters": dict(sorted(letters.items())),
    }


@dataclass
class Facts:
    scenario_ids: set = field(default_factory=set)  # {(cluster, sid)}
    letters: set = field(default_factory=set)       # {(cluster, letter)}
    bugs: set = field(default_factory=set)
    dates: set = field(default_factory=set)


def extract_facts(text: str) -> Facts:
    f = Facts()
    for m in SCENARIO_ID_RE.finditer(text):
        f.scenario_ids.add((m.group(1), m.group(2)))
    for m in LETTER_HEAD_RE.finditer(text):
        f.letters.add((m.group(1), m.group(2)))
    f.bugs = set(BUG_RE.findall(text))
    f.dates = set(DATE_RE.findall(text))
    return f


def _yaml_dump(d: dict) -> str:
    import yaml
    return yaml.safe_dump(d, sort_keys=False, allow_unicode=True, width=100)


def run(plan_dir: Path, apply: bool) -> int:
    clusters_md = (plan_dir / "test-clusters.md").read_text()
    writing_md = (plan_dir / "test-writing-plan.md").read_text()
    sessions_md = (plan_dir / "progress" / "session-log.md").read_text()

    preamble, cluster_sections = split_clusters_md(clusters_md)
    wp_misc, wp_blocks = split_writing_plan_md(writing_md)
    fragments = split_sessions_md(sessions_md)

    old_facts = extract_facts(clusters_md + writing_md + sessions_md)

    planned: dict[Path, str] = {}
    planned[plan_dir / "testing-philosophy.md"] = (
        "# Testing Philosophy\n\n"
        + preamble.split("\n", 1)[1].lstrip()  # drop the old H1
    )
    planned[plan_dir / "clusters" / "README.md"] = (
        "# Cluster Allocation — Conventions, Backlog, Pending Decisions\n\n"
        "> Absorbed from `test-writing-plan.md` (retired). Per-cluster batch\n"
        "> history lives in each `NN-<slug>/batches.md`.\n\n" + wp_misc.strip() + "\n"
    )
    for num, (title, body) in cluster_sections.items():
        slug = CLUSTER_SLUGS.get(num)
        if slug is None:
            print(f"WARNING: cluster {num} has no known slug — review manually")
            continue
        d = plan_dir / "clusters" / f"{num:02d}-{slug}"
        planned[d / "cluster.md"] = body
        batches = wp_blocks.get(num, f"## Cluster {num} — {title}\n\n(no batches recorded yet)\n")
        planned[d / "batches.md"] = batches
        planned[d / "allocation.yaml"] = _yaml_dump(
            seed_allocation(num, slug, title, body + "\n" + batches)
        )
    for frag in fragments:
        name = f"{frag['date']}-s{frag['session']:03d}.md"
        planned[plan_dir / "progress" / "sessions" / name] = (
            "---\n"
            f"date: {frag['date']}\n"
            f"clusters: [{frag['clusters']}]\n"
            f"tests_added: \"{frag['tests_added']}\"\n"
            f"suite_tally: \"{frag['suite_tally']}\"\n"
            "---\n\n"
            f"(migrated session {frag['session']})\n\n{frag['prose']}\n"
        )
    # Preserve the old dashboard for the Task-4 review, then it is deleted.
    planned[plan_dir / "progress" / "dashboard-pre-migration.md"] = (
        plan_dir / "progress" / "dashboard.md"
    ).read_text()

    new_facts = extract_facts("".join(planned.values()))
    missing = Facts(
        scenario_ids=old_facts.scenario_ids - new_facts.scenario_ids,
        letters=old_facts.letters - new_facts.letters,
        bugs=old_facts.bugs - new_facts.bugs,
        dates=old_facts.dates - new_facts.dates,
    )
    lost = any((missing.scenario_ids, missing.letters, missing.bugs, missing.dates))
    print(f"planned files: {len(planned)}")
    if lost:
        print("FACT AUDIT FAILED — facts present in old files but not the new tree:")
        for label, vals in (
            ("scenario ids", missing.scenario_ids), ("letters", missing.letters),
            ("bugs", missing.bugs), ("dates", missing.dates),
        ):
            if vals:
                print(f"  {label}: {sorted(vals)[:40]}")
        return 1
    print("fact audit: OK (scenario ids, letters, bugs, dates all preserved)")
    if apply:
        for path, text in planned.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        print("applied.")
    else:
        print("dry run — re-run with --apply to write.")
    return 0


def _cli() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan-dir", type=Path, default=Path("lex/test_project/test-plan"))
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    return run(a.plan_dir, a.apply)


if __name__ == "__main__":
    sys.exit(_cli())
