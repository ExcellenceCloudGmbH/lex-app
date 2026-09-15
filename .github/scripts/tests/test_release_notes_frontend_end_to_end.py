"""End-to-end: a release note that covers the frontend as well as the backend.

Every other test in this directory injects its inputs. This one does not: it
builds two real git repositories, runs the real digest/changelog code over
them, and asserts on the text a release would actually publish.

It exists because the unit tests all passed while the generated output was
wrong. Frontend provenance had never resolved in practice — every release was
a gap — so `changelog._line` had never once been called with a frontend change,
and nothing noticed that it rendered frontend shas under lex-app's URL.

Only the GitHub API is stubbed. Nothing else here is a fake.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import changelog, digest, facts, ranges  # noqa: E402
import release_notes.__main__ as main  # noqa: E402

LEX_REPO = "ExcellenceCloudGmbH/lex-app"
PAC_REPO = "ExcellenceCloudGmbH/process-admin-general-client"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _init(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    return repo


def _commit(repo: Path, subject: str, *, body: str = "", touch: str = "f.txt") -> str:
    target = repo / touch
    target.parent.mkdir(parents=True, exist_ok=True)
    # Append rather than overwrite so every commit is a distinct diff; an
    # identical tree makes `git commit` refuse without --allow-empty.
    with target.open("a", encoding="utf-8") as handle:
        handle.write(subject + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", subject, *(["-m", body] if body else []))
    return _git(repo, "rev-parse", "--short", "HEAD")


def _write_pin(repo: Path, version: str) -> None:
    (repo / "requirements.txt").write_text(
        f"django\ncelery\n{ranges.PIN_NAME}~={version}\n", encoding="utf-8"
    )


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A lex-app and a PAC, each with two releases and real commits between."""
    pac = _init(tmp_path / "pac")
    _commit(pac, "chore: scaffold")
    _git(pac, "tag", "v1.8.3")
    fe_first = _commit(
        pac, "feat: let a viewer filter foreign-key options",
        body="Picking a related record meant scrolling the whole list.",
    )
    fe_second = _commit(pac, "fix: stop unauthorised users editing SharePoint files")
    _commit(pac, "ci: bump the node version")          # excluded from the changelog
    _git(pac, "tag", "v1.9.0")

    lex = _init(tmp_path / "lex")
    _commit(lex, "chore: scaffold")
    _write_pin(lex, "1.8.3")
    _git(lex, "add", "-A")
    _git(lex, "commit", "-q", "-m", "build(deps): declare the frontend version")
    _git(lex, "tag", "v2.90.0")
    be_first = _commit(
        lex, "fix: derive the session key from DJANGO_SECRET_KEY",
        body="A worker with no key refused to boot.",
    )
    be_second = _commit(lex, "feat: export a calculation log as XLSX")
    _commit(lex, "docs: rewrite the upgrade guide")    # excluded from the changelog
    _write_pin(lex, "1.9.0")
    _git(lex, "add", "-A")
    _git(lex, "commit", "-q", "-m", "build(deps): take frontend 1.9.0")
    _git(lex, "tag", "v2.90.1")

    # `facts` binds REPO_ROOT at import (`from .ranges import REPO_ROOT`), so
    # patching ranges alone leaves it pointed at the real repository.
    for module in (ranges, digest, facts):
        monkeypatch.setattr(module, "REPO_ROOT", lex)
    # No network. Returning None is what a commit with no pull request looks
    # like, which is the honest shape for these fixtures.
    monkeypatch.setattr(digest, "_lookup_pr", lambda sha, **kw: None)

    return {
        "lex": lex, "pac": pac,
        "be": (be_first, be_second), "fe": (fe_first, fe_second),
    }


@pytest.fixture
def rendered(world):
    built = main._digest_for("v2.90.1", pac_checkout=world["pac"])
    section = changelog.render(built, date="2026-09-14", repo=LEX_REPO)
    return built, section


def test_the_range_is_read_from_the_pin_at_each_tag(world):
    assert ranges.frontend_range("v2.90.0", "v2.90.1") == ranges.Range(
        from_sha="v1.8.3", to_sha="v1.9.0"
    )


def test_the_digest_carries_both_components(rendered):
    built, _ = rendered
    components = {c["component"] for c in built["changes"]}
    assert components == {"backend", "frontend"}


def test_a_resolved_range_is_not_recorded_as_a_gap(rendered):
    built, section = rendered
    assert built["frontend_recorded"] is True
    assert changelog.GAP_MARKER not in section


def test_the_frontend_commit_count_reaches_the_drafter(rendered):
    built, _ = rendered
    # Three commits in v1.8.3..v1.9.0. The count is what lets the note say
    # "the interface did not change" instead of leaving a reader to guess, so
    # it counts everything in the range — not only the shippable subset.
    assert built["frontend_commits"] == 3


def test_both_components_appear_in_the_rendered_section(rendered):
    _, section = rendered
    assert "- **backend** " in section
    assert "- **frontend** " in section


