# CI / CD overview — `lex-app`

> **Audience:** DevOps engineer reviewing the pipeline for improvements.
> **Last verified against `.github/workflows/`:** April 28, 2026, branch `lex-app-v2`.

This document describes what is **actually in the repository today**. Anything in `CLAUDE.md` or older docs that contradicts this is out of date.

---

## 1. The eight workflows at a glance

| File | Trigger | Role | Reusable? |
|---|---|---|---|
| `showcase_tests.yml` | `workflow_dispatch`, `workflow_call` | Run the test suite (cluster-grouped), produce coverage XML, build Platform Health Report (HTML + PDF), email it via SendGrid (always — pass or fail), final gate on cluster outcomes | ✅ called by `pip_publish.yml` |
| `celery_redis_broker_example.yml` | `workflow_dispatch`, `workflow_call` | Real Redis broker release gate: PostgreSQL + Redis service, runs Cluster 8k's producer → worker → result-backend examples including `CalculationModel` + `WaitForTasks` | ✅ called by `pip_publish.yml` |
| `pip_publish.yml` | `release: created`, `workflow_dispatch` | Release entry point. Fires on draft save (or direct publish), calls both reusable gates (`showcase_tests.yml` + `celery_redis_broker_example.yml`), then writes `lex/_version.py` from the tag and pushes to PyPI | ❌ |
| `custom-image.yml` | `workflow_run` of "Publish to PyPI" (success), `workflow_dispatch` | Build + push Docker image tagged from the release | ❌ |
| `frontend_build.yml` | `release: published`, `workflow_dispatch` | Pull frontend repo, Vitest + Playwright, `yarn build`, commit bundle into `lex/react/build/` | ❌ |
| `update_docs.yml` | `workflow_run` of "Publish to PyPI" (success), `workflow_dispatch` | Pure dispatcher — fires `repository_dispatch` (`event_type: release-published`) at `lex-app-docs` using a GitHub App token | ❌ |
| `scancode-toolkit.yml` | weekly cron (Sun 23:00 UTC), `workflow_dispatch` | License scan (informational, no gate) | ❌ |
| `vulnerability-scan.yml` | weekly cron (Sun 23:00 UTC), `workflow_dispatch` | `bandit` Python security scan (informational, no gate) | ❌ |

**Default branch:** `lex-app-v2`. `workflow_run` triggers only fire when the parent workflow file lives on the default branch — that's why it was switched off `main` (which still carries v1).

---

## 2. The release pipeline — what happens when you publish a GitHub release

A maintainer can either **save a draft release** or publish one directly — either action fires `release: created`, which is the new trigger for `pip_publish.yml`. The draft path means the publish to PyPI happens without a separate manual "Publish release" click.

```
GitHub release created (draft saved OR direct publish, tag vX.Y.Z)
       │
       ├──────────────► pip_publish.yml          (release: created)
       │                  ├─ gate-release ──► showcase_tests.yml (workflow_call)
       │                  │                     ├─ install deps
       │                  │                     ├─ run_showcase_suite.py --only <clusters>
       │                  │                     ├─ build manifest.json + report.html + report.pdf
       │                  │                     ├─ ALWAYS email via SendGrid (pass or fail)
       │                  │                     └─ exit non-zero if any cluster failed
       │                  ├─ gate-celery-broker ──► celery_redis_broker_example.yml (workflow_call)
       │                  │                         ├─ PostgreSQL service
       │                  │                         ├─ Redis service
       │                  │                         └─ lex test Cluster 8k (8.45 + 8.46)
       │                  └─ publish (needs BOTH gates)
       │                     ├─ resolve VERSION = ${TAG#v}
       │                     ├─ write lex/_version.py
       │                     ├─ python -m build → twine check
       │                     └─ pypa/gh-action-pypi-publish (PYPI_API_TOKEN)
       │
       └──────────────► frontend_build.yml       (release: published — runs IN PARALLEL with pip_publish)

After "Publish to PyPI" completes successfully, two workflow_run consumers fire:

   ─► custom-image.yml      (build + push Docker image)
   ─► update_docs.yml       (repository_dispatch → lex-app-docs)
                              │
                              └──► lex-app-docs receiver workflow
                                     ├─ compute commit/code diff (truncated to ~45k chars)
                                     ├─ create issue with diff + writing-style prompt
                                     └─ assign @copilot-swe-agent[bot] (COPILOT_PAT)
                                          │
                                          └──► Copilot agent opens a docs PR
```

