---
date: 2026-11-10
clusters: [1]
tests_added: "7 new scenarios (1.619-1.625); 1.235 in 1ac re-pointed from x-streamlit-access-token to authorization, the header that now carries the credential"
suite_tally: "init: 601 pass / 9 fail / 14 skip. All 9 failures are pre-existing and reproduce identically on lex-app-v2 (the installed streamlit is 1.43.2 against the 1.66.1 floor, so the theme batch and the asset manifest fail; one `lex reflex --help` subtest needs reflex_enterprise). The five reflex-importing modules are not collected locally for the same reason. Gates: coverage detect pass, test_plan_aggregates validate unchanged at 79 pre-existing problems."
---

# Batch 1bk — the Streamlit handshake stays inside everyone's header budget

See [1bk](../../clusters/01-init/batches.md). The lex-app half of
[LEX-898](https://linear.app/lundgroup/issue/LEX-898); the other half is in `LEX_TERRAFORM_MODULES`
(`modules/lex-keycloak`), and the incident behind both is LEX-894 on instance lex-1483.

**What the incident was.** The proxy hands Streamlit the user's access token in headers, so the
token is a header line. Every per-instance Keycloak client ran with full scope, which puts every
role the user holds anywhere in the realm into every token — so a token grew with each instance a
person had access to, and reached ~8.3 KB. `websockets` refuses a header line over
`WEBSOCKETS_MAX_LINE_LENGTH` (8192 by default) and uvicorn's `websockets-sansio` handler neither
answers nor closes when the parse fails, so the dashboard never loaded, the socket stayed open, and
no log line anywhere named a header. The instance was brought back up by setting that variable by
hand.

**What landed here.** `lex/ws_header_limits.py` raises the ceiling to 65536 by `setdefault`, and is
imported before `uvicorn` in `lex/bin/lex.py` and before `websockets` in `lex/proxy.py`. The
ordering is the whole mechanism — the library reads the variable once, as `websockets.http11` is
imported, so a correct value applied one import too late is indistinguishable at runtime from no
value at all. 1.621 therefore asserts it on the import statements, matched as statements so
`starlette.websockets` eighteen lines earlier in `proxy.py` cannot anchor the check and pass
whatever the file does.

The variable only exists from websockets 13, and that release renamed the constant holding the
result (`MAX_LINE` → `MAX_LINE_LENGTH`). `websockets_line_limit_shortfall()` reads both names and
`lex streamlit` prints what it returns, in the same shape as the streamlit-version warning three
lines above it: a ceiling that is not in force has to say so, or the environment reads 65536 while
the handshake still dies at 8 KB. `requirements.txt` now names the floor as well
(`websockets>=13`), which `uvicorn[standard]` was already resolving above anyway.

**Three headers became one.** The proxy sent the token as `Authorization`,
`X-Forwarded-Access-Token` and `X-Streamlit-Access-Token` — ~25 KB of handshake for one credential
before LEX-898, on the single request that opens a connection for hours. Only `lex/streamlit_app.py`
reads any of them, through `bearer_from_headers`, which has always accepted `Authorization`; the
locally checked-out project repos read none of them. That makes `authorization` in `_HOP_BY_HOP`
load-bearing rather than housekeeping (1.623): Starlette hands header names lower-cased, so a
caller's own copy and the proxy's would be different dict keys and both would travel, reaching the
upstream joined by a comma into a value that parses as neither.

1.235 in [1ac](../../clusters/01-init/batches.md) asserted the handoff token on
`x-streamlit-access-token` and now asserts it on `authorization` — the same fact about the same
hop, under the header that carries it.

**Driven once outside the suite, because the unit scenarios cannot see the symptom.** The
scenarios assert the mechanism — the variable, the import order, the headers — but the failure they
exist to prevent is a parse inside a library this checkout does not even have at a version that
reads the variable (local is websockets 11.0.3). So: a venv with websockets 15.0.1, a real
`websockets` server, and a client sending an 8310-byte `Cookie: st_access=…`, the header that
carried the token on lex-1483.

| | header-line limit in force | result |
| --- | --- | --- |
| library left alone | 8192 | `InvalidMessage: did not receive a valid HTTP response` |
| `lex.ws_header_limits` imported first | 65536 | handshake completes, server message received |

The same run settles the attribute lookup: websockets 15.0.1 carries only `MAX_LINE_LENGTH` and
11.0.3 only `MAX_LINE`, so `websockets_line_limit_shortfall()` reading one name would have been
silent on exactly the half it has to judge. It returns `None` on 15.0.1 and the warning on 11.0.3.

The Keycloak half of LEX-898 was verified the same day against a throwaway Keycloak 26.7.2 — a
heavy user's token drops 6207 → 1307 bytes and `validate_jwt_token` still accepts it; see the
`lex-keycloak` README in `LEX_TERRAFORM_MODULES`.

**Letter.** `bk` / 1.619–1.625, not the next free pair on `lex-app-v2` (`ay` / 1.410): open branches
hold `ay` through `bj`. `bj` was taken the same way.
