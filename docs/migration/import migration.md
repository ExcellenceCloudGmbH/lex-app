---
title: Import Migration
---

> [!note]
> This guide is for teams migrating from V1 (`generic_app`). If you're starting a new project, you can skip this.

Updating your imports is typically the first step in migrating from V1. It's mostly a find-and-replace operation — here's how to do it systematically.

## What You Need to Do

Every V1 file starts with imports from `generic_app`. These need to change to the current `lex.*` and `django.*` paths. The good news: most of these are simple find-and-replace operations.

## Step-by-Step

### 1. Replace Model Imports

Find all files that import from `generic_app`:

```bash
grep -rn "from generic_app" --include="*.py" .
```

Then replace:

```python
# Before
from generic_app import models
from generic_app.models import *

# After
from django.db import models
from lex.core.models.LexModel import LexModel
```

### 2. Replace Calculation Imports

```python
# Before
from generic_app.generic_models.upload_model import ConditionalUpdateMixin

# After
from lex.core.models.CalculationModel import CalculationModel
```

### 3. Replace Logging Imports

```python
# Before
from generic_app.submodels.CalculationLog import CalculationLog

# After
from lex.audit_logging.handlers.LexLogger import LexLogger
```

### 4. Replace Permission Imports

```python
# Before
from generic_app.generic_models.ModelModificationRestriction import ModelModificationRestriction

# After
from lex.core.models.LexModel import UserContext, PermissionResult
```

### 5. Add Lifecycle Hook Imports (if needed)

V1 had no explicit hook imports. If your models used `UploadModelMixin`, you'll need:

```python
from django_lifecycle import hook, AFTER_CREATE
```

For the complete import replacement table, see the [[reference/V1 to V2 Import Map|V1 → V2 Import Map]].

## Verification

After replacing all imports, check that nothing was missed:

```bash
# Should return zero results
grep -rn "from generic_app" --include="*.py" .
grep -rn "import generic_app" --include="*.py" .
```

## Migration Checklist

- [ ] Replace all `from generic_app import models` → `from django.db import models`
- [ ] Replace all `from generic_app.models import *` → explicit imports
- [ ] Replace `ConditionalUpdateMixin` → `CalculationModel`
- [ ] Replace `UploadModelMixin` → `LexModel` + `@hook` decorators
- [ ] Replace `CalculationLog.create()` → `LexLogger()`
- [ ] Replace `ModelModificationRestriction` → `permission_*` methods
- [ ] Verify: `grep -rn "from generic_app" --include="*.py" .` returns nothing
