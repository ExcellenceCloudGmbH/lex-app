---
title: "V1 to V2 Import Map"
---

> [!note]
> This page is for teams migrating from V1 (`generic_app`). If you're starting a new project, just use the current import paths shown throughout the documentation.

A complete find-and-replace table for updating your imports.

## Core Models

| V1 Import | Current Import |
|---|---|
| `from generic_app import models` | `from django.db import models` |
| `from generic_app.models import *` | `from lex.core.models.LexModel import LexModel` |
| `from generic_app.generic_models.upload_model import ConditionalUpdateMixin` | `from lex.core.models.CalculationModel import CalculationModel` |
| `from generic_app.generic_models.upload_model import UploadModelMixin` | `from lex.core.models.LexModel import LexModel` + `from django_lifecycle import hook, AFTER_CREATE` |

## Permissions

| V1 Import | Current Import |
|---|---|
| `from generic_app.generic_models.ModelModificationRestriction import ModelModificationRestriction` | `from lex.core.models.LexModel import UserContext, PermissionResult` |

## Logging

| V1 Import | Current Import |
|---|---|
| `from generic_app.submodels.CalculationLog import CalculationLog` | `from lex.audit_logging.handlers.LexLogger import LexLogger` |

## Lifecycle Hooks

| V1 Import | Current Import |
|---|---|
| *(no equivalent — hooks were implicit)* | `from django_lifecycle import hook, AFTER_CREATE, BEFORE_UPDATE, ...` |
| *(no equivalent)* | `from django_lifecycle.conditions import WhenFieldValueIs, WhenFieldHasChanged` |

## Fields and Utilities

| V1 Import | Current Import |
|---|---|
| `from generic_app.generic_models.upload_model import IsCalculatedField` | *(removed — inherited from `CalculationModel`)* |
| `from generic_app.generic_models.upload_model import CalculateField` | *(removed — inherited from `CalculationModel`)* |

See [[migration/import migration]] for a full walkthrough of how to apply these changes.
