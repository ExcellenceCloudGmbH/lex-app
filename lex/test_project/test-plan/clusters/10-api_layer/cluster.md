## 10. API Layer

**What it tests:** REST API endpoints — One (create/read/update/delete), List, Many, History — including serializer behavior, filter backends, and the API-specific calculation trigger path.

**Why last:** The API is the external interface. It wraps everything above (CRUD, permissions, history, calculations) into HTTP endpoints. If the layers below work, the API layer is mostly wiring.

**Models needed:**
- `SimpleItem` (reused)
- `AtomicCalc` (reused)

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 10.1 | POST creates record | 201, record in DB, response includes `id` |
| 10.2 | GET retrieves record | 200, correct field values |
| 10.3 | PATCH updates fields | 200, only specified fields changed |
| 10.4 | DELETE removes record | 204/200, record gone |
| 10.5 | GET list returns all records | 200, correct count |
| 10.6 | GET history returns history rows | 200, ordered by `history_date` |
| 10.7 | Many endpoint bulk delete | DELETE with repeated `ids` query params removes selected records only |
| 10.9 | Invalid data returns 400 | Validation errors in response |
| 10.10 | Unauthenticated request handled | 401 or redirect |

### 10m. Calculation-log tree pagination + N+1 fix ✅

**What it tests:** `CalculationLogTreeView` (the endpoint that backfills the calculation-log tree when the panel opens). Before this change the view ran `CalculationLog.objects.all()` (or every row for one calc) and the serializer issued one child query per node (N+1) — loading the whole table into memory in one shot, a second compounding offender behind the backend OOM. The fix paginates with `limit`/`offset` (default 1000, max 5000), resolves every page's child ids in a single query via a prefetched `children_map`, and checks `parent_log_id` instead of lazy-loading the parent FK per node.

**Why a regression matters:** the unbounded query + N+1 is the read-path twin of the 6p buffer leak — together they were the calculation-log OOM. Pagination caps how much a single request can materialize; the `assertNumQueries` gate is what stops the N+1 from silently creeping back.

**Scenario range:** 10.61 – 10.66. **Test file:** `lex/test_project/tests/api_layer/test_10m_calculation_log_tree.py`. **Type:** I. **Status:** ✅ Complete (Session 77 — June 8). Scenarios: 10.61 default limit bounds the page; 10.62 offset walks the dataset; 10.63 child ids resolved per parent; 10.64 `isRoot` present only for parentless rows (stripped on children); 10.65 the integration surface — `assertNumQueries(3)` across 25 parents+children proves the N+1 is gone; 10.66 `calculation_id` filter scopes the rows.

---

### 10d. Global search with the Reflex report registered ✅

**What it tests:** with `IS_REFLEX_ENABLED=true` lex-app registers a `Reflex` report — an `HTMLReport` framing the project's Reflex dashboards, with no table behind it. The global search walks every registered container, so it must skip the report as it skips `streamlit`.

**Why a regression matters:** the search would read the report's `_meta`, raise `AttributeError`, and turn every search in the frontend into a 500 for any project that enables Reflex.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.82 | the Reflex report is skipped | a search over the report's real container and a model with a matching row answers 200 with that row alone |

**Scenario range:** 10.82. **Test file:** `lex/test_project/tests/api_layer/test_10d_reflex_search_exclusion.py`. **Type:** I. **Status:** ✅ Complete (2026-09-28). Covers `lex/api/views/global_search_for_models/Search.py`. Batch: [10d](batches.md).

---

### 10o. Usage recorder and interface area events (fleet metrics, phase 1) ✅

**What it tests:** the usage recorder (fleet metrics phase 1, LEX-749) — the two raw-SQL tables (`lex_usage_daily`, `lex_runtime_versions`), the API-area map and the interface's area list (`lex/core/services/usage_areas.py`), the UTC-day rule, the process-local `UsageRecorder` front that collapses repeats to one insert per (day, actor, area, channel), the middleware that drives it for every API request (`api` channel), and `POST api/usage/area` which the interface calls for its own activity (`ui` / `ui_embed` channels).

