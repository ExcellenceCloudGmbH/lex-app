

### 1eu. lex.ui frame: areas nav= does not list follow the listed ones; a typo is still refused ✅

**What it tests:** with `ui.frame(nav=["Orders"])`, raw pages in Admin and Tools start the app and follow Orders in declaration order; a `@ui.page` joins a raw area; a `@ui.page` area neither listed nor a raw page's (with or without route arguments) is refused naming the choices.

**Why a regression matters:** An app that does not start because an old page's area is unlisted, or a typo that silently makes a new area.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1030 | Unlisted areas follow; a typo is still refused | finalize goes on with raw areas Admin, Tools unlisted; sections Orders, Admin, Tools; a ui.page in Admin listed there; nav=Ordres and a record page in Reprots refused, naming Orders, Admin, Tools |

**Scenario range:** 1.1030. **Test file:** `lex/test_project/tests/init/test_1eu_ui_frame_unlisted_areas.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1eu](batches.md).
