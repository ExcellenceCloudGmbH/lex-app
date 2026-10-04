---
date: 2026-10-03
clusters: [1, 9]
tests_added: 3
suite_tally: "init + signals_ws + CLI-touching unit tests: 648 pass / 18 fail / 14 skip — all 18 environmental and identical on unmodified lex-app-v2 (reflex and lex_mcp not installed locally, no coverage data), four Reflex files not collected for the same reason — 1au 2 pass, 9g 1 pass"
---

# Batches 1au and 9g — `lex --version`, and a star import that raised

Two framework defects found while auditing the documentation. In both cases the docs had been
rewritten to work around the code.

`lex --version` answered "No such option", although `main()` already sent it past the Django
bootstrap: the group never declared the option, so the installation guide's first check had become
`pip show lex-app`. The option now prints the version `/health` reports
([batch 1au](../../clusters/01-init/batches.md), 1.384–1.385). That is `lex._version`, not the
package metadata: an image built from a git ref leaves the metadata at `0.0.0.dev0` and stamps the
module.

`from lex.core.signals import *` raised `AttributeError`, because two profile handlers deleted in
February left their names in `__all__`
([batch 9g](../../clusters/09-signals_ws/batches.md), 9.43).

Once a release carries the option, the installation guide and the CLI reference in lex-app-docs can
go back to `lex --version`. Both currently say it does not exist.
