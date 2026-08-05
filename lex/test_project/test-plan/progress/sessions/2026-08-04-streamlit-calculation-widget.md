---
date: 2026-08-04
clusters: [1, 10]
tests_added: 34
suite_tally: "1ab 24 pass / 0 fail; 10o 10 pass / 0 fail"
---

# Streamlit calculation widget — `lex_calculation()`

Landed [batch 1ab](../../clusters/01-init/batches.md) and
[batch 10o](../../clusters/10-api_layer/batches.md) together: a dashboard author can
now trigger a calculation on one record and watch it from Streamlit, instead of
embedding a whole React table view in an iframe to click one button.

Two clusters because the surfaces are in two domains. Cluster 1 already owns the
Streamlit helpers, so the widget and its HTTP client are tested there with the
request boundary faked and a stand-in for `st`. Cluster 10 owns the API layer, so
the new status endpoint is driven through a real client.

The pairing is the point of splitting them: 10o is the endpoint 1ab polls, and the
two halves gate opposite failures. 10o's is a leak — a purpose-built status
endpoint that answers by pk is a new place to forget a permission, and one that
distinguishes "you may not read this" from "this does not exist" confirms the
record to someone who is not allowed to know. 1ab's are silent operational ones:
a poll that never stops, an exception that erases the bottom half of a page, and
a second in-process route into `calculate` that would sidestep permission, audit
actor and `_defer_calculate_hook` at once.

Two things cost more work than the widget itself. The poll timer: `st.fragment`
reads `run_every` only when the fragment is *declared*, and only a full script run
declares it — so storing a new interval from inside the fragment never reaches the
browser, in either direction. Starting and stopping the watch both need an
app-scoped rerun, and the interval comparison is what keeps that rerun rare. And
run scoping: the record carries no timestamp of its own, so everything about "the
last run" is reconstructed from `CalculationLog`, which still holds every earlier
run's rows. Unscoped, the timings report days for a run that took seconds and a
short re-run's tail gets padded out of yesterday's lines.
