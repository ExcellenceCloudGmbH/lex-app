"""
Initial-data upload tests — consolidated.

This subfolder groups every test that exercises the customer-facing
``INITIAL_DATA`` seed-loading pipeline:

* **1c** (`test_1c_initial_data.py`) — seed-data parse contract.
  Asserts the JSON shape every seed file must follow and the
  ``lex_config.py`` declarations (`INITIAL_DATA`, `PROJECT_GROUPS`)
  customers configure to point at it.

* **1f** (`test_1f_seed_idempotency.py`) — idempotency gate.
  Drives the ``load_data`` task body's "all-or-nothing" guard: seed
  load runs only when *every* referenced model is empty. Also pins
  declaration-order preservation so FK references resolve correctly.

* **1i** (`test_1i_initial_data_journey.py`) — full upload journey.
  End-to-end coverage of the seed walker, ``InitialDataAuditLogger``
  CRUD methods, batch finalize, and the create / update / delete
  arc with audit logging on and off.

All three files keep their original sub-cluster letters (1c / 1f /
1i) and scenario numbers (1.17–1.22, 1.18b/c, 1.20b, 1.51 / 1.51b,
1.52, 1.54–1.60). Test discovery picks them up automatically — no
label change is needed in CI invocations.

Sibling files in the parent folder cover non-seed parts of ``lex
init`` (project setup, command flow, KeycloakSyncManager, drift,
client preflight, full pipeline integration) and are intentionally
kept outside this group.
"""
