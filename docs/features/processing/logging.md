---
title: Logging
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

- **Calculation Log Panel** — a slide-out drawer during calculation showing live Markdown-rendered output as the calculation progresses
- **Hierarchical Log Tree** — when a parent calculation triggers children, logs are nested in a tree view showing the full execution hierarchy
- **PDF Export** — the calculation log for any record can be exported as a PDF, useful for compliance documentation and auditing
- **Rich Rendering** — headings, tables, DataFrames, and code blocks are all rendered with proper formatting and syntax highlighting

See the [[interface/record-detail/index|Record Detail]] page for how logs appear in context.
