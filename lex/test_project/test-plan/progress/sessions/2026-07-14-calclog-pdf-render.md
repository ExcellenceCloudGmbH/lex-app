---
date: 2026-07-14
clusters: [15h]
tests_added: "3 (15.32–15.34) + source in 1 file (DownloadMarkdownPdf)"
suite_tally: "15h 3 pass / 0 fail; cluster-15 environmental baseline unchanged (9 known local failures in 15b–15e, memory-documented)"
---

**Batch 15h landed — the calculation-log PDF export now renders like the log
view (customer report 2026-07-14: markdown structure lost in the download).**
Two root causes: xhtml2pdf's minimal CSS + bare stylesheet, and a markdown
parser surface that silently diverged from the log view's markdown-it (the
`code-friendly` extra disabled `__bold__`; `~~strike~~` was never enabled).
Fix: render via **WeasyPrint** (already in requirements.txt) with a
GitHub-style stylesheet mirroring the frontend log view; extras now
`tables, fenced-code-blocks, strike`; xhtml2pdf kept as a runtime fallback so
missing pango/cairo degrades styling instead of 500ing. Contract pinned:
**anything the log view renders appears rendered in the PDF** — 15.33 raw
syntax never leaks, 15.34 every feature's content survives (verified via
pypdf text extraction). See [batch 15h](../../clusters/15-calculation_logging/batches.md).