**Why a regression matters:** fleet metrics derives active-people (K1) and feature-reach (K2) counts entirely from what gets written here. A recorder that is registered but never reached, a day boundary computed in the wrong timezone, or a channel mix-up between API and interface activity all look identical to the lex-analytics collector: an instance nobody uses.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 10.83 | the core migration installs the usage tables | `lex_usage_daily` carries day/actor_kind/actor_id/area/channel keyed by all five, with no time of day; `lex_runtime_versions` carries version/first_seen/last_seen/usage_enabled keyed by version, with the types the collector relies on; an unknown channel and an API key on an interface channel are refused |
| 10.84 | the API area map matches the spec's table | every URL name in the spec's table lands in its area, and the map holds exactly those names |
| 10.85 | the grid in as-of mode is history | a list request carrying `as_of` counts as `history`; without it, `grid`; `as_of` on any other route changes nothing |
| 10.86 | a named route nobody mapped still says "this person was here" | an unmapped name is `app`; an unnamed route (SPA catch-all, static assets) is `None`; the usage endpoint's own name is `None` too, so its event isn't double-counted |
| 10.87 | a request at 00:30 Berlin on 26 Oct is filed under 25 Oct | both the UTC and the Berlin-time expression of the same instant land on 2026-10-25; a naive datetime is refused rather than guessed |
| 10.88 | the recorder sees the user every other middleware settled on | `UsageRecorderMiddleware` is the last entry in `MIDDLEWARE` |
| 10.89 | inserting the same presence twice leaves one row | one row exists after two inserts of the same (day, actor, area, channel) |
| 10.90 | one row per version, carrying the latest switch state | recording a version twice (usage on, then off) leaves one row, `usage_enabled` false, `last_seen >= first_seen` |
| 10.91 | a busy person costs one insert per area per day | two grid visits + one record visit cost exactly two inserts (one per area) |
| 10.92 | a new UTC day starts at UTC midnight | the same person recorded either side of `2026-10-26T00:00:00Z` produces two rows, filed 25 and 26 October |
| 10.93 | a failing table costs one warning, not every request | inserts raising `DatabaseError` pause recording; a request inside the pause skips the database; one retries after the pause; one warning logged in the hour |
| 10.94 | a person listing a model is active in the grid today, on the API | GETting a model's list leaves one presence row: the user's id, area `grid`, channel `api`, today's UTC day |
| 10.95 | presence, not volume | three identical GETs of the same list leave one presence row |
| 10.96 | the history tab and the grid's as-of mode are history | opening a record's history and listing with `as_of` both land a `history`/`api` row; no `grid` row |
| 10.97 | an API key is an actor of its own, never a person | a GET authenticated with a named API key leaves one row: actor `api_key` / the key's name, area `grid`, channel `api` |
| 10.98 | the Instance Controller's own calls are not usage | a GET authenticated as the instance's own `LEX_API_KEY` leaves no presence row |
| 10.99 | a failed request is not usage | a GET that 404s leaves no presence row |
| 10.100 | `LEX_USAGE_METRICS=false` stops usage rows and says so | no presence row; one version row with `usage_enabled` false, so the collector reports "disabled" rather than "nobody used it" |
| 10.101 | the running version is recorded | a served GET leaves one version row: the running lex-app version, usage enabled |
| 10.102 | a storage failure never changes the response | presence inserts raising `DatabaseError` still leave the GET's normal 200 body intact, plus one warning |
| 10.103 | the area map cannot silently rot | every URL name in the API area map resolves to a real route in the live URLconf |
| 10.104 | the endpoint accepts exactly the spec's interface areas | `UI_AREAS` is the spec's list name for name; every API area except `app` is in it; `app` is not |
| 10.105 | the same area on two channels is two rows | the same person reaching `grid` via API, interface and embedded interface leaves three rows, one per channel |
| 10.106 | the interface says a person opened History today | `POST api/usage/area {"area": "history", "embed": false}` returns 204 and one row: the session's user id, `history`, channel `ui`, today's UTC day; the event's own request adds no `api` row |
| 10.107 | use inside a Streamlit page or the widget host is told apart | `{"area": "grid", "embed": true}` returns 204 and one row on channel `ui_embed` |
| 10.108 | the event carries an area from the list and an embed flag, nothing else | an area outside `UI_AREAS` (including the API-only `app`), a missing `embed`, an extra key, a non-boolean `embed`, a non-string area, or a JSON list are all refused with 400 and no row |
| 10.109 | no session, no event | a client with no session posting a valid event gets 401 or 403 and leaves no row |
| 10.110 | an API key never appears on an interface channel | an API-key-authenticated, session-less POST gets 401 or 403 and leaves no row |
| 10.111 | the CSRF check is in force | a signed-in session that omits its CSRF token gets 403 naming CSRF, and leaves no row |
| 10.112 | `LEX_USAGE_METRICS=false` turns the events off with the rest | the switch off still answers 204 (so the interface sees nothing unusual) and records nothing |
| 10.113 | presence, not volume, whatever the interface's dedupe did | the same event POSTed three times leaves one row |
| 10.114 | a tab left open across midnight is one presence per UTC day | the same event either side of `2026-10-26T00:00:00Z` leaves two `ui` rows, filed 25 and 26 October |
| 10.115 | the hourly version throttle holds with anonymous requests in the mix | an actor's first call writes the version; a second call inside the hour does not; an anonymous request never writes it and never consumes or resets the throttle; past the hour, the next actor call writes it again |
| 10.116 | a value that is neither a known "on" nor a known "off" is off | `LEX_USAGE_METRICS` set to an unrecognised value reports off on every read, and logs exactly one warning naming the setting |
| 10.117 | whitespace and case don't matter for a true value | `LEX_USAGE_METRICS="TRUE "` reports on |
| 10.118 | liveness never touches the recorder's tables | an anonymous `GET /health` leaves no row in either table and its response is unaffected; a signed-in request right after it still writes the version row |

**Scenario range:** 10.83 – 10.118. **Test file:** `lex/test_project/tests/api_layer/test_10o_usage_recorder.py`. **Type:** U + I + E. **Status:** ✅ Complete. Covers `lex/core/sql/usage_metrics.sql`, `lex/core/migrations/0002_usage_metrics.py`, `lex/core/services/usage_areas.py`, `lex/core/services/usage_metrics.py`, `lex/core/middleware/usage_recorder.py`, `lex/api/views/usage/UsageArea.py`, the `usage-area` route in `lex/process_admin/sites/process_admin_site.py`. Batch: [10o](batches.md).

---
