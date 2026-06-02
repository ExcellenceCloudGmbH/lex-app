---
title: LexLogger API
---

`LexLogger` uses a builder pattern — chain methods together and call `.log()` at the end. Every method returns the logger instance, so you can chain as many as you need.

## Core Methods

### `add_text(text: str)`

Adds a plain text paragraph.

```python
LexLogger().add_text("Processing complete.").log()
```

### `add_heading(text: str, level: int = 1)`

Adds a Markdown heading (levels 1–6).

```python
LexLogger().add_heading("Summary", level=2) \
           .add_text("All items processed.") \
           .log()
```

### `add_table(headers: list, rows: list)`

Adds a Markdown table.

```python
LexLogger().add_table(
    ["Name", "Amount"],
    [["Alice", "500"], ["Bob", "750"]]
).log()
```

### `add_dataframe(df: pd.DataFrame)`

Renders a Pandas DataFrame as a Markdown table.

```python
import pandas as pd
df = pd.DataFrame({"Q": ["Q1", "Q2"], "Revenue": [100000, 120000]})
LexLogger().add_dataframe(df).log()
```

### `add_code(code: str, language: str = "")`

Adds a fenced code block.

```python
import json
config = {"rate": 0.19}
LexLogger().add_code(json.dumps(config, indent=2), language="json").log()
```

### `log()`

Writes the accumulated content to the database. **Always call this last.**

> [!warning]
> If you forget to call `.log()`, nothing is written. This is the most common mistake.

## Context-Aware Logging

LexLogger automatically resolves:

| Context | How |
|---|---|
| **Calculation ID** | Links to the current `CalculationLog` entry |
| **Model Instance** | Identifies which model is executing |
| **Parent/Child** | Links nested calculations hierarchically |

You never need to pass context manually.

## Nested Calculations

When a parent calculation triggers a child, wrap the child execution in `model_logging_context` to preserve the log hierarchy:

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

See [[features/processing/logging]] for more examples and usage patterns.
