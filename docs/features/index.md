---
title: Building Blocks
---

Lex App provides a set of building blocks that you compose to create your application. Each one solves a specific problem — you only use what you need. They're organized into four groups that mirror how data flows through your app.

## The ETL Pipeline

Every Lex App project follows the Extract → Transform → Load pattern. Your folder structure reflects this:

| Stage | Folder | What Lives Here |
|---|---|---|
| **Extract** | `Upload/` | `CalculationModel` subclasses that ingest CSVs, Excel files, API data |
| **Transform** | `Input/` | Input models — your core business entities and domain logic |
| **Load** | `Reports/` | `CalculationModel` subclasses that compute summaries and analytics |

## Building Blocks by Category

### [[features/data-pipeline/index|Data Pipeline]]
How data enters and is structured — [[features/data-pipeline/serializers|serializers]], [[features/data-pipeline/lifecycle hooks|lifecycle hooks]], and [[features/data-pipeline/model structure|model structure]].

### [[features/processing/index|Processing]]
How business logic runs — [[features/processing/calculations|calculations]], [[features/processing/celery and async calculations|Celery async dispatch]], and [[features/processing/logging|LexLogger]] for rich calculation output.

### [[features/tracking/index|Tracking & Audit]]
Every action leaves a trail — [[features/tracking/audit logs|audit logs]] for operation tracking and [[features/tracking/bitemporal history|bitemporal history]] for data over time.

### [[features/access-and-ui/index|Access & UI]]
Who sees what and how it's presented — [[features/access-and-ui/permissions|permissions]] and [[features/access-and-ui/streamlit dashboards|Streamlit dashboards]].
