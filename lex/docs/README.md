---
tags: [lex, handbook, payload, shipped]
---

# The Lex delivery handbook — shipped payload

**This folder is product, not documentation of this repository.** It is the
handbook LEX AI installs into a *customer's* Lex project so the IDE agent
working there knows the Lex framework and how LEX AI's own modes behave.

> ### The two docs folders — read this before you edit anything
>
> | Folder | Who reads it | Ships? |
> | --- | --- | --- |
> | **`src/docs/`** (this one) | The IDE agent inside a **customer's** Lex project | **Yes** — lands there as `./docs/` |
> | **`lex_ai_docs/`** (repo root) | Maintainers and agents **building** LEX AI | No — never leaves this repo |
>
> Short version: `src/docs/` is the docs that *ship*; `lex_ai_docs/` is the docs
> *about shipping*. If what you are writing explains how this repository is
> built, tested, released, or scored, it belongs in `lex_ai_docs/` — not here.

## Paths in this folder are written from the customer's point of view

In this repository the tree lives at `src/docs/`. In the project where it is
consumed it lives at the **project root as `./docs/`**. Every path in the
shipped agent payload (`src/lex_mcp_*/.github/`) is written the downstream way:

```text
docs/lex_topics/20-LEX-SPECIFICATIONS.md    <- correct, that is the customer path
src/docs/lex_topics/20-LEX-SPECIFICATIONS.md  <- wrong in a shipped agent file
```

Do not "fix" a `docs/...` path in the payload to `src/docs/...`. It is not broken.

## How it reaches a customer project

The delivered copy is carried by the sibling **`lex-app`** package, not by the
`lex-mcp-local` wheel. `lex_mcp.ai_setup` declares
`LEX_APP_EMBEDDED_DIRECTORY_NAMES = ("docs",)` and
`copy_lex_app_docs_directory()` copies `lex/docs/` from the installed package
into the project root; `lex_mcp.ai_update` calls it on every update.

So there are two copies of this tree and they are kept in step by hand:

| Copy | Role |
| --- | --- |
| `src/docs/` (here) | Maintainer source of truth — where you edit |
| `lex-app` `lex/docs/` | The copy actually delivered — where it is read from |
| `./.venv/lib/python3.12/site-packages/lex/docs` | The installed handbook root in a customer project |

**Editing here does not ship anything on its own.** This folder is not in
`[tool.setuptools.package-data]` and not in `MANIFEST.in`, and it has no
`__init__.py`, so setuptools does not put it in the wheel. Mirror the change
into `lex-app` `lex/docs/` or it will never reach a customer.

`lex-app`'s copy also carries `planning/`, `implementation/`, and `deployment/`
phase handbooks that are deliberately **not** kept here — they belong to that
package. Copies of them lived here once, drifted from the shipped agents, and
were removed rather than maintained twice.

## Navigation

- [Lex topic map (focused index)](lex_topics/00-TOPIC-LIST.md)
- [Lex query router](lex_topics/99-QUERY-ROUTER.md) — start here for any Lex question
- [Lex specifications (canonical, project-specific)](lex_topics/20-LEX-SPECIFICATIONS.md)
- [MCP modes](mcp-modes.md) — which mode to use, and how to switch
- [MCP execution model](mcp-execution-model.md) — how a run is executed
- [Lex AI behavior map](lex-ai-behavior-map.md) — the intended flow per task shape
- [Lex local example context files](_context/lex_examples/README.md)
- [Run log template](runs/run-template.md) — reference shape; the server writes the real log

## Handbook contract

- `docs/` is **read-only** during planning and implementation runs.
- Agents must never write generated output into `docs/`. All generated
  artifacts go to `plans/technical_docs/...`.
- For LLM execution context, this handbook root plus a connected `lex-mcp`
  server are all that is required.

## Where the step order is defined

There is exactly one description of the workflow steps: the shipped step agents
under `src/lex_mcp_*/.github/agents/lex-step-NN.agent.md`, served to the
coordinator by `get_plan_step`. Read those, not a copy. Nothing in this folder
restates the step order, because a second copy only drifts.

## MCP step execution contract

- Step instructions are loaded from MCP via `get_plan_step`, never from local
  step files.
- Steps 0-19 are served by that single tool. The count is `TOTAL_PLAN_STEPS`
  (currently 20) in the mode's `wrapper_mcp.py`; completion mode starts at
  `COMPLETION_START_STEP` (currently 9).
- The IDE LLM is a **coordinator**. It calls `get_plan_step`, delegates to the
  matching `lex-step-NN` agent, and calls `notify_step_complete`. It does not do
  step work itself.
- Human approvals are not required to advance between steps.
- Deployment is out of scope for this execution mode and is handled separately
  when explicitly requested.

## How a forward run goes

1. Start from MCP (`kickstart_workflow` for a new project, `kickstart_run` for
   an existing one), then delegate each step to its `lex-step-NN` agent in order
   without pausing for approval gates.
2. Each step agent writes to `plans/technical_docs/step-NN-<name>.md`. The
   coordinator calls `notify_step_complete` after each step, then loads the next.
3. Continue until every step is complete in the same prompt execution.
4. Store all generated artifacts only under `plans/technical_docs/`.
5. Call `finalize_workflow` to merge the workflow branch.

## Run log

`plans/technical_docs/run.md` is the run log: the single place step status,
decisions, and open questions are recorded for a whole run.

**The server creates and maintains it — do not copy the template over it.**
`_write_run_log` seeds it at kickstart and resume, and `_tick_run_log_step`
ticks each box from `notify_step_complete`. Both live in the forward and
completion `wrapper_mcp.py`. An existing log is never overwritten, because its
ticks are the state a resumed run depends on.

It is machine-read. `_check_run_md_status_board` requires a literal
`## Step Status Board` heading and treats a run as complete only when every
`- [ ]` in that section is ticked, which gates the switch to backward mode for
resumed runs. The heading text and checkbox format must survive any hand-edit;
record decisions and answers freely in the other sections.

The board is generated from the same step mapping `get_plan_step` dispatches
from, so it cannot drift from the shipped agents. The two boards differ by
design: forward covers steps 0-19, completion covers only
`COMPLETION_START_STEP`-19 because it inherits the earlier steps from the
preceding MVP run. [runs/run-template.md](runs/run-template.md) documents the
shape; it is not something to copy by hand.

## Core system assumptions

- Target code is Python and Django-ish.
- Lex consumes generated files and assembles project structure.
- Django ORM models are the primary source of truth for data and behavior
  boundaries.
- Business logic must be implementable from pseudocode with minimal ambiguity.

## Lex ground-truth rule

Always apply
[lex_topics/20-LEX-SPECIFICATIONS.md](lex_topics/20-LEX-SPECIFICATIONS.md).
Where this handbook contradicts prior model knowledge, the handbook wins. Lex
here is an implementation platform and framework contract, not a standalone
traceability requirement policy.
