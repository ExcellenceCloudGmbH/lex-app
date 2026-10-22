---
date: 2026-10-21
clusters: [1]
tests_added: "16 new scenarios (1.420-1.435)"
suite_tally: "init, 1az: 16 pass / 0 fail"
---

# Batch 1az — `lex incident_tz scan`

[1az](../../clusters/01-init/batches.md) adds the read-only scan the TZ remediation plan
calls Q1 (LEX-747): for one database, the periods in which rc212 – v2.1.3 wrote to it, dated
from its own audit log, and a verdict A–E for every datetime value, from its history,
meta-history or row stamps. `lex/lex_app/incident_tz/scan.py` is one file with no Django or lex
import, so it also runs on its own against an instance that has not been upgraded;
`lex incident_tz scan` runs it on Django's connection. Five deliberate breaks of the decision
rules each fail a named scenario.
