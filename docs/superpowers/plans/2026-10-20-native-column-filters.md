# Native Column Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AG Grid's own column filters become the only filter UI, the same in the app and in embeds. The custom filter bar and `filter_bar` are withdrawn, and value lists apply as values are ticked.

**Architecture:**
- **Frontend:** the value-list machinery stays: `filter_values`, AG Set and Multi filters fed by it, positive selection, narrowing on open, saved-view migration, the embed `filters`/`filter` channel. The MUI filter bar above the grid and everything that exists only for it go.
- **Backend:** `list_ui.filter_bar` (`Meta.filter_bar`, a model's `filter_bar`) and `lex_view(filter_bar=)` go. The fields endpoint's `value_list` flag stays.
- **Apply on tick:** the Set filter, and the Set half of a Multi filter, lose their Apply button. Conditions keep Apply / Enter.

**Tech Stack:** Django/DRF, pytest via `python -m lex pytest` (backend); React 18, react-admin 5.7, AG Grid Enterprise 33.2.1 SSRM, Vitest, Playwright (frontend).

**Spec:** `docs/superpowers/specs/2026-10-14-multi-value-filters-and-cross-filtering-design.md`. Amendment 18 is this plan's authority; Amendments 1–17 still hold where 18 does not withdraw them.

## Global Constraints

- **Nothing is pushed.** No `git push`, no PR or issue edits on GitHub or Linear. Commits are local only.
- **GPU rule.** Playwright runs only headless inside `bwrap --dev-bind / / --tmpfs /dev/dri --ro-bind /dev/null /dev/kfd -- …`. No browser preview, headed browser, in-app browser or download (`npx playwright install`, `yarn install`, `npm install`, `pip install`).
- **No customer data, and no customer project name or path,** in any committed file.
- Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No review narration in code, comments or test names.
- **zsh:** quote paths and never use the `$var:path` form (write `${var}`). Never bare `git stash`.
- **Backend tests:** from the lex-app worktree root, run `DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT="$(pwd)" /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest <paths>`. One pytest run at a time: the test database is shared.
- **Frontend baselines** (the merge with `lex-app-v2-pac-latest` at `4e52b12`): Vitest fails the same 193 tests in 24 files on the base (`localStorage` undefined in this local Node), and tsc reports 238 errors. A change keeps both sets identical by test id and error text, apart from the tests it adds, which must pass.
- **Renumbering.** This branch's test-plan batches are unmerged. A withdrawn scenario is removed and the batch is renumbered contiguously within its own range. Never renumber another batch: lex-app-v2's and the frontend base's batches keep theirs.

## File structure

Backend, in `/home/syscall/Documents/lex/.claude/worktrees/lex_multi_filter`:
- `lex/api/serializers/base_serializers.py`: drop `_filter_bar_dropped_warned`, `_resolve_filter_bar`, and the `filter_bar` key in the list-UI options (around lines 42–47, 318–380 and 489–495).
- `lex/api/views/model_info/Fields.py`: drop the `"filter_bar": []` default (around line 161) and the per-caller bar narrowing (around lines 289–298). Keep the `value_list` flag.
- `lex/lex_app/streamlit/embed.py`: drop `_filter_bar_param`, the `filter_bar` parameter, its URL parameter and docstring entries.
- Docstrings that name the bar: `lex/api/utils/filterable_fields.py:3,161,181` and `lex/api/views/model_entries/FilterValues.py:273`.
- Tests:
  - `lex/test_project/tests/api_layer/test_10p_list_ui_filter_bar.py`: keep the `value_list`-flag scenarios, drop the bar's, and rename the file to `test_10p_fields_value_list_flag.py`;
  - `test_10i_fields_view_and_list_ui.py` and `serializers/test_12e_factory.py`: restore lex-app-v2's expectations (`git diff origin/lex-app-v2 -- <file>` shows what this branch added for the bar);
  - `api_layer/models.py`: drop fixtures that only the bar tests use;
  - `init/test_1ay_lex_view_filters.py` and `permissions/test_4r_filter_values_secure_defaults.py`: drop the bar's cases.
- Shards under `lex/test_project/test-plan/clusters/{01-init,04-permissions,10-api_layer}/`, a new session fragment `progress/sessions/2026-10-20-native-column-filters.md`, and `progress/dashboard.md` (regenerated).

Frontend, in `/home/syscall/LUND_IT/process-admin-general-client/.claude/worktrees/multi_value_filters`:
- **Delete:** `src/components/model-components/list/filters/FilterBar.tsx`, `filters/filterBarColumns.ts`, `filters/__test__/FilterBar.cluster.test.tsx`, and any test file only for `filterBarColumns`.
- `src/components/model-components/list/CustomList.tsx`: drop the bar's import, state (`filterBarColumns`, `resolveDefaultFilterBarColumns`, `resetFilterBar`, `activeViewFilterBarSignature`), its render (around line 1756), its write path (`writeColumnFilter`, around line 453) and `onShowInFilterBar`.
- `src/components/model-components/list/CustomDatagrid.tsx`:
  - drop the `filterBarColumns` and `onShowInFilterBar` props (lines 508–510, 802–803);
  - drop the "Show in filter bar" column-menu item (around lines 2559–2580);
  - the Set filter params get `SET_FILTER_BUTTON_PARAMS` (below) instead of `FILTER_APPLY_PARAMS` (line 2962).
- `src/components/model-components/list/viewSettings.ts`: drop `filterBar` and `filterBarDefault`. A saved view that still carries `filterBar` restores without error and ignores it.
- `src/api/endpoints/model-info.ts:44`: drop `filter_bar` from the `list_ui` type.
- `src/utils/lexAppBridge.ts:147`: drop `'filter_bar'` from the embed URL parameters.
- `src/components/model-components/list/filters/setSelection.ts`: drop `writeColumnFilterModel` if nothing else uses it after the bar goes, and reword comments that name the bar. The pending-write and intended-model machinery stays: the startup write, carried filters and host filters use it.
- Comments that name the bar: `src/utils/embedFilters.ts:13,18`, `filters/setFilterParams.ts:10,58`, `src/components/hooks/useFillViewportHeight.ts:30`. Keep the grandparent observer: it covers any content above the grid that changes height.
- The unit tests that reference the bar, from `git grep -l -iE 'filterbar|filter_bar' -- src`.
- **e2e:** `e2e/e2e_project/Funds/Fund.py:27-28` (drop `filter_bar`), `e2e/value-lists.spec.ts`, and `e2e/helpers/grid.ts:555` (comment).
- **Shards:** `docs/test-plan/clusters/{F03-grid_read,F04-grid_views,F12-embed_streamlit}/` and a new session record `docs/test-plan/progress/sessions/2026-10-20-native-column-filters.md`.

Docs: a local branch in lex-app-docs (Task 4).

---

### Task 1: Backend — withdraw `filter_bar`

**Files:** see "Backend" above.

**Interfaces:**
- Produces: `GET api/model_info/<model>/fields` keeps every field's `value_list: true|false`, and its `list_ui` has no `filter_bar` key. `lex_view()` has no `filter_bar` parameter; passing it is a `TypeError` like any unknown keyword.
- Unchanged: `filter_values`, including `search`; GET `__blank` and typed `__exact`/`__in`; `lex_view(filters=, on_filter=)`; `LEX_EMBED_ALLOWED_ORIGINS`.

- [ ] **Step 1: Write the failing test.** In the renamed `test_10p_fields_value_list_flag.py`, add a scenario at the end of the batch: the fields endpoint's `list_ui` carries no `filter_bar`, even for a model with a `filter_bar` attribute and a serializer with `Meta.filter_bar`.
  ```python
  def test_10_9x_list_ui_has_no_filter_bar(self):
      """
      Scenario 10.9x: the grid's own column filters are the only filter UI (Amendment 18).
      Given: a model and serializer that still declare filter_bar
      When: the fields endpoint is read
      Then: list_ui carries no filter_bar key, and every field still carries value_list
      """
      resp = self.client.get(self.fields_url)
      self.assertEqual(resp.status_code, 200)
      list_ui = resp.data["list_ui"]
      self.assertNotIn("filter_bar", list_ui, "list_ui must not offer a filter bar")
      self.assertTrue(all("value_list" in f for f in resp.data["fields"]))
  ```
  In `test_1ay_lex_view_filters.py`, replace the bar's cases with one that pins `lex_view("x", filter_bar=["a"])` raising `TypeError`.
- [ ] **Step 2: Run both and see them fail** (`filter_bar` is still in `list_ui`; `lex_view` still accepts it).
- [ ] **Step 3: Remove the code** listed under "Backend". Remove the bar's tests and fixtures, and restore `test_10i`/`test_12e` to lex-app-v2's expectations.
- [ ] **Step 4: Run** `lex/test_project/tests/api_layer lex/test_project/tests/init lex/test_project/tests/permissions lex/test_project/tests/queries lex/test_project/tests/serializers lex/test_project/tests/exports`. Every failure must also fail on `origin/lex-app-v2`.
- [ ] **Step 5: Bring the test-plan up to date.** Update the shards (10p's title and range, 1ay, 4r), add the session fragment, run `python .github/scripts/test_plan_aggregates.py build`, and check that `validate` still reports 79.
- [ ] **Step 6: Run the full suite.** Its failing ids must equal the merged head's 36 failures and 5 collection errors. Those are the baseline: cluster-15 calculation logging, Reflex not installed, stress timing, and the gate self-test.
- [ ] **Step 7: Commit** locally: `refactor(api): the grid's own column filters replace the filter bar`.

### Task 2: Frontend — remove the filter bar; value lists apply on tick

**Files:** see "Frontend" above, except the e2e files (Task 3).

**Interfaces:**
- Consumes: Task 1's `list_ui` without `filter_bar`. The frontend no longer reads it, so it also works against a backend that still sends it.
- Produces: `createSetFilterParams(field, fetchValues, SET_FILTER_BUTTON_PARAMS)` everywhere a Set filter is built, including the Set half of a Multi filter:
  ```ts
  /** A value list applies as values are ticked; Reset clears it at once. */
  const SET_FILTER_BUTTON_PARAMS = { buttons: ['reset'] }
  ```
  Condition filters keep `FILTER_APPLY_PARAMS` (`buttons: ['reset', 'apply']`, `closeOnApply: true`).

- [ ] **Step 1: Write the failing tests** on a real `AgGridReact` with the server-side row model, following the existing real-grid tests (`CustomList.realGrid.cluster.test.tsx`, `valueLists.cluster.test.ts`):
  - ticking a value in a Set filter changes `api.getFilterModel()` and fires one `filterChanged`, with no Apply click;
  - in a Multi filter, ticking in the Set half applies at once, while a typed condition applies only on Apply;
  - Reset in a Set filter clears its selection at once;
  - a saved view that still carries `filterBar` restores without error, and the bar is gone.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Remove the bar and switch the Set params** as listed under "Frontend". Delete the bar's tests, and update every test that referenced the bar, the menu item or the `filter_bar` URL parameter.
- [ ] **Step 4: Run the full Vitest suite and tsc.** Both must match the baselines in Global Constraints.
- [ ] **Step 5: Bring the test-plan up to date.**
  - Remove the bar's scenarios from F03 `p`, F04 `f` and F12 `u`, and renumber within those batches.
  - Add the apply-on-tick scenarios.
  - Write the session record.
- [ ] **Step 6: Commit** locally: `refactor(grid): AG Grid's own column filters replace the filter bar`.

### Task 3: Frontend e2e

**Files:** `e2e/e2e_project/Funds/Fund.py`, `e2e/value-lists.spec.ts`, `e2e/helpers/grid.ts`, and the F03/F12 shards for any renumbered journey.

- [ ] **Step 1: Rewrite the journeys that drove the bar** so they drive AG's column filter.
  - **The value-list journey (formerly the bar's):** ticking Equity in Strategy's column filter narrows the rows at once with no Apply, and Currency's list then shows only the remaining currencies with their counts.
  - **The embed-pagination journey:** with no bar, an embedded table keeps its pagination inside a 600 px frame, with the toolbar shown and with it hidden.
  - **Host-filter journeys:** they keep their assertions; drop any step that used the bar.
- [ ] **Step 2: Move the e2e backend checkout to Task 1's local head.**
  ```bash
  git -C /home/syscall/Documents/lex/.claude/worktrees/lex_e2e_backend fetch /home/syscall/Documents/lex/.claude/worktrees/lex_multi_filter feat/multi-value-filters
  git -C /home/syscall/Documents/lex/.claude/worktrees/lex_e2e_backend checkout --detach FETCH_HEAD
  ```
- [ ] **Step 3: Run the six specs** (value-lists, grid-filters, grid-combined, grid-views, embed, exports) with the recipe below. Compare with the merge's run A: 40 passing, 2 expected failures, 1 stale-marker pass, and 4 failures (F9.1, F4.1, F12.3, F12.11). Explain every difference.

  The recipe:
  1. **Browser directory:** a directory of symlinks in the scratchpad, used as `PLAYWRIGHT_BROWSERS_PATH`. Link `chromium_headless_shell-1217` to `~/.cache/ms-playwright/chromium_headless_shell-1243`, `chromium-1217` to `~/.cache/ms-playwright/chromium-1217`, and `ffmpeg-1011` to `~/.cache/ms-playwright/ffmpeg-1011`.
  2. **A `lex` wrapper first on PATH:** it exports `PYTHONPATH=<fe>/e2e:<be>:<be>/lex` and runs `exec /home/syscall/Documents/lex/.venv-test/bin/lex "$@"`.
  3. **Environment:** `DJANGO_SETTINGS_MODULE=e2e_project.e2e_settings`, `PROJECT_ROOT=<fe>/e2e/e2e_project`, `DATABASE_DEPLOYMENT_TARGET=local`.
  4. **Fresh database each run:** remove `e2e/e2e_project/db.sqlite3*`, `e2e/e2e_project/migrations`, `e2e/.auth`, `e2e/.e2e-api-key` and `test-results`. Then, from `e2e/e2e_project`, run `lex makemigrations e2e_project --noinput && lex migrate --noinput && lex seed_e2e_data`.
  5. **Run:** `bwrap --dev-bind / / --tmpfs /dev/dri --ro-bind /dev/null /dev/kfd -- env CI=1 LEX_E2E_KEEP_DB=1 NPM_MARMELAB_TOKEN=x PATH="$PATH" PYTHONPATH="$PYTHONPATH" PLAYWRIGHT_BROWSERS_PATH=<dir> LEX_E2E_PYTHON=/home/syscall/Documents/lex/.venv-test/bin/python npx playwright test --retries=0 --workers=1 --reporter=list,json <specs>`.
  6. **Ports:** 8000, 3000 and 8501 must be free first (`ss -ltnp`).
  7. **Afterwards:** clear the e2e artifacts. The two `.pyc` files tracked under `e2e/e2e_project/ReferenceData/` stay as they are.
- [ ] **Step 4: Commit** locally: `test(e2e): drive the grid's own column filters`.

### Task 4: Docs — a local lex-app-docs branch without the bar

**Files:** in a new worktree `/home/syscall/LUND_IT/lex-app-docs/.claude/worktrees/native_column_filters` on a new branch `docs/value-list-filters-native`, created from `origin/docs/value-list-filters` (#249's head). Pages:
- `content/using-the-app/the-grid/filtering and sorting.md`:
  - drop "The Filter Bar";
  - value lists apply as you tick, conditions wait for Apply;
  - drop the line that sends readers to the bar's server search for columns with more than 1,000 values.
- `content/model-your-data/serializers.md`: "Value lists and the filter bar" becomes value lists only, and `Meta.filter_bar` / the model `filter_bar` go.
- `content/access-and-dashboards/streamlit/embedding app pages.md`: drop the `filter_bar` argument and "Choosing the filter bar". Picks happen in the table's column filters.
- `content/access-and-dashboards/streamlit/embedding app controls.md`, `content/using-the-app/the-grid/saved views.md` and `index.md`: drop their bar mentions.
- `content/reference/Grid Filter API.md`: drop `list_ui.filter_bar`. The fields endpoint's `value_list` stays.

- [ ] **Step 1:** Make the edits. Verify every claim against Task 1's and Task 2's local heads.
- [ ] **Step 2:** Commit locally with `git -c user.name=hazempgm -c user.email=30691209+hazempgm@users.noreply.github.com commit` and the trailer. Do not push: lex-app-docs accepts one push per branch, and this branch goes up later as a fresh PR superseding #249. Never work in the main clone: it holds untracked customer files.

### Task 5: The local example page

**Files:** the private project's `_streamlit_structure.py` (local, uncommitted; the path stays out of every committed file).

- [ ] **Step 1:** Drop the `filter_bar=` arguments from `_linked_tables()`. Its docstring and caption say the picks happen in the cashflows table's vehicle and investor column filters.
- [ ] **Step 2:** Run `py_compile`, and check the calls against the new `lex_view` signature with `inspect.signature(...).bind`.

### Task 6: PR descriptions, prepared locally

- [ ] **Step 1:** Write updated bodies for lex-app #822 and process-admin-general-client #579, and a body for the future docs PR, as files in the session scratchpad. No GitHub writes.

## Self-review

- **Spec coverage (Amendment 18):**
  - bar withdrawn: Tasks 1–4;
  - `filter_bar` gone from `list_ui` and `lex_view`: Task 1;
  - apply on tick and Reset at once: Task 2;
  - the same filtering in app and embeds: Tasks 2–3, since there is no embed-only UI;
  - the 1,000-value lists: unchanged code, with the docs line in Task 4;
  - `search` kept: Task 1 leaves it.
- **Types:** `SET_FILTER_BUTTON_PARAMS` and `createSetFilterParams(field, fetchValues, buttonParams)` are used consistently.