`pip_publish.yml` now fires on `release: created` (draft save or direct publish), while `frontend_build.yml` still fires on `release: published`. So a draft save publishes to PyPI but does **not** rebuild the frontend bundle — the frontend rebuild waits for the maintainer to actually publish the release. If you want the bundle to track the PyPI release exactly, either move `frontend_build.yml` to `release: created` (will then race `pip_publish` again, as before) or chain it on `workflow_run` of "Publish to PyPI" the way `custom-image.yml` does. **`frontend_build` is not gated on the backend test suite** despite the comment at the top of the file claiming otherwise — `django_tests.yml` was absorbed into `showcase_tests.yml` on 22 April 2026 and the corresponding `gate-tests` job was removed but the header comment was not updated. **This is a known drift.**

---

## 3. The test gate — `showcase_tests.yml` in detail

This is the main functional release gate. The PyPI release also waits for `celery_redis_broker_example.yml`, which owns the real Redis broker boundary.

### Inputs

* `clusters` (string) — comma-separated selector consumed by `.github/scripts/run_showcase_suite.py --only`. Each entry is either `<cluster_key>` (whole cluster) or `<cluster_key>:<test_suffix>` (single test).
* Default selector: `init,crud_api,api_layer,calculations,validation_hooks,permissions,queries` — seven clusters considered release-blessed.
* The job **refuses to run** if `clusters` is empty. There is no implicit "run everything".

### Why release events are **not** a direct trigger

A `release` event (any subtype, including `created`) cannot supply `inputs.*`, so a direct trigger would land on the empty-input guard and fail. The only entry points are `workflow_dispatch` (manual) and `workflow_call` (from `pip_publish.yml`).

### Services + environment

* Postgres `:latest` service container (`POSTGRES_USER=django`, `POSTGRES_DB=db_lex`).
* Python 3.12 matrix (single entry).
* System deps: `libcairo2-dev pkg-config libpango-1.0-0 libpangoft2-1.0-0` for the WeasyPrint PDF writer (this was the `pycairo` build failure we fixed earlier).
* Pip cache keyed on `requirements.txt` hash.
* Django env: `DJANGO_SETTINGS_MODULE=lex_app.settings`, `DATABASE_DEPLOYMENT_TARGET=default`, `CELERY_ACTIVE=False`.
* Optional Keycloak secrets exported to env. If any of `KEYCLOAK_URL` / `KEYCLOAK_REALM` / `OIDC_RP_CLIENT_ID` / `OIDC_RP_CLIENT_SECRET` is missing the live integration tests skip cleanly. `LEX_RUN_KEYCLOAK_DESTRUCTIVE` is a **repo variable** (not a secret), defaulting to `0`.

### Steps (in order)

1. Checkout, set up Python, restore + save pip cache.
2. Install lex-app (`pip install -e .`) plus the report toolchain (`coverage`, `weasyprint`, `cairosvg`, `sendgrid`).
3. **Run the cluster suite** — `run_showcase_suite.py --only "$SHOWCASE_CLUSTERS" --out manifest.json`. `continue-on-error: true` so the email step always reaches.
4. **Coverage report (informational)** — `coverage report` to logs, `coverage xml -o coverage.xml`. **No coverage threshold gate** — showcase pass/fail is the functional gate, and `pip_publish.yml` also waits for the Celery Redis broker gate.
5. Upload `coverage.xml` as artefact `backend-coverage-report`.
6. **Parse manifest** — sets job outputs `overall_ok` (`true`/`false`) and `clusters_passing` (e.g. `5/5`) by reading `manifest.json`.
7. Build the Platform Health Report — `build_showcase_report.py` produces `report.html`, `report.pdf`, `logo.png`.
8. Upload report artefact `platform-health-report`.
9. **ALWAYS send the email** — `send_showcase_email.py` with SendGrid. `if: always()`, runs even if step 3 failed.
10. **Final gate** — fail the job if `overall_ok != 'true'`. This runs **after** the email step on purpose, so the email always fires.

### Job outputs

```yaml
outputs:
  overall_ok:        "true" | "false"
  clusters_passing:  "5/5"
```

These are consumed by `pip_publish.yml`'s `gate-release` job. The `publish` job's `needs: [gate-release, gate-celery-broker]` does the actual blocking.

---

## 4. PyPI publish — version derivation

The version that ships to PyPI is **derived from the GitHub release tag** at build time, not stored in `pyproject.toml`.

