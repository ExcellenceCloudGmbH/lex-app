# Automated Documentation Pipeline

> **Owner:** CI/CD
> **Repos involved:** `lex-app`, `lex-app-docs`
> **Last updated:** 2026-04-09

---

## What it does

After every successful PyPI publish of `lex-app`, the pipeline automatically:

1. Computes the code diff between the previous and current release tag.
2. Creates an issue in `lex-app-docs` with the diff and update instructions.
3. Assigns the issue to **GitHub Copilot**, which reads the diff, updates docs, and opens a PR.
4. If Copilot opens a PR with **no actual doc changes** (test-only releases, internal refactors, CI edits), a follow-up workflow in `lex-app-docs` auto-closes that empty PR with an explanatory comment — so the maintainer queue isn't littered with no-op PRs.

**No docs go live without human approval** — it's always a PR.

---

## Architecture

Uses the **`repository_dispatch` pattern** — each repo owns its own CI logic:

```
lex-app                                  lex-app-docs
───────                                  ────────────

pip_publish.yml succeeds
        │
        ▼ (workflow_run trigger)
update_docs.yml
        │
        ├─ Read git tags
        ├─ Generate GitHub App token
        │  (scoped to lex-app-docs)
        │
        ▼
  Fire repository_dispatch ──────────►  auto-update-docs.yml
  event: "release-published"                    │
  payload: { head_tag, base_tag }               ├─ Checkout lex-app (read-only)
        │                                       ├─ Compute diff
        └─ Done                                 ├─ Create issue with diff
                                                └─ Assign @copilot
                                                        │
                                                        ▼
                                                Copilot reads issue + repo
                                                        │
                                                        └─ Opens PR with doc updates
                                                                │
                                                                ▼
                                                        auto-close-empty-pr.yml
                                                        (on: pull_request)
                                                                │
                                                                ├─ If PR has 0 changed
                                                                │  files or 0 line delta:
                                                                │     comment + close
                                                                │
                                                                └─ Otherwise: leave open
                                                                   for human review
```

### Why this pattern?

- **Minimal cross-repo auth**: The only cross-repo call is the dispatch event. Copilot and the PR in `lex-app-docs` use the repo's own permissions.
- **Separation of concerns**: Each repo owns its CI. The docs workflow lives in the docs repo.
- **Security**: The GitHub App token used for the dispatch is short-lived (1 hour) and scoped to just `lex-app-docs`.
- **No LLM API tokens needed**: Copilot is built into GitHub — no `MODELS_API_TOKEN` or external API keys required.

---

## Workflow files

| File | Repo | Purpose |
|---|---|---|
| `.github/workflows/update_docs.yml` | `lex-app` | Fires `repository_dispatch` after successful publish |
| `.github/workflows/auto-update-docs.yml` | `lex-app-docs` | Receives event, creates issue, assigns Copilot |
| `.github/workflows/auto-close-empty-pr.yml` | `lex-app-docs` | Auto-closes empty Copilot PRs (no docs to apply) |

Reference copies of the `lex-app-docs` workflows are stored alongside this file:

- [`docs/ci-cd/docs-receiver-workflow.yml`](docs-receiver-workflow.yml) → `lex-app-docs/.github/workflows/auto-update-docs.yml`
- [`docs/ci-cd/docs-auto-close-empty-pr.yml`](docs-auto-close-empty-pr.yml) → `lex-app-docs/.github/workflows/auto-close-empty-pr.yml`

---

## Setup

### 1. GitHub App (`lex-docs-bot`)

The GitHub App generates short-lived tokens for the cross-repo dispatch call.

**Installation:**

1. Go to: `https://github.com/apps/lex-docs-bot`
2. Click "Install"
3. Select **ExcellenceCloudGmbH**
4. Choose "Only select repositories" → pick **lex-app** and **lex-app-docs**
5. Click Install

**App permissions:**

| Permission | Level | Why |
|---|---|---|
| Repository contents | Read & Write | Fire `repository_dispatch` events |

### 2. Secrets (on `lex-app` only)

| Secret | Value |
|---|---|
| `DOCS_APP_ID` | GitHub App numeric ID (visible in App settings) |
| `DOCS_APP_PRIVATE_KEY` | The `.pem` private key (generate in App settings → Private keys) |

No secrets needed on `lex-app-docs` — Copilot uses its built-in access.

### 3. Enable Copilot Coding Agent on `lex-app-docs`

