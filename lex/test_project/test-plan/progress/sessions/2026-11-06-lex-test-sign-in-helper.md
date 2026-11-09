---
date: 2026-11-06
clusters: [1]
tests_added: 6
suite_tally: "01-init 1bh: 6 pass / 0 fail (with 10aa and 4y: 32 pass, 43 subtests)"
---

Batch [1bh](../../clusters/01-init/batches.md) adds `lex.lex_app.tests.auth`, the shipped helper that signs a test client in the way lex 2.5 sees a real sign-in (OIDC session with client roles, Keycloak UMA stubbed, undone by the test's cleanups). It came out of moving atrium's tests to lex-app 2.5: under 2.5 a signed-in write asks Keycloak for UMA permissions before the view, so downstream tests need a supported stub, not a patch of a private path. The two internal `_e2e_test_case` modules re-export it.

Later the same day (1.606): the reflex-ws workstream found that a test method decorated with `@override_settings` that called `sign_in` leaked its own setting into every later test -- the decorator's override ends before the cleanups, and the helper's cleanup then restored the settings it had found, the method's. The cleanup now undoes the helper's settings only while they are still in force and otherwise only notifies the `setting_changed` receivers. 1.606 fails against the old cleanup.