```
pyproject.toml:
    [project]
    dynamic = ["version", "dependencies"]

    [tool.setuptools.dynamic]
    version = { attr = "lex._version.__version__" }
```

`lex/_version.py` is a placeholder (`__version__ = "0.0.0.dev0"`) committed to the repo. In the `publish` job we overwrite it:

```bash
VERSION="${GITHUB_REF_NAME#v}"          # v2.0.0rc124 → 2.0.0rc124
echo "__version__ = \"${VERSION}\"" > lex/_version.py
python -m build
twine check dist/*
pypa/gh-action-pypi-publish (password: PYPI_API_TOKEN)
```

We tried OIDC Trusted Publishing first; it failed with `invalid-publisher` because the trusted publisher was never configured on PyPI. Switching to a token was the deliberate fallback.

---

## 5. Docker image build (`custom-image.yml`)

* Triggered by `workflow_run` of "Publish to PyPI" with `conclusion == 'success'`, plus a `workflow_dispatch` escape hatch with a required `tag` input.
* `workflow_run` events do **not** carry `github.event.release.tag_name`, so a "Determine release tag" step resolves it via `git describe --tags --abbrev=0 HEAD` (or the dispatch input).
* The Dockerfile does `pip install --no-cache-dir "lex-app==${IMAGE_VERSION}"` — that's why this stage *must* run after PyPI publish, otherwise the install would race the upload.

### ⚠️ Known bug, deliberately left in place

`build/Dockerfile` line ~83 still passes the tag with the leading `v` (`lex-app==v2.0.0rcXXX`), which PyPI rejects. The fix is one line — strip `v` before `pip install` or pass an already-stripped build arg. Tracked, not fixed this round per a prior priority call. **First thing to flag with the engineer.**

---

## 6. Documentation pipeline (`update_docs.yml` + receiver + auto-close in `lex-app-docs`)

This is a two-repo, three-workflow design. `lex-app` dispatches; `lex-app-docs` receives, creates the Copilot issue, and (separately) cleans up empty Copilot PRs so the maintainer queue doesn't accumulate no-op PRs from test-only releases.

### `lex-app/update_docs.yml` — pure dispatcher

Triggers on `workflow_run` of "Publish to PyPI" (success), or manual dispatch with `base_ref` / `head_ref` inputs.

Steps:
1. Checkout `lex-app` with `fetch-depth: 0` (need full git history for tags).
2. Resolve `head_tag` / `base_tag` either from inputs or `git describe --tags --abbrev=0 HEAD` and `HEAD^`.
3. **Generate a GitHub App token** scoped to `lex-app-docs` only:
   ```yaml
   uses: actions/create-github-app-token@v1
   with:
     app-id:        ${{ secrets.DOCS_APP_ID }}
     private-key:   ${{ secrets.DOCS_APP_PRIVATE_KEY }}
     repositories:  "lex-app-docs"
     owner:         ExcellenceCloudGmbH
   ```
4. `gh api repos/.../dispatches` with `event_type: release-published` and a `client_payload` carrying `head_tag` / `base_tag` / `source_repo`.

That's the entire `lex-app` side. No diffs computed here, no LLM call here.

### `lex-app-docs` — receiver workflow

Reference copy at `docs/ci-cd/docs-receiver-workflow.yml` in this repo; the live file lives in `lex-app-docs/.github/workflows/`.

1. `on: repository_dispatch: types: [release-published]`.
2. Resolve tags from payload, or fall back to `git describe` for manual dispatch.
3. Compute the diff: commit log + code diff. **Two-stage truncation**: shell-side `head -800` on the code diff, then a JS-side `MAX_DIFF = 45000` character cap to stay below GitHub's 65,536-character issue-body limit.
4. Create an issue with the diff and a tuned prompt (the "Writing style" section forces conversational tone, draws a line between feature docs and reference docs, blocks the agent from exposing class names like `ContextResolver` or `LIFO stack`).
5. Assign the issue to **`copilot-swe-agent[bot]`** using `actions/github-script@v7` with `github-token: ${{ secrets.COPILOT_PAT }}`.

### `lex-app-docs` — auto-close empty-PR workflow

Reference copy at `docs/ci-cd/docs-auto-close-empty-pr.yml` in this repo; the live file lives in `lex-app-docs/.github/workflows/auto-close-empty-pr.yml`.

Copilot does not always realise a release has nothing for it to document — it sometimes still opens a PR for test-only releases, internal refactors, or CI-only changes. That PR has 0 changed files (or a 0-line net delta) and would otherwise need a human to close it. This workflow handles that cleanup.

