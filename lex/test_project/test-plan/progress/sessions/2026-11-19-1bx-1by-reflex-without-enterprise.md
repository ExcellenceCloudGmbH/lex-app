---
date: 2026-11-19
clusters: [1, 4]
tests_added: 12
suite_tally: "init + api_layer + permissions + signals_ws, no reflex-enterprise installed: 1301 pass / 5 fail (4x/4y, order-dependent, pass alone and fail on the base too) / 17 skip"
---

# Reflex without Reflex Enterprise (1bx, 1by)

lex-app's Reflex dashboards run on plain Reflex and RFDS's open path: no Reflex account, no
Reflex Enterprise in the requirements. They sign in through lex-app's own dashboard hand-off
(audience `reflex`, 10ag). Batches [1bx and 1by](../../clusters/01-init/batches.md) cover the
sign-in, the socket guard, the middleware, RFDS's identity and `lex_event`, the token, sign-out,
export ownership, the import ban and the one-port proxy (whose 1.738 found two proxy bugs, fixed
here). 1aq, 1bt and 4n were rewritten for the plain config and the binding's token; 1ar, 1as,
1at and 1bg -- Enterprise-only code -- are retired (`rolled-back`, letters kept), and
known-bugs BUG-048 to BUG-051 say why their tests went.
