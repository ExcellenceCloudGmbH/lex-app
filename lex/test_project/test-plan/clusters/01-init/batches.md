

### Batch 1eu — lex.ui frame: areas nav= does not list follow the listed ones; a typo is still refused ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.1030 |
| Type | U |
| Files covered | `lex/lex_app/reflex/shell.py` (`check_nav`, `check_area`, `_raw_areas`, `_areas`), `lex/lex_app/reflex/ui/pages/frame.py` (`nav=`), `lex/lex_app/reflex/ui/pages/declare.py` (`finalize`) |
| Test file | `lex/test_project/tests/init/test_1eu_ui_frame_unlisted_areas.py` |
| Test classes | `TestCluster01eu_FrameUnlistedAreas` (1.1030) |
| Fixtures | `FrameCase` and `_record` from 1eb |
| Tests landed | **1 pass / 0 fail** |
| Status | ✅ Complete |

Section 6.22 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1030 | Unlisted areas follow; a typo is still refused | finalize goes on with raw areas Admin, Tools unlisted; sections Orders, Admin, Tools; a ui.page in Admin listed there; nav=Ordres and a record page in Reprots refused, naming Orders, Admin, Tools |