1. `on: pull_request: types: [opened, synchronize, reopened]`.
2. Gate: `github.event.pull_request.user.login == 'copilot-swe-agent[bot]'` — only acts on PRs authored by the Copilot agent. Human PRs and other bots are untouched.
3. Measure the PR delta via `gh pr view --json files` — both `files | length` and `sum(additions + deletions)`. No checkout; the GitHub API is the source of truth.
4. If either metric is zero, comment on the PR explaining the auto-close and `gh pr close` it. Otherwise the workflow logs the delta and exits cleanly, leaving the PR for human review.

Permissions: `pull-requests: write`, `contents: read`. Uses the repo's default `GITHUB_TOKEN` — no extra secrets. The `synchronize` trigger re-evaluates on each push from Copilot, so a PR that's empty at `opened` time but gets real commits later is *not* re-opened (we close once); it falls back to a manual reopen if needed (see the troubleshooting note in `automated-docs-pipeline.md`).

### Why GitHub App + classic PAT (hybrid)

* **GitHub App** for `repository_dispatch` because: short-lived tokens, repo-scoped, org-controlled, no person-bound coupling.
* **Classic PAT** for assigning the Copilot agent because: as of today, neither `GITHUB_TOKEN` nor a GitHub App token can assign `copilot-swe-agent[bot]` — only a real user PAT can. The PAT must be SSO-authorized for `ExcellenceCloudGmbH` (we are SAML-enforced).

If GitHub ever lifts the Copilot-assignment restriction, the PAT goes away.

---

## 7. Secrets + variables

### Repo-level secrets on `lex-app`

| Secret | Used by | Purpose |
|---|---|---|
| `PYPI_API_TOKEN` | `pip_publish.yml` | Upload to PyPI |
| `SENDGRID_API_KEY` | `showcase_tests.yml` | Send Platform Health Report email |
| `SHOWCASE_REPORT_RECIPIENTS` | `showcase_tests.yml` | Comma-separated recipients |
| `SHOWCASE_REPORT_FROM` | `showcase_tests.yml` | SendGrid-verified sender |
| `SHOWCASE_REPORT_FROM_NAME` | `showcase_tests.yml` | Display name (optional) |
| `DOCS_APP_ID` | `update_docs.yml` | GitHub App numeric ID |
| `DOCS_APP_PRIVATE_KEY` | `update_docs.yml` | GitHub App `.pem` |
| `KEYCLOAK_URL` / `KEYCLOAK_REALM` / `KEYCLOAK_REALM_NAME` / `OIDC_RP_CLIENT_ID` / `OIDC_RP_CLIENT_SECRET` / `OIDC_RP_CLIENT_UUID` | `showcase_tests.yml` | Optional — enables init cluster's live-Keycloak read-only tests in CI |
| `PAT` | `custom-image.yml` (checkout) | Long-lived PAT used by the Docker build's checkout step |
| `NPM_MARMELAB_TOKEN` | `frontend_build.yml` | Private `@marmelab` npm registry |
| `FRONTEND_REPO_TOKEN` | `frontend_build.yml` | PAT for cross-repo checkout of the frontend repo |

### Repo-level secrets on `lex-app-docs`

| Secret | Used by | Purpose |
|---|---|---|
| `COPILOT_PAT` | receiver workflow | Classic PAT, SSO-authorized, used solely to assign `copilot-swe-agent[bot]` |

### Repo-level **variables** on `lex-app`

| Variable | Default | Purpose |
|---|---|---|
| `LEX_RUN_KEYCLOAK_DESTRUCTIVE` | `0` | Set to `1` to opt the destructive 1l clean-state Keycloak tests in (only safe against test realms) |
| `SHOWCASE_BRAND` | `Excellence Cloud` | Brand string on the Platform Health Report |

---

## 8. Showcase test runner — what it actually does

`.github/scripts/run_showcase_suite.py` is the cluster-grouped Django test driver. It:

1. Parses `--only` into a list of `(cluster_key, optional_test_suffix)` tuples.
2. For each cluster:
   * Runs `coverage run -m lex test <dotted.path>` against the corresponding `lex/test_project/tests/<cluster>/` package.
   * Captures per-cluster pass/fail + counts.
3. Combines `.coverage` files across clusters.
4. Writes `manifest.json` with shape:
   ```json
   {
     "overall": {
       "outcome": "success" | "failure",
       "clusters_passing": 5,
       "clusters_total":   5
     },
     "clusters": [
       { "key": "init", "passed": 24, "failed": 0, "skipped": 0, "duration_s": 12.4 },
       …
     ]
   }
   ```
