

### 1fb. ui.testing: drive a searched field, seed a private value as gone, test against lex-app's API in-process ✅

**What it tests:** `ui.testing.search` returns the matches a searched field offers, as the browser gets them, and refuses a field that is not searched; seeding `ui.testing.GONE` logs nothing and the handler is refused in plain words, a seeded value is read; `ui.testing.lex_api(client)` answers `self.api` with lex-app's real list view in-process.

**Why a regression matters:** Project tests that reach into lex.ui's private modules, noisy logs, or a stand-in API that tests what lex-app does not do.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1038 | A test drives a searched field | matches Customer 001..009 with value and label only; an unsearched key refused |
| 1.1039 | A private value is seeded as gone without a logged error | no WARNING log while seeding GONE; refused with the gone sentence; a seeded value read |
| 1.1040 | self.api is answered by lex-app's views in-process | Acme Ltd, Gamma Ltd from a real list with search; calls == GET /api/model_entries/uicustomer/list |

**Scenario range:** 1.1038 – 1.1040. **Test file:** `lex/test_project/tests/init/test_1fb_ui_testing_search_gone_api.py`. **Type:** U (1.1038-1.1039), E (1.1040). **Status:** ✅ Complete (2026-11-24). Batch: [1fb](batches.md).
