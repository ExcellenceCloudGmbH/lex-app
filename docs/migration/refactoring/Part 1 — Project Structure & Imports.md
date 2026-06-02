---
title: "Part 1 — Project Structure & Imports"
---

The first step in migrating from V1 is getting your project into the right shape. V1 projects typically use nested Django app folders and import everything from `generic_app`. You'll strip out the Django boilerplate and update every import to the current `lex.*` paths.

## Flatten the Project Structure

V1 projects often look like this — a standard Django app layout:

```
MyProject/
├── manage.py
├── my_app/
│   ├── __init__.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── Fund.py
│   │   ├── Quarter.py
│   │   └── Investment.py
│   ├── admin.py
│   └── views.py
└── requirements.txt
```

Lex App doesn't use Django's app scaffolding. There's no `manage.py`, no `admin.py`, no `views.py`. Your models go into three ETL folders:

```
MyProject/
├── .env
├── .run/
├── migrations/
├── Input/
│   ├── __init__.py
│   ├── Fund.py
│   ├── Quarter.py
│   └── Investment.py
├── Upload/
│   ├── __init__.py
│   ├── FundUpload.py
│   └── InvestmentUpload.py
└── Reports/
    ├── __init__.py
    └── CalculateNAV.py
└── requirements.txt
```

### How to Flatten

1. Move your model files out of the nested app folder
2. Organize them at the root or into meaningful subfolders
3. Delete `manage.py`, `admin.py`, `views.py`, and any empty `__init__.py` files
4. Delete the old app directory
5. Run `lex setup` to generate `.env`, `.run/`, and `migrations/`

```bash
# Example commands
mv my_app/models/*.py ./
rm manage.py
rm -rf my_app/
lex setup
```

> [!warning]
> Don't delete your existing `migrations/` folder — you'll need it to run Django migrations after refactoring.

## Update All Imports

This is the most mechanical part of the migration. Every file that imports from `generic_app` needs to be updated.

### Find All Files to Update

```bash
grep -rn "from generic_app" --include="*.py" .
grep -rn "import generic_app" --include="*.py" .
```

### Core Model Imports

```python
# Before
from generic_app import models
from generic_app.models import *

# After
from django.db import models
from lex.core.models.LexModel import LexModel
```

### Calculation Imports

```python
# Before
from generic_app.generic_models.upload_model import ConditionalUpdateMixin

# After
from lex.core.models.CalculationModel import CalculationModel
```

### Lifecycle Hook Imports

V1 had no explicit hook imports — hooks were implicit via `UploadModelMixin`. Now you need to import them explicitly:

```python
# Before
from generic_app.generic_models.upload_model import UploadModelMixin

# After
from lex.core.models.LexModel import LexModel
from django_lifecycle import hook, AFTER_CREATE
```

### Logging Imports

```python
# Before
from generic_app.submodels.CalculationLog import CalculationLog

# After
from lex.audit_logging.handlers.LexLogger import LexLogger
```

### Permission Imports

```python
# Before
from generic_app.generic_models.ModelModificationRestriction import ModelModificationRestriction

# After
from lex.core.models.LexModel import UserContext, PermissionResult
```

> [!tip]
> For the complete replacement table, see the [[reference/V1 to V2 Import Map]].

## Update Internal References

Imports between your own models follow the folder structure:

```python
# Importing an input model from anywhere
from Input.Fund import Fund

# Importing an upload model
from Upload.FundUpload import FundUpload

# Importing a report model
from Reports.CalculateNAV import CalculateNAV
```

> [!tip]
> String references like `'Fund'` in ForeignKey fields are often the safest option during migration — they avoid circular import issues.

## Verify

Run these checks — both should return zero results:

```bash
grep -rn "from generic_app" --include="*.py" .
grep -rn "import generic_app" --include="*.py" .
```

## Checkpoint

- [ ] Django boilerplate removed (`manage.py`, `admin.py`, `views.py`)
- [ ] `lex setup` has been run (`.env`, `.run/`, `migrations/` exist)
- [ ] All `generic_app` imports replaced with `lex.*` / `django.*` imports
- [ ] Internal model references updated to match folder structure
- [ ] Zero `grep` results for `generic_app`

Next: [[migration/refactoring/Part 2 — Models & Fields|Part 2 — Models & Fields]].
