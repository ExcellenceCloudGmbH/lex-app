

### 1ev. lex reflex check follows the project's own imports from its dashboards ✅

**What it tests:** `check_paths` adds every project module the structure module and the dashboards import, transitively (absolute, relative, a package's `__init__`), and nothing outside the project or not imported; the imported module's undecorated public method is reported.

**Why a regression matters:** A handler the browser can call without lex.ui's guard, in a module the check never read.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.1031 | The check follows the project's imports | paths: _dashboards, _reflex_structure.py, helpers.py, orders/__init__.py, orders/pages/__init__.py, orders/pages/tickets.py (not unused.py, not os/reflex/lex); one error in tickets.py: TicketsState.approve has no lex.ui decorator |

**Scenario range:** 1.1031. **Test file:** `lex/test_project/tests/init/test_1ev_reflex_check_follows_imports.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1ev](batches.md).
