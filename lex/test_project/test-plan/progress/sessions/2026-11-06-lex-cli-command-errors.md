---
date: 2026-11-06
clusters: [1]
tests_added: 2
suite_tally: "01-init 1bi: 2 pass / 0 fail (with 1m: 11 pass, 1 skip)"
---

Batch [1bi](../../clusters/01-init/batches.md): running the documented sign-in operating commands end to end through the `lex` CLI showed that `lex oidc_check` reporting a problem printed a Python traceback. The CLI forwarded management commands through `call_command`, which lets `CommandError` escape; it now answers like `manage.py` (one `CommandError:` line on stderr, the command's exit code), and `--traceback` still raises.