1. Go to **Organization Settings → Copilot → Policies**
2. Enable "Copilot coding agent"
3. Optionally: go to **`lex-app-docs` → Settings → Copilot → Coding agent** to confirm it's enabled at the repo level

### 4. Copy the receiver + auto-close workflows to `lex-app-docs`

```bash
# From the lex-app repo root:
cp docs/ci-cd/docs-receiver-workflow.yml \
   /path/to/lex-app-docs/.github/workflows/auto-update-docs.yml

cp docs/ci-cd/docs-auto-close-empty-pr.yml \
   /path/to/lex-app-docs/.github/workflows/auto-close-empty-pr.yml
```

Then commit and push to `lex-app-docs`. Both files must live on the default branch of `lex-app-docs` (the `pull_request` trigger fires from the file on the default branch, not the PR's source branch).

---

## Manual trigger

Both workflows support `workflow_dispatch` for testing or re-running:

**From `lex-app`** (fires the dispatch):
```
Actions → "Update Documentation (Post-Release)" → Run workflow
  base_ref: v2.3.0    (previous tag)
  head_ref: v2.4.0    (current tag)
```

**From `lex-app-docs`** (runs directly, skips dispatch):
```
Actions → "Auto-Update Documentation" → Run workflow
  base_tag: v2.3.0
  head_tag: v2.4.0
```

---

## Troubleshooting

### "Resource not accessible by integration"
The GitHub App is not installed on the target repo, or the token doesn't have `contents:write` permission. Re-install the App on both repos.

### Issue created but Copilot didn't respond
- Verify Copilot coding agent is enabled at both org and repo level.
- Check that the issue was assigned to `copilot` (not a user named "copilot").
- Copilot may take a few minutes to start working on the issue.

### Copilot PR has incorrect changes
Close the PR and edit the issue with more specific instructions, or fix manually. You can also re-run the workflow with different tags to create a new issue.

### "No documentation updates needed"
If Copilot decides the code changes don't affect any existing docs, one of two things will happen:

1. **Best case:** Copilot comments on the issue and never opens a PR. Nothing to clean up.
2. **Common case:** Copilot opens an empty PR anyway. `auto-close-empty-pr.yml` will detect the empty diff (0 files changed *or* 0 line delta) and close the PR with a comment explaining why. Look for runs of "Auto-Close Empty Docs PR" in the `lex-app-docs` Actions tab if you want to audit which PRs were auto-closed.

Either way is normal for test-only releases, internal refactors, and CI-only changes.

### Auto-close fired on a PR that had real changes
The auto-close workflow gates on `pull_request.user.login == 'copilot-swe-agent[bot]'` and on `gh pr view --json files`. If a non-empty PR was closed:

- Confirm the PR really was empty at the moment the workflow ran — check the run logs for the `changed_files=` and `total_delta=` values.
- If those are both non-zero, that's a bug in the workflow — file an issue, not a manual reopen. The "PR has content" branch should have run.
- If both were zero, Copilot probably pushed real changes *after* the run had already closed the PR (e.g. the workflow fired on `opened` while Copilot was still pushing). The PR is now closed but the branch has real commits — reopen the PR manually; the `synchronize` re-run on subsequent pushes only acts on PRs that are still open.

---

## Design decisions

### Why Copilot Coding Agent instead of the Models API?

| | Copilot Agent | Models API |
|---|---|---|
| API token needed? | No | Yes (`MODELS_API_TOKEN`) |
| Repo context? | Full (reads all files) | Only what you pass in the prompt |
| Output | Creates PR directly | Returns JSON, you build the PR |
| Retry on failure | Re-assign or comment | Re-run workflow |
| Workflow complexity | ~50 lines | ~200 lines |

Copilot has full repo context natively, so it can follow existing doc structure, frontmatter style, and naming conventions without being told. The Models API requires bundling docs into the prompt (limited by token window).

### Why a GitHub App instead of a PAT?

| | GitHub App | PAT |
|---|---|---|
| Tied to a person? | No (org-owned) | Yes |
| Token lifetime | 1 hour | Up to 1 year |
| Scope | Only approved repos | Can be broad |
| What if person leaves? | Nothing breaks | Pipeline breaks |

### Why `repository_dispatch` instead of a monolithic workflow?

The original design ran everything from `lex-app`. With `repository_dispatch`, the `lex-app` side just fires a webhook. The docs repo does the work with its own permissions — reducing the cross-repo auth surface to a single API call.
