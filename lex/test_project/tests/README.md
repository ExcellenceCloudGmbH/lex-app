# Test Suite Layout

Tests are organised by **cluster** (see
[docs/test-plan/test-clusters.md](../../../docs/test-plan/test-clusters.md))
and within each cluster by **sub-cluster** (`1a`, `1b`, …).

```
tests/
├── fixtures/                  Shared JSON/CSV fixtures
├── init/                      Cluster 1 — Project Bootstrap
│   ├── test_1a_lex_setup.py           `lex setup` CLI
│   ├── test_1b_lex_init.py            `lex Init` command handler
│   ├── test_1b_default_authz.py       Default roles + scope→policy contract
│   └── test_1c_initial_data.py        INITIAL_DATA parse contract
│
├── crud_api/                  Cluster 2 — CRUD via REST API
│   ├── models.py                      SimpleItem, TrackedItem (+ URL names)
│   ├── test_2a_create.py              POST /api/<model>/create/
│   ├── test_2b_read.py                GET detail + list
│   ├── test_2c_update.py              PATCH / PUT
│   ├── test_2d_delete.py              DELETE
│   └── test_2e_bulk.py                many/ endpoint
│
└── validation_hooks/          Cluster 3 — Validation Hooks
    ├── models.py                      PreValidatedItem, PostValidatedItem, HookOrderItem
    ├── test_3a_pre_validation.py      pre_validation cancel semantics
    ├── test_3b_post_validation.py     post_validation rollback semantics
    ├── test_3c_hook_ordering.py       Standard lifecycle hook ordering
    └── test_3d_recursion_guard.py     _validation_in_progress
```

## Rules

1. **One sub-cluster per file.** Each file corresponds to a section
   heading in [test-clusters.md](../../../docs/test-plan/test-clusters.md).
   If a file grows past ~8 tests, the cluster plan probably needs to be
   split into more sub-clusters.

2. **Shared models live in `models.py` next to the tests that use them.**
   Never import across clusters — if Cluster 4 needs a `SimpleItem`-like
   model, it defines its own in `cluster_04_*/models.py`. This keeps
   clusters independent and makes it obvious which model is under test.

3. **Scenario numbering matches the docs.** A test named
   `test_2_3_post_missing_required_field_returns_400` implements
   scenario 2.3 from the cluster plan. When the plan changes, rename
   the test — the link between plan and code is the single source of
   truth.

4. **Every expected-failure is tracked.** Never use
   `@unittest.expectedFailure` without a comment pointing at the entry
   in [progress.md → Known Bugs Tracker](../../../docs/test-plan/progress.md#known-bugs-tracker).

## Running

```bash
# All clusters
lex test lex.test_project.tests --noinput

# One cluster
lex test lex.test_project.tests.validation_hooks --noinput

# One sub-cluster
lex test lex.test_project.tests.validation_hooks.test_3a_pre_validation --noinput

# One scenario
lex test lex.test_project.tests.crud_api.test_2a_create.TestCluster02a_Create.test_2_1_post_creates_record --noinput
```
