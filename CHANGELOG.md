## [2.1.9] - 2026-09-04

> **Frontend changes for this release are not yet recorded.**

### Added
- **backend** give the drafter the context the backfill needed ([9633952a](https://github.com/ExcellenceCloudGmbH/lex-app/commit/9633952a)) (#742)
- **backend** attribute the three pre-manifest frontend bundles ([d8d49dee](https://github.com/ExcellenceCloudGmbH/lex-app/commit/d8d49dee)) (#738)
- **backend** land frontend provenance on lex-app-v2 ([0744dc9f](https://github.com/ExcellenceCloudGmbH/lex-app/commit/0744dc9f)) (#733)
- **backend** frontend provenance, gap recovery and backfill ([393f9298](https://github.com/ExcellenceCloudGmbH/lex-app/commit/393f9298)) (#732)

### Fixed
- **backend** derive the session key from DJANGO_SECRET_KEY, and stop refusing to boot ([148d1283](https://github.com/ExcellenceCloudGmbH/lex-app/commit/148d1283)) (#751)
- **backend** the 401 flood, the slow cold start and the dynamic-import TypeErrors are one bug ([b7c1478a](https://github.com/ExcellenceCloudGmbH/lex-app/commit/b7c1478a)) (#750)
- **backend** ignore client-admin and purge stale ignored-role policies (LEX-5) ([25c4429a](https://github.com/ExcellenceCloudGmbH/lex-app/commit/25c4429a)) (#726)
- **backend** stop a rejected draft passing as a success ([37afacea](https://github.com/ExcellenceCloudGmbH/lex-app/commit/37afacea)) (#739)
- **backend** draft from PR bodies, and stop announcing our toolchain as a feature ([3e152ae8](https://github.com/ExcellenceCloudGmbH/lex-app/commit/3e152ae8)) (#702)

## [2.1.8] - 2026-08-25

> No frontend change: the bundle is unchanged from `v2.1.4`.

### Added
- **backend** add lex ai-worktree ([92ce6ec7](https://github.com/ExcellenceCloudGmbH/lex-app/commit/92ce6ec7))
- **backend** resolve ai-* commands against the installed lex-mcp-local ([b924e231](https://github.com/ExcellenceCloudGmbH/lex-app/commit/b924e231))

### Fixed
- **backend** stop defaulting ai-worktree to edit mode ([61f58532](https://github.com/ExcellenceCloudGmbH/lex-app/commit/61f58532))
- **backend** unbreak ai-worktree, which could not be invoked at all ([2a7f3972](https://github.com/ExcellenceCloudGmbH/lex-app/commit/2a7f3972))

### Changed
- **backend** Docs folder not needed anymore ([53d7cbfa](https://github.com/ExcellenceCloudGmbH/lex-app/commit/53d7cbfa))

## [2.1.7] - 2026-08-14

> No frontend change: the bundle is unchanged from `v2.1.4`.

### Added
- **backend** build the digest and drop merge/bundle noise ([c8c960a7](https://github.com/ExcellenceCloudGmbH/lex-app/commit/c8c960a7))
- **backend** classify release tags and find the previous one ([92b89f7f](https://github.com/ExcellenceCloudGmbH/lex-app/commit/92b89f7f))
- **backend** CLI entrypoints and the quackback publish stub ([c52ea840](https://github.com/ExcellenceCloudGmbH/lex-app/commit/c52ea840))
- **backend** draft the business note via GitHub Models ([e6db623e](https://github.com/ExcellenceCloudGmbH/lex-app/commit/e6db623e))
- **backend** onboard any agentic IDE, not just PyCharm Copilot ([81edcbc6](https://github.com/ExcellenceCloudGmbH/lex-app/commit/81edcbc6))
- **backend** parse conventional commit subjects ([308b3066](https://github.com/ExcellenceCloudGmbH/lex-app/commit/308b3066))
- **backend** pluggable model provider, with Gemini and OpenAI ([f2eecc0e](https://github.com/ExcellenceCloudGmbH/lex-app/commit/f2eecc0e))
- **backend** read commits from git and enrich them with PR titles ([3105517a](https://github.com/ExcellenceCloudGmbH/lex-app/commit/3105517a))
- **backend** render the digest as a Keep a Changelog section ([ae6f372e](https://github.com/ExcellenceCloudGmbH/lex-app/commit/ae6f372e))
- **backend** resolve the frontend range from the build manifest ([3fa1ab6a](https://github.com/ExcellenceCloudGmbH/lex-app/commit/3fa1ab6a))
- **backend** vendor the LEX Python tokens + a CI freshness gate (C-1) ([5595a9a2](https://github.com/ExcellenceCloudGmbH/lex-app/commit/5595a9a2)) (#686)

### Fixed
- **backend** address final review findings ([874fdc7b](https://github.com/ExcellenceCloudGmbH/lex-app/commit/874fdc7b))
- **backend** break out of iframe on re-auth instead of framing Keycloak ([cd99fb33](https://github.com/ExcellenceCloudGmbH/lex-app/commit/cd99fb33))
- **backend** commit the style exemplar and make failure visible ([621da610](https://github.com/ExcellenceCloudGmbH/lex-app/commit/621da610))
- **backend** let the embedded Streamlit token be renewed before it expires ([b0f34da3](https://github.com/ExcellenceCloudGmbH/lex-app/commit/b0f34da3))
- **backend** never stamp edited_at/edited_by for a calculation-owned save ([705850d9](https://github.com/ExcellenceCloudGmbH/lex-app/commit/705850d9))
- **backend** one entry per PR, and move off the retiring GitHub Models ([c6a16e15](https://github.com/ExcellenceCloudGmbH/lex-app/commit/c6a16e15))
- **backend** port off the SDK's vendored FastMCP before it disappears ([3584c642](https://github.com/ExcellenceCloudGmbH/lex-app/commit/3584c642)) (#703)
- **backend** run the drafter from the default branch, not the tag ([8544b79a](https://github.com/ExcellenceCloudGmbH/lex-app/commit/8544b79a))
- **backend** stop the drafter announcing our toolchain as a feature ([a9ab950e](https://github.com/ExcellenceCloudGmbH/lex-app/commit/a9ab950e))
- **backend** treat a blank frontend SHA as no SHA ([96379cdf](https://github.com/ExcellenceCloudGmbH/lex-app/commit/96379cdf))
- **backend** unbreak the prerelease gate's 1.107 CLI-help contract ([a22d9b00](https://github.com/ExcellenceCloudGmbH/lex-app/commit/a22d9b00)) (#722)

## [2.1.6] - 2026-07-24

> No frontend change: the bundle is unchanged from `v2.1.4`.

### Added
- **backend** add VS Code run configuration generation ([86bedbcd](https://github.com/ExcellenceCloudGmbH/lex-app/commit/86bedbcd))

### Fixed
- **backend** remove stale AI cluster tests ([57044aea](https://github.com/ExcellenceCloudGmbH/lex-app/commit/57044aea))

### Changed
- **backend** ai command carry over ([374f7336](https://github.com/ExcellenceCloudGmbH/lex-app/commit/374f7336))
- **backend** ai_issue_report ([73440b22](https://github.com/ExcellenceCloudGmbH/lex-app/commit/73440b22))
- **backend** Dashboard and faq upgrades ([e01923de](https://github.com/ExcellenceCloudGmbH/lex-app/commit/e01923de))
- **backend** Lex docs change ([6cf9589e](https://github.com/ExcellenceCloudGmbH/lex-app/commit/6cf9589e))
- **backend** Mode change is a beauty ([6a901dc1](https://github.com/ExcellenceCloudGmbH/lex-app/commit/6a901dc1))
- **backend** Mode change strengthening ([9e005332](https://github.com/ExcellenceCloudGmbH/lex-app/commit/9e005332))
- **backend** publishable ([c1731fe7](https://github.com/ExcellenceCloudGmbH/lex-app/commit/c1731fe7))

## [2.1.5] - 2026-07-23

> No frontend change: the bundle is unchanged from `v2.1.4`.

### Fixed
- **backend** keep DateTimeFields aware on assignment and Excel-safe on write ([375d11e0](https://github.com/ExcellenceCloudGmbH/lex-app/commit/375d11e0))

## [2.1.4] - 2026-07-22

> Frontend: `7a728de..22c16f9`. **Removes the interface introduced in 2.1.3**, returning to the 2.1.2 design.

### Fixed
- **backend** adopt timezone-aware UTC (USE_TZ=True) + incident data migration ([b953cfd9](https://github.com/ExcellenceCloudGmbH/lex-app/commit/b953cfd9))
- **backend** default TIME_ZONE to Europe/Berlin (env LEX_TIME_ZONE); fix as_of tests ([69f741ed](https://github.com/ExcellenceCloudGmbH/lex-app/commit/69f741ed))
- **backend** DST-aware correction + flag transition-window values ([4285e5e9](https://github.com/ExcellenceCloudGmbH/lex-app/commit/4285e5e9))
- **backend** per-instance window [--cutoff, --until); no unsafe global default ([17eaabc0](https://github.com/ExcellenceCloudGmbH/lex-app/commit/17eaabc0))
- **backend** render Excel datetimes in the requester's browser timezone ([5e5467bf](https://github.com/ExcellenceCloudGmbH/lex-app/commit/5e5467bf))
- **backend** run the Postgres session in TIME_ZONE so fetched datetimes read local ([2c1c41d3](https://github.com/ExcellenceCloudGmbH/lex-app/commit/2c1c41d3))
- **frontend** re-apply filefield-clear fix (#443 code only) ([59bc870](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/59bc870))
- **frontend** send explicit UTC instants from datetime inputs ([524f92f](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/524f92f))
- **frontend** send the viewer's timezone on export requests ([a83ebf6](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/a83ebf6))

### Removed
- **frontend** reset PAC frontend to pre-redesign baseline (1cab9e5) ([2c48eaf](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/2c48eaf))
- **frontend** Revert "Merge pull request #442 from ExcellenceCloudGmbH/feat/frontend-test-plan" ([d94723f](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/d94723f))

## [2.1.3] - 2026-07-14

> Frontend: `1cab9e5..7a728de`. **This interface work was rolled back in 2.1.4.**

### Added
- **backend** batch 12i — foreign-key display names in the read contract ([3f7cbd8e](https://github.com/ExcellenceCloudGmbH/lex-app/commit/3f7cbd8e))
- **frontend** add format/fk_label_field/fk_preview to field types (phase 3) ([6bb195c](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/6bb195c))
- **frontend** add Intl-based value formatter util (phase 3) ([813f88b](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/813f88b))
- **frontend** apply navy/teal financial-services theme from CEO design study ([e15d86f](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/e15d86f))
- **frontend** bring calculation log tree in line with the design ([1af475f](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/1af475f))
- **frontend** calc log tree polish — labels, severity badges, copy ([26de772](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/26de772))
- **frontend** calculate action button + sliding edit drawer (trial) ([be94eee](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/be94eee))
- **frontend** calculation status pill component (phase 4a) ([c751308](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/c751308))
- **frontend** collapse 3 refresh controls into one icon button (phase 2) ([5135bca](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/5135bca))
- **frontend** collapsible sections in the consolidated log ([c27268c](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/c27268c))
- **frontend** collapsible sidebar tree + section headers + active highlight (phase 6) ([bd06a5d](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/bd06a5d))
- **frontend** collapsing search magnifier + pinned actions/selection ([8e1019d](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/8e1019d))
- **frontend** crosshair hover, tight calc-status column, single filter toggle, log typography (batch 3f) ([120e536](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/120e536))
- **frontend** declutter sidebar + full currency/decimals format editor ([d43bdb9](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/d43bdb9))
- **frontend** design the sliding edit panel as part of the grid surface ([cda45ae](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/cda45ae))
- **frontend** deterministic --volume seed flag ([f5ac393](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/f5ac393))
- **frontend** direct Columns/Filters toolbar buttons (phase 2) ([1934752](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/1934752))
- **frontend** dockable sidebar — drag it to either screen edge (batch 2a) ([4185b1b](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/4185b1b))
- **frontend** embed host fixture page ([315035b](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/315035b))
- **frontend** floating filter row, guaranteed default auto-fit, date-only cells ([7f8db77](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/7f8db77))
- **frontend** fold breadcrumb into the list toolbar row ([080410c](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/080410c))
- **frontend** full-height sidenav shell + consolidated header bar (CEO trial) ([b48de3e](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/b48de3e))
- **frontend** FundValuation CalculationModel + eager calc mode ([0208747](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/0208747))
- **frontend** home page as a branded launchpad ([1b0c090](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/1b0c090))
- **frontend** icon-only create, history dropdown, minimal sidebar top ([81c74c4](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/81c74c4))
- **frontend** inline theme toggle (Default/Dark) in appbar ([8b19350](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/8b19350))
- **frontend** iPhone-gallery selection mode — checkboxes hidden until Select (batch 5e) ([c3715c5](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/c3715c5))
- **frontend** live status chip + empty state for calc log stream ([31fde09](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/31fde09))
- **frontend** merge As-of + History into one segmented switch (phase 2) ([7140c71](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/7140c71))
- **frontend** move Density into a Settings gear menu (phase 2) ([cf9d14a](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/cf9d14a))
- **frontend** multi-version history seed ([ca61878](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/ca61878))
- **frontend** navy sidenav per design study + settings panel polish ([c7db50f](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/c7db50f))
- **frontend** optional column filter button — hide the header funnel while keeping filtering available ([9c885b9](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/9c885b9))
- **frontend** optional column filters + settings polish; fix numeric/boolean filters (BUG-F-004) + view activation (BUG-F-009) ([cfd5329](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/cfd5329))
- **frontend** per-column format override editor + reset (phase 5b) ([abc46cd](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/abc46cd))
- **frontend** per-column value formatter + FK label fallback (phase 3) ([fd9c047](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/fd9c047))
- **frontend** pin calc status left + tighten the actions column ([64d880c](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/64d880c))
- **frontend** process-flow model ([df7a39f](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/df7a39f))
- **frontend** proxy /ws to Django ASGI in Vite e2e mode + FundValuation nav ([32a24e1](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/32a24e1))
- **frontend** pure calculation-status classifier (phase 4a) ([2cf7bf7](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/2cf7bf7))
- **frontend** range selection (Phase A), format fix, consolidated log view ([0300b58](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/0300b58))
- **frontend** render FK cells as label chips from <fk>_label (phase 3) ([9e48ac3](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/9e48ac3))
- **frontend** render FK display names from the backend companion (BUG-F-003 frontend half) ([882d622](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/882d622))
- **frontend** reorder toolbar left group for natural reading order ([3f9572b](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/3f9572b))
- **frontend** restricted-permission viewer user + field/row scoping ([16bc0db](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/16bc0db))
- **frontend** revert to Inter font, fix selection bar overlap, clean up toolbar & breadcrumb ([7bcc0f2](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/7bcc0f2))
- **frontend** settings panel shell with density + display toggles (phase 5a) ([40a9441](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/40a9441))
- **frontend** stable AG-Grid sideBar object, hidden by default (phase 2) ([e361f44](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/e361f44))
- **frontend** switch home page to the project's Streamlit app when available ([01102e0](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/01102e0))
- **frontend** uppercase calc status labels + ABORTED/CANCELLED states (phase 4a) ([83a026e](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/83a026e))
- **frontend** use FK hover card in grid FK cells (phase 3) ([831aa18](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/831aa18))
- **frontend** use status pill in calculate cell (phase 4a) ([45424bc](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/45424bc))
- **frontend** validation hook on Fund for rejection scenarios ([20fd6e3](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/20fd6e3))
- **frontend** vertical FK hover card with preview serializer (phase 3) ([f6d96ef](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/f6d96ef))
- **frontend** wire + apply settings panel (density/display/format) (phase 5) ([a7a303e](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/a7a303e))

### Fixed
- **backend** PDF export renders like the log view (batch 15h) ([8fce4af1](https://github.com/ExcellenceCloudGmbH/lex-app/commit/8fce4af1))
- **frontend** actually pin the edit drawer toolbar to the bottom ([960cd56](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/960cd56))
- **frontend** align top bar + sidebar into one card system ([a89b6d7](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/a89b6d7))
- **frontend** batch 9d — send explicit clear marker for removed files (BUG-F-021) ([967a383](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/967a383))
- **frontend** bound calc log cards to viewport so scroll stays inside ([03b3714](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/03b3714))
- **frontend** center the history toggles + themed breadcrumb ([7ec58c6](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/7ec58c6))
- **frontend** distinct grid header color + history switch far left ([19e4b89](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/19e4b89))
- **frontend** dockable sidebar — kill the twitches, actually move the widget ([7a728de](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/7a728de))
- **frontend** drop cell text selection — it fights range selection ([68c82a7](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/68c82a7))
- **frontend** filter popup round 2 + auto-size empty-grid collapse ([8173747](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/8173747))
- **frontend** filter popup survives typing, tight auto-size, FK chip height ([3555a4e](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/3555a4e))
- **frontend** guard double-submit (BUG-F-018) + surface DRF field errors (BUG-F-007) ([26fca62](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/26fca62))
- **frontend** hide reserved keys in FK card, resolve id via id_field ([ee2c301](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/ee2c301))
- **frontend** JS-based feedback-bubble lift + sidebar layout trial toggle ([c203755](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/c203755))
- **frontend** keep actions/status columns pinned after drawer saves ([17d6ea4](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/17d6ea4))
- **frontend** lift the feedback bubble off the drawer footer ([58b761b](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/58b761b))
- **frontend** re-assert actions column width after refreshes ([b5af05d](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/b5af05d))
- **frontend** reclaim empty space above the table ([d028451](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/d028451))
- **frontend** remove redundant Refresh button from list toolbar ([477c897](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/477c897))
- **frontend** revert collapsing search + tint pinned columns ([be4037a](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/be4037a))
- **frontend** revert content-only auto-size for good + stability tests ([582e94b](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/582e94b))
- **frontend** sanitize process-flow output (BUG-F-020 XSS) + block delete on historical rows (BUG-F-001) ([9cf3181](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/9cf3181))
- **frontend** serialize time-travel anchors as UTC with explicit Z (BUG-F-022) ([3a39914](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/3a39914))
- **frontend** sort from getColumnState v33 API (BUG-F-010) + exclude read-denied columns (BUG-F-002) ([b8c7fd2](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/b8c7fd2))
- **frontend** SSRM status bar crash, contained log scroll, navy topbar ([83f50dc](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/83f50dc))
- **frontend** standalone-layout harness fixes ([5274323](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/5274323))
- **frontend** toolbar polish — remove dup toggle, tighten actions, card-like padding, move Create right ([2e617e6](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/2e617e6))
- **frontend** widen actions column for the Calculate button ([16db901](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/16db901))

### Changed
- **frontend** remove Sidebar toggle + dead isTableSidebarOpen plumbing (phase 2) ([88b73b5](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/88b73b5))
- **frontend** split page bar from table card ([520c402](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/520c402))

### Removed
- **frontend** drop the floating filter row ([06ed96e](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/06ed96e))
- **frontend** restore built-in theme toggle in appbar ([5b54b4d](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/5b54b4d))
- **frontend** Revert "docs(test-plan): CI architecture — per-cluster matrix + aggregate gate, gating flag in allocation.yaml" ([d46f6cb](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/d46f6cb))
- **frontend** Revert "feat(frontend-redesign): home page as a branded launchpad" ([aabfbba](https://github.com/ExcellenceCloudGmbH/process-admin-general-client/commit/aabfbba))

## [2.1.2] - 2026-07-13

> No frontend change: the bundle is unchanged from `v2.1.1`.

### Added
- **backend** batch 12h — clearable file fields on the REST update path ([bd4183c6](https://github.com/ExcellenceCloudGmbH/lex-app/commit/bd4183c6))

### Changed
- **backend** Fixed lex pytest by enabling keepdb=True ([3e47f157](https://github.com/ExcellenceCloudGmbH/lex-app/commit/3e47f157))

## [2.1.1] - 2026-07-13

> No frontend change: the bundle is unchanged from `v2.0.0rc221`.

### Added
- **backend** aggregates tool — allocation loader + generated dashboard (build/check) ([be06f27b](https://github.com/ExcellenceCloudGmbH/lex-app/commit/be06f27b))
- **backend** aggregates tool — allocation validate + CLI ([7e5ca8f5](https://github.com/ExcellenceCloudGmbH/lex-app/commit/7e5ca8f5))
- **backend** cloud prompt reads the sharded plan — per-cluster dir + allocation.yaml ([48bed8ad](https://github.com/ExcellenceCloudGmbH/lex-app/commit/48bed8ad))
- **backend** object-less heading frames in the calculation log tree ([be555222](https://github.com/ExcellenceCloudGmbH/lex-app/commit/be555222))
- **backend** one-shot migration splitter with fact-preservation audit ([931fd28b](https://github.com/ExcellenceCloudGmbH/lex-app/commit/931fd28b))
- **backend** parallelize suite — matrix planner + manifest aggregator ([02021a2c](https://github.com/ExcellenceCloudGmbH/lex-app/commit/02021a2c))
- **backend** rewrite showcase_tests.yml to plan/showcase/aggregate ([549834b7](https://github.com/ExcellenceCloudGmbH/lex-app/commit/549834b7))
- **backend** sharded-plan PR-shape rules — cluster shard edit + session fragment, monoliths frozen ([d3ee13b9](https://github.com/ExcellenceCloudGmbH/lex-app/commit/d3ee13b9))

### Fixed
- **backend** BUG-025 — render naive-UTC datetimes with an explicit Z ([4dbf0844](https://github.com/ExcellenceCloudGmbH/lex-app/commit/4dbf0844))
- **backend** Golden-Rule anchor never matched index.md — whole index was inlined into every prompt ([47e294b4](https://github.com/ExcellenceCloudGmbH/lex-app/commit/47e294b4))
- **backend** renumber duplicated scenario IDs + letter collision (BUG-023) ([c0f869b8](https://github.com/ExcellenceCloudGmbH/lex-app/commit/c0f869b8))
- **backend** repair duplicate session number 75 (fragment filename collision) ([75da195a](https://github.com/ExcellenceCloudGmbH/lex-app/commit/75da195a))
- **backend** repair malformed session-log row (date cell + duplicate session number) ([8040467b](https://github.com/ExcellenceCloudGmbH/lex-app/commit/8040467b))

### Changed
- **backend** promote calculation_logging to cluster 15 (BUG-024) ([9143dc27](https://github.com/ExcellenceCloudGmbH/lex-app/commit/9143dc27))
- **backend** shard monoliths into per-cluster dirs + session fragments + generated dashboard ([d3b832ea](https://github.com/ExcellenceCloudGmbH/lex-app/commit/d3b832ea))

