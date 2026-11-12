---
date: 2026-11-10
clusters: [1]
tests_added: "22 new scenarios (1.632-1.653), new file test_1bm_custom_domain_hosts.py"
suite_tally: "init (.venv-lex-core): 662 pass / 14 skip / 4 fail; the same 4 fail on the base commit (641 pass there), all subprocess-environment failures in 1aq and 1at"
---

# Batch 1bm — custom domains reach the pods, and dashboards stay first-party

See [1bm](../../clusters/01-init/batches.md). The Instance Controller could already attach a
customer's domain to an instance and route it, but nothing told the *processes*: Django rejected
the hostname it had never heard of, the Streamlit proxy completed sign-in on the canonical host
and left the user's host without a cookie, and Reflex's websocket CORS named one origin.
`lex/hosts.py` is the single reader of `LEX_APP_HOSTS` / `LEX_STREAMLIT_HOSTS` /
`LEX_REFLEX_HOSTS` (written by lex-instance 3.2.0) and falls back to the three `*_URL` variables,
so an instance without a custom domain is byte-identical to before — asserted in 1.632, 1.641 and
1.646 rather than assumed, because `ALLOWED_HOSTS` is a security boundary.

The second half is `same_site_url`: a dashboard is framed from the hostname that shares a
registrable domain with the page embedding it. Across registrable domains the iframe is a
third-party context, and Safari blocks its cookies outright — which is why "Streamlit works for
everyone except this customer" was a browser bug nobody could reproduce.
