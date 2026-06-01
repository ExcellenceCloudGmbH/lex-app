---
title: Project Structure
---

Lex App uses a flat project structure — no `manage.py`, no nested [Django](https://docs.djangoproject.com/) app folders, no `admin.py` or `views.py`. Your project follows the **ETL pattern**: Upload models for ingestion, Input models for domain logic, and Report models for output.

```
YourProject/
├── .env                        ← environment config (single source of truth)
├── .run/                       ← PyCharm run configurations (auto-generated)
│   ├── Init.run.xml
│   └── Start.run.xml
├── migrations/                 ← Django migrations
├── tests/
│   └── test_suite.py
├── model_structure.yaml        ← frontend sidebar layout (optional)
├── lex_config.py               ← framework settings (Celery, etc.)
├── requirements.txt
│
├── Upload/                     ← Extract: data ingestion models
│   ├── __init__.py
│   ├── UploadBalanceSheet.py
│   ├── UploadCashflow.py
│   └── serializers.py
│
├── Input/                      ← Transform: core business entities
│   ├── __init__.py
│   ├── Fund.py
│   ├── Quarter.py
│   └── Investment.py
│
└── Reports/                    ← Load: calculations & analytics
    ├── __init__.py
    ├── CalculateNAV.py
    └── InvestorTrackRecord.py
```

## The ETL Convention

| Folder | Purpose | Base Class | Example |
|---|---|---|---|
| `Upload/` | Ingest raw data (CSV, Excel) | `CalculationModel` | `UploadBalanceSheet` |
| `Input/` | Core business entities and domain logic | `LexModel` | `Fund`, `Investor` |
| `Reports/` | Compute summaries, analytics | `CalculationModel` | `CalculateNAV` |

This isn't enforced by the framework — you can organize however you like — but following this convention makes projects immediately understandable to anyone familiar with Lex App.

> [!tip]
> Larger projects often create additional subfolders within `Input/` for domain areas, like `Input/InvestmentStructure/`, `Input/Cashflows/`, or `Input/Valuation/`. The key is that imports always follow the folder structure: `from Input.Fund import Fund`.

## Imports Follow the Folder Structure

Each folder is a Python package. Import paths mirror the directory layout:

```python
from Input.Fund import Fund
from Upload.UploadBalanceSheet import UploadBalanceSheet
from Reports.CalculateNAV import CalculateNAV
```

## The `.env` File

The `.env` file is the single source of truth for all runtime configuration. It's loaded automatically by the PyCharm run configurations, or you can source it manually:

> [!note]- Terminal: loading .env manually
> ```bash
> set -a; source .env; set +a
> ```

See [[installation]] for how to configure it.

## Model Files

Each model is a standalone Python file. The framework discovers them automatically — you don't need to register them anywhere.

```python title="Input/Fund.py"
from lex.core.models.LexModel import LexModel
from django.db import models


class Fund(LexModel):
    name = models.CharField(max_length=200)
    department = models.CharField(max_length=200)
    budget = models.DecimalField(max_digits=12, decimal_places=2)
```

> [!tip]
> To organize models in the frontend sidebar, use a `model_structure.yaml` file. See [[features/data-pipeline/model structure]] for details.

## Key Dependencies

Lex App brings along a specific set of dependencies. Make sure your `requirements.txt` includes:

- `lex-app` — the framework itself
- `pandas`, `numpy` — you manage these versions yourself (not bundled)
- Any additional libraries your project needs

> [!warning]
> If you're upgrading from an older version, `pandas` and `numpy` are no longer bundled with `lex-app`. Add them explicitly to your `requirements.txt`.
