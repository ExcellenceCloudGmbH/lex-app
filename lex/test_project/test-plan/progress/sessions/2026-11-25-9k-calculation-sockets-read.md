---
date: 2026-11-25
clusters: [9]
tests_added: "8 new scenarios (9.74-9.81), batch 9k; 9f and 9j switched to LEX_SEC_INTERNAL=false"
suite_tally: "signals_ws + api_layer + 4m + 4p + 7m + 15r: 263 pass / 0 fail; with the consumers before the fix 9.74 and 9.76-9.81 fail and 9.75 passes"
---

# Batch 9k: the calculation sockets carry a run only to who may read its record

[Batch 9k](../../clusters/09-signals_ws/batches.md) closes the WebSocket half of the leak that
[10ah](../../clusters/10-api_layer/batches.md) closed on the REST endpoints. Three live channels
sent every run to every connected socket: the live-log socket (named after the record, so any
record could be followed by its number), the status socket (on connect every running
calculation, then each state change with its error message and traceback) and `ws/calculations`
(each run's id). Under the default `LEX_WS_AUTH=log` that included sockets without a sign-in.

They now share the REST rule through `lex/api/consumers/record_access.py`: `SocketReader`
builds the request the permission layer expects from the socket's user and Keycloak sign-in
(client roles from the sign-in, UMA permissions asked with its access token while it is valid)
and asks `calculation_record_readable` / `sees_every_calculation`. Socket code never refreshes
a token, because a refreshing socket would keep an idle sign-in alive, so a socket whose token
has run out keeps the permissions it last had. A refused socket stays open and hears nothing.
Every state change still updates the process's active-calculation store.

9f and 9j now set `LEX_SEC_INTERNAL=false`: their broadcasts name made-up records, which the
rule refuses, and they test wiring and the sign-in. 9.79 pins the new default for an unsigned
socket under `log`.
