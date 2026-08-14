---
tags: [run-log, planning]
status: draft
---

# Run — {{date}} — {{project-name}}

> Reference shape for `plans/technical_docs/run.md`, the run log: the one place
> step status, decisions, and open questions are recorded for the whole run.

**Do not copy this file by hand.** The server creates the run log at kickstart and
resume (`_write_run_log`) and ticks each box from `notify_step_complete`
(`_tick_run_log_step`), both in the forward and completion `wrapper_mcp.py`. A
hand-copied board would be wrong for completion mode, which covers only
`COMPLETION_START_STEP`-19 because it inherits the earlier steps from the preceding
MVP run. This file exists to document the shape and explain why it is fixed.

Do not rename the `## Step Status Board` heading and do not change the `- [ ]`
checkbox format. `_check_run_md_status_board` parses them, and treats the run as
complete only when every box in that section is ticked. It gates the switch to
backward mode on resumed runs, where in-memory step state is gone.

## Metadata

- Owner:
- Participants:
- Lex Context Version:
- Scope:
- Run Path: `plans/technical_docs/`

## Step Status Board

One box per step in `TOTAL_PLAN_STEPS` (currently 20, steps 0-19). Completion
mode starts at `COMPLETION_START_STEP` (currently 9) and inherits steps 0-8 from
the preceding MVP run; tick those from that run's log rather than redoing them.

- [ ] Step 0 — Project Overview
- [ ] Step 1 — Input/Output File Schemas
- [ ] Step 2 — Requirements and End Goals
- [ ] Step 3 — Central User Story
- [ ] Step 4 — Architecture and Data Flow
- [ ] Step 5 — Functional Breakdown and Information Mapping
- [ ] Step 6 — UML + ER + State Machine Diagrams
- [ ] Step 7 — Business Logic Pseudocode
- [ ] Step 8 — Rule Compliance Validation
- [ ] Step 9 — Implementation Planning
- [ ] Step 10 — Blueprint Consolidation, Release Readiness, and Plan Compliance
- [ ] Step 11 — Full Project Implementation (Code Delivery)
- [ ] Step 12 — Initial Data Upload Plan
- [ ] Step 13 — Streamlit Capabilities Execution Plan
- [ ] Step 14 — Code-Level Lex Rule Compliance Validation
- [ ] Step 15 — Technical-Map Architecture & Module Discovery
- [ ] Step 16 — Technical-Map Convention & Pattern Extraction
- [ ] Step 17 — Technical-Map Per-Module CONTEXT
- [ ] Step 18 — Technical-Map Synthesis, Data Sources & Cross-Referencing
- [ ] Step 19 — Forward ↔ Backward Doc & Code Synchronization

## Decisions Log

| Date | Decision | Why | Impacted Steps |
| --- | --- | --- | --- |
| ... | ... | ... | ... |

## Open Questions

Mirror of `plans/technical_docs/questions-to-user.md`, which the step agents
append to when they need an answer to proceed.

- ...

## Artifact Links

Each step writes `plans/technical_docs/step-NN-<name>.md`.

- I/O schemas:
- Requirements:
- User story:
- Architecture:
- UML / ER:
- Pseudocode:
- Implementation outputs:
- Technical map:

## Workflow Completion

- Planning complete (steps 0-8): Yes / No
- Implementation complete (steps 9-14): Yes / No
- Technical map and sync complete (steps 15-19): Yes / No
- Date:
