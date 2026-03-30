# Logging & LexLogger

Search keywords: LexLogger, add_text, add_heading, add_table, add_dataframe, calculation logging, model_logging_context

## Scope

- Rich Markdown-formatted logging during calculations
- Builder-pattern API for structured log output
- Context-aware logging with automatic calculation/model linking
- Nested calculation log hierarchy

## Key Points

- `LexLogger` is Lex's built-in logging API for producing rich, Markdown-formatted log entries during calculations.
- Uses a builder pattern: chain methods together, then call `.log()` to save to the database.
- Context-aware: automatically links log entries to the correct calculation, model instance, and parent/child hierarchy without manual ID passing.
- Output is rendered in the frontend calculation log panel in real-time.

## Import

```python
from lex.audit_logging.handlers.LexLogger import LexLogger
```

## Core API

### `add_text(text: str)`
Adds a plain text paragraph.

```python
LexLogger().add_text("Processing started").log()
```

### `add_heading(text: str, level: int = 1)`
Adds a Markdown heading (levels 1–6).

```python
LexLogger().add_heading("Invoice Summary", level=2) \
           .add_text("Processing completed successfully.") \
           .log()
```

### `add_table(headers: list, rows: list)`
Adds a Markdown table.

```python
headers = ["Invoice ID", "Amount", "Status"]
rows = [
    ["INV-001", "500.00", "Paid"],
    ["INV-002", "1200.00", "Pending"],
]
LexLogger().add_heading("Invoice Summary") \
           .add_table(headers, rows) \
           .log()
```

### `add_dataframe(df: pd.DataFrame)`
Renders a Pandas DataFrame as a Markdown table.

```python
import pandas as pd
df = pd.DataFrame({
    'Quarter': ['Q1', 'Q2', 'Q3', 'Q4'],
    'Revenue': [100000, 120000, 115000, 130000]
})
LexLogger().add_text("Quarterly Revenue Report:") \
           .add_dataframe(df) \
           .log()
```

### `add_code(code: str, language: str = "")`
Adds a fenced code block.

```python
import json
config = {"tax_rate": 0.19, "currency": "EUR"}
LexLogger().add_text("Current Configuration:") \
           .add_code(json.dumps(config, indent=2), language="json") \
           .log()
```

### `log()`
Writes the accumulated content to the database. **Always call this last.**

> **Warning:** If you forget `.log()`, nothing is written.

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

This ensures logs from the child appear nested under the parent in the calculation log.

## Where to Expand

- `lex_context.md`: logger usage and logging patterns
- `lex_context_repo.md`: logging details

## LLM Prompt Starters

- "Add LexLogger-based step logging to this calculation flow with headings and tables."
- "Add structured LexLogger checkpoints for this `calculate()` flow with DataFrames."
