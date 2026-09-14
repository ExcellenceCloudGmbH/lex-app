---
title: Logging
aliases:
  - "features/processing/logging"
---

Lex App provides `LexLogger`, a builder-pattern logging API that produces rich, Markdown-formatted log entries. It supports text, headings, tables, DataFrames, code blocks, and more — all stored in the database and displayed in the frontend.

LexLogger is context-aware: it automatically links log entries to the correct calculation, model instance, and parent/child hierarchy without any manual ID passing.

## Basic Usage

Chain builder methods together, then call `.log()` to save:

```python
from lex.audit_logging.handlers.LexLogger import LexLogger

def calculate(self):
    LexLogger().add_text("Processing started").log()

    # Rich formatting with heading
    LexLogger().add_heading("Invoice Summary", level=2) \
               .add_text("Processing completed successfully.") \
               .log()
```

> [!warning]
> Always call `.log()` at the end of your chain. Without it, nothing is written to the database.

## Tables and DataFrames

```python
# Markdown table
headers = ["Invoice ID", "Amount", "Status"]
rows = [
    ["INV-001", "500.00", "Paid"],
    ["INV-002", "1200.00", "Pending"],
]

LexLogger().add_heading("Invoice Summary") \
           .add_table(headers, rows) \
           .log()
```

```python
# Pandas DataFrame
import pandas as pd

df = pd.DataFrame({
    'Quarter': ['Q1', 'Q2', 'Q3', 'Q4'],
    'Revenue': [100000, 120000, 115000, 130000]
})

LexLogger().add_text("Quarterly Revenue Report:") \
           .add_dataframe(df) \
           .log()
```

## Code and JSON

```python
import json

config = {"tax_rate": 0.19, "currency": "EUR"}

LexLogger().add_text("Current Configuration:") \
           .add_code(json.dumps(config, indent=2), language="json") \
           .log()
```

## Context-Aware Logging

LexLogger automatically resolves the current execution context. You don't need to pass IDs manually — it figures out which calculation is running and which model instance is executing.

## Nested Calculations

When a parent calculation triggers a child, use `model_logging_context` to maintain the log hierarchy:

```python
from lex.audit_logging.utils.ModelContext import model_logging_context


class ParentCalculation(CalculationModel):
    def calculate(self):
        LexLogger().add_text("Starting parent").log()

        child = CalculateNAV.objects.filter(quarter=self.quarter).first()
        with model_logging_context(child):
            child.is_calculated = "IN_PROGRESS"
            child.save()

        LexLogger().add_text("Child finished.").log()
```

This ensures logs from the child appear nested under the parent in the frontend.

## Grouping logs into sections

A long calculation is easier to follow when its log reads like a document — with a
title for each phase of the work. Pass a plain **string** to `model_logging_context`
and everything logged inside that block is grouped under a titled section, no backing
model required:

```python
def calculate(self):
    with model_logging_context("Data collection"):
        LexLogger().add_text("Loaded 1,240 investor positions.").log()

        with model_logging_context("Validation"):
            LexLogger().add_list(["Schemas OK", "No missing funds"]).log()

    with model_logging_context("Aggregation"):
        LexLogger().add_text("Rolled positions up to the fund level.").log()
```

Each title becomes its own node in the execution tree, and sections nest freely — inside
one another and around child calculations. The result is a table of contents for the run:

```
Investor Track Record
├─ Data collection
│  └─ Validation
└─ Aggregation
```

A few things worth knowing:

- **Sections that never log anything are skipped.** If a block produces no output, it
  simply doesn't appear in the tree — so you can wrap optional work in a section without
  cluttering the log when it does nothing.
- **Re-entering the same title continues the same section.** Opening
  `model_logging_context("Validation")` twice under the same parent appends to one node
  rather than creating a duplicate.
- **Headings only shape the tree.** They don't change which record a log belongs to, so
  live streaming and the calculation's status are unaffected — a section is purely a way
  to organise what you write.

> [!tip]
> Reach for a **string** context to structure *one* calculation's own log into phases,
> and a **model instance** context (above) to nest a *child calculation's* logs under
> their parent. They compose: a model section can contain string sections, and vice versa.

For the complete method list, see the [[reference/LexLogger API|LexLogger API reference]].

> [!note]- Migrating from V1?
> If you're coming from `CalculationLog.create()`:
>
> | Aspect | V1 (Old) | Current |
> |---|---|---|
> | API | `CalculationLog.create(...)` | `LexLogger()` builder pattern |
> | Formatting | Plain text only | Rich Markdown |
> | Context | Manual — pass IDs yourself | Automatic |
> | Nested calculations | Not supported | Built-in parent/child hierarchy |
>
> Replace all `CalculationLog.create(...)` calls with `LexLogger()`, remove manual context/ID passing, and always end chains with `.log()`.

## In the Frontend

LexLogger output is rendered in the frontend in real-time:

- **Calculation Log Panel** — a slide-out drawer during calculation showing live Markdown-rendered output as the calculation progresses, including background calculations after an initial HTTP `202` response
- **Execution tree** — the left pane lists every node — model instances *and* the string sections above — so you can click straight to the part of the log you care about
- **Collapsible sections** — in the consolidated log, any section can be folded away; collapsing a heading hides its whole sub-tree, so you can focus on one phase of a long run at a time
- **PDF Export** — the calculation log for any record can be exported as a PDF that renders just like the on-screen view: headings, tables, fenced code blocks and even strikethrough survive the export, which makes it usable as compliance evidence
- **Complete subtree export** — download a log together with all of its nested child logs as one PDF by adding `include_descendants=true` to the download request
- **Rich Rendering** — headings, tables, DataFrames, and code blocks are all rendered with proper formatting and syntax highlighting

See the [[using-the-app/record-detail/index|Record Detail]] page for how logs appear in context.