def test_a_frontend_change_links_to_the_frontend_repository(world, rendered):
    _, section = rendered
    for sha in world["fe"]:
        assert f"https://github.com/{PAC_REPO}/commit/{sha}" in section
        assert f"https://github.com/{LEX_REPO}/commit/{sha}" not in section


def test_a_backend_change_links_to_lex_app(world, rendered):
    _, section = rendered
    for sha in world["be"]:
        assert f"https://github.com/{LEX_REPO}/commit/{sha}" in section
        assert f"https://github.com/{PAC_REPO}/commit/{sha}" not in section


def test_docs_and_ci_commits_are_left_out_of_both_halves(rendered):
    _, section = rendered
    assert "rewrite the upgrade guide" not in section
    assert "bump the node version" not in section


def test_a_frontend_commit_body_reaches_the_digest(rendered):
    """The drafter writes from bodies. A frontend body must survive the trip."""
    built, _ = rendered
    entry = next(c for c in built["changes"]
                 if c["component"] == "frontend" and "foreign-key" in c["subject"])
    assert "scrolling the whole list" in entry["detail"]


def test_without_a_pac_checkout_the_frontend_is_a_gap_not_a_silence(world):
    """An omitted checkout must never read as "the interface did not change"."""
    built = main._digest_for("v2.90.1", pac_checkout=None)
    section = changelog.render(built, date="2026-09-14", repo=LEX_REPO)
    assert built["frontend_recorded"] is False
    assert changelog.GAP_MARKER in section
    assert "- **frontend** " not in section


def test_a_missing_pin_is_reported_as_a_missing_pin(world, monkeypatch):
    """The operator has to be told which line to add, and where."""
    lex = world["lex"]
    _git(lex, "tag", "-d", "v2.90.0")
    _git(lex, "tag", "v2.90.0", _git(lex, "rev-list", "--max-parents=0", "HEAD"))

    reason = main._frontend_gap_reason("v2.90.0", "v2.90.1")
    assert ranges.PIN_NAME in reason
    assert ranges.REQUIREMENTS_PATH in reason
    assert "v2.90.0" in reason


def test_frontend_commits_are_looked_up_against_pac(world, monkeypatch):
    """Not against lex-app.

    This is the failure a passing test suite hides: `gh api repos/{owner}/{repo}`
    resolves the placeholder from its working directory, so asking lex-app about
    a PAC sha does not error — it answers about whichever lex-app commit the
    abbreviation happens to match, and the note gets a plausible, wrong title.
    """
    bound = []
    real = digest.pr_lookup_for

    def spy(checkout, **kwargs):
        bound.append((Path(checkout), kwargs.get("token")))
        return real(checkout, **kwargs)

    monkeypatch.setattr(digest, "pr_lookup_for", spy)
    monkeypatch.setenv(main.FRONTEND_TOKEN_ENV, "fe-t0ken")

    main._digest_for("v2.90.1", pac_checkout=world["pac"])

    assert bound == [(world["pac"], "fe-t0ken")]


def test_the_two_halves_are_tallied_separately(world, capsys):
    """One number for both halves hides a systemic frontend auth failure."""
    main._digest_for("v2.90.1", pac_checkout=world["pac"])
    err = capsys.readouterr().err
    assert "Backend PR enrichment:" in err
    assert "Frontend PR enrichment:" in err


def test_the_release_that_introduces_the_pin_is_a_gap(world, monkeypatch):
    """Even with a manifest at both ends that resolves perfectly well.

    This is the transition release, and it is the case a unit test missed: with
    `bundle` stubbed to None the fallback happened to fail, so the gap looked
    like the contract working. With a real manifest it does not fail — it
    answers, comparing two vendored bundles for a release whose frontend now
    comes from a pinned package instead.
    """
    lex = world["lex"]
    manifest = lex / ranges.MANIFEST_PATH
    manifest.parent.mkdir(parents=True, exist_ok=True)

    # v2.90.0 loses its pin but keeps a manifest, exactly as a pre-pin tag has.
    _git(lex, "checkout", "-q", "-B", "transition", "v2.90.0~1")
    manifest.write_text('{"sha": "%s"}' % ("a" * 40), encoding="utf-8")
    _git(lex, "add", "-A")
    _git(lex, "commit", "-q", "-m", "build(react): record the bundle")
    _git(lex, "tag", "-f", "v2.93.0")

    _write_pin(lex, "1.9.0")
    manifest.write_text('{"sha": "%s"}' % ("b" * 40), encoding="utf-8")
    _git(lex, "add", "-A")
    _git(lex, "commit", "-q", "-m", "build(deps): introduce the pin")
    _git(lex, "tag", "-f", "v2.93.1")

    # The manifest alone would resolve: two ends, two distinct shas.
    assert ranges.frontend_sha_at("v2.93.0") == "a" * 40
    assert ranges.frontend_sha_at("v2.93.1") == "b" * 40
    # It must still refuse, because the two ends measure different things.
    assert ranges.frontend_range("v2.93.0", "v2.93.1") is None

    built = main._digest_for("v2.93.1", pac_checkout=world["pac"])
    assert built["frontend_recorded"] is False
    assert changelog.GAP_MARKER in changelog.render(
        built, date="2026-09-14", repo=LEX_REPO
    )