5. Sister scripts: `build_showcase_report.py` (manifest → HTML/PDF via WeasyPrint), `send_showcase_email.py` (HTML/PDF → SendGrid attachment).

The "platform health" framing is deliberate — the email goes to non-technical stakeholders on every release day, so it leads with cluster narrative + pass counts and only links to coverage as a secondary artefact.

---

## 9. What the gate does **not** check

These are deliberate decisions, but call them out for the engineer in case any are wrong for your environment:

1. **No coverage threshold.** `coverage.xml` is published as an artefact, not gated. The release gates are "did any selected showcase cluster fail" and "did the Celery Redis broker examples fail".
2. **No mutation testing.** Listed as "future work" in `docs/testing-methodology.md`.
3. **No frontend gate on the backend tests.** `frontend_build.yml` runs in parallel with `pip_publish.yml` on the same `release: published` event. The header comment is wrong.
4. **No license-scan or vuln-scan gating.** Both run weekly, results are uploaded as artefacts, no PR/release is blocked on them.
5. **No staging deploy.** The Docker image is built and pushed to the registry; consuming environments pull on their own schedule.
6. **35 pre-existing failing backend tests** are marked `@unittest.skip` across 12 files (documented in `progress.md` Section 9). They live outside the showcase clusters, so they don't touch the gate, but they exist.
7. **No integration tests in CI for the destructive 1l Keycloak path.** That sub-cluster only runs locally against `auth.excellence-cloud.de` realm `lex` with three opt-in env vars and a `clientId` containing `test`. CI will skip it.

---

## 10. Open issues / good places for an engineer to start

In rough priority order:

1. **`build/Dockerfile` `v`-prefix bug** — `pip install lex-app==v2.0.0rcXXX` is rejected by PyPI. One-line fix; gates the whole image build.
2. **`frontend_build.yml` is not gated on the backend tests** despite its header claiming so. Either re-add a `gate-tests` job that calls `showcase_tests.yml` (cheap, parallel-safe) or update the header to match reality.
3. **`pip_publish.yml` and `frontend_build.yml` race on `release: published`.** Both fire from the same event with no ordering. If you want a single sequencing tree, switch `frontend_build` to `workflow_run` of "Publish to PyPI" the same way `custom-image.yml` does, so it inherits the test gate transitively.
4. **No coverage threshold.** Currently informational. Reasonable next step: introduce `--fail-under=50` and ratchet up by 5 points per release. We have a process discipline around this in `docs/testing-methodology.md` but no enforcement.
5. **`COPILOT_PAT` is a person-bound credential.** Watch GitHub release notes for App-token support for Copilot agent assignment — when that lands, retire the PAT.
6. **Two duplicated checkout-with-PAT patterns** in `custom-image.yml` and `frontend_build.yml`. Unifying them on the GitHub App (already used by `update_docs`) would remove `PAT` and `FRONTEND_REPO_TOKEN` from the secret store.
7. **The 35 skipped tests in `progress.md` Section 9.** Tracked, not in the gate, low-urgency but visible.
8. **Weekly scans are informational only.** `bandit` + `scancode` upload artefacts; nothing flags a critical CVE on the release path. Consider adding a release-time `pip-audit` or `osv-scanner` step inside `pip_publish.yml`.
9. **Pip cache key is `**/requirements.txt`** — `pyproject.toml` changes don't bust it. If you move dependencies into `pyproject.toml` over time, the cache key needs updating.
10. **Docker push tag derivation.** `git describe --tags --abbrev=0 HEAD` only works because the workflow checks out with `fetch-depth: 0`. If anyone shallowly checks this workflow in future, tag resolution silently breaks.

---

## 11. Pointers to existing docs

* `docs/testing-methodology.md` — full test suite methodology + rationale.
* `docs/ci-cd/automated-docs-pipeline.md` — `repository_dispatch` pattern, GitHub App setup, troubleshooting for the docs flow.
* `docs/ci-cd/developer-story.md` — narrative timeline of how the pipeline got here.
* `docs/ci-cd/docs-receiver-workflow.yml` — reference copy of the receiver in `lex-app-docs/.github/workflows/`.
* `docs/ci-cd/showcase-ci.md` — the stakeholder-side view of the Platform Health Report.

If anything in those docs contradicts this file, **this file is the authoritative one** as of the date in the header.

