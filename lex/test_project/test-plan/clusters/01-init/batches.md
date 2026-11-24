

### Batch 1fb — ui.testing: drive a searched field, seed a private value as gone, test against lex-app's API in-process ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.1038 – 1.1040 |
| Type | U (1.1038-1.1039), E (1.1040) |
| Files covered | `lex/lex_app/reflex/ui/testing.py` (`search`, `Harness.search`, `Harness.set_fields`, `lex_api`, `LexViews`), `lex/test_project/tests/init/_ui_calc_aids.py` (`ViewsClient` is now `testing.LexViews`) |
| Test file | `lex/test_project/tests/init/test_1fb_ui_testing_search_gone_api.py` |
| Test classes | `TestCluster01fb_TestingHelpers`, `TestCluster01fb_LexApiInProcess` (1.1038–1.1040) |
| Fixtures | `E2ETestCase` with `UI_FK_MODELS` (1.1040) |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

Section 6.22 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1038 | A test drives a searched field | matches Customer 001..009 with value and label only; an unsearched key refused |
| 1.1039 | A private value is seeded as gone without a logged error | no WARNING log while seeding GONE; refused with the gone sentence; a seeded value read |
| 1.1040 | self.api is answered by lex-app's views in-process | Acme Ltd, Gamma Ltd from a real list with search; calls == GET /api/model_entries/uicustomer/list |
