---
date: 2026-11-25
clusters: [1]
tests_added: "10 new scenarios (1.1123-1.1132) in 1fc; 1ac (1.238, 1.239, 1.242), 1ad (1.301, 1.302) and 1be (1.523) re-pointed at lex/lex_app/streamlit/auth.py, assertions unchanged"
suite_tally: "init: 897 pass / 0 fail / 17 skip on this branch, against 887 pass / 0 fail / 17 skip on lex-app-v2 at 76a36358 (the same 17 skips). Gates: coverage detect pass; test_plan_aggregates validate unchanged at 79 pre-existing problems; check_doc_env_vars flags LEX_API_URL until the lex-app-docs change is mirrored; check_doc_imports pass."
---

# Batch 1fc — a dashboard calls the Lex API with the token current when the request is sent

See [1fc](../../clusters/01-init/batches.md).

**What the incident was.** On the AGI provision tool (lex-app 2.4.2), four of ten uploads on 23–24 Nov
created a provision but failed to attach its document with "HTTP 401 Unauthorized: id token is
missing, user is not authenticated". The page built `{"Authorization": f"Bearer {token}"}` once per
full page run and kept it in `st.session_state.headers`; the new-provision form is an `st.dialog`,
whose reruns do not rebuild it. The backend logged `ExpiredSignatureError` on each PATCH while the
Streamlit log showed the session's own token renewed a minute or two earlier — the request carried
a copy the refresher had no way to reach.

**What landed here.** `lex.lex_app.streamlit.lex_api()` and `current_access_token()`, which read the
token per request; an in-place update of a stored `st.session_state.headers` on every renewal, so
existing dashboards recover on upgrade; and the token lifecycle moved from `lex/streamlit_app.py`
to `lex/lex_app/streamlit/auth.py`, because Streamlit re-executes the former as a new `__main__` on
every script run (its lock was a new object each run) and a dashboard cannot import it.
