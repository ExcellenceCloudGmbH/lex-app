# Lex Specifications (Canonical, Project-Specific)

This document is the canonical Lex rule set for this repository.

## Lex purpose

- Lex is the base framework contract for generated project code.
- Project models extend Lex primitives and generated code is assembled into a Django-style project structure.
- Lex is an implementation platform, not a standalone audit/traceability requirement policy.

## A) Core class contracts

- Project entity models should extend `LexModel` instead of raw Django model base.
- Business processing/upload/report classes should extend `CalculationModel`.
- `CalculationModel.calculate()` is mandatory for `CalculationModel`-based classes.
- `CalculationModel` internals should not be re-implemented; only `calculate()` is the intended override point.
- `CalculationModel` status lifecycle includes `IN_PROGRESS`, `ERROR`, `SUCCESS`, `NOT_CALCULATED`, `ABORTED` via `is_calculated`.
- Calculation hook behavior is lifecycle-driven (create/update hooks trigger calculation flow).

## B) Field and schema rules

- Every model should include explicit PK format: `id = models.AutoField(primary_key=True)`.
- Any field ending with `Id` must be `ForeignKey`, never `IntegerField`.
- Relationships are mandatory and should be modeled with `ForeignKey` fields.
- Use direct class references in `ForeignKey` definitions (not string-based references).
- Never use module-style relation strings (forbidden examples: `"Inputs.Config.CompensationPeriod"`, `"Uploads.PeopleFluent.PeopleFluentUpload"`) instead just use the model name (correct examples: `"CompensationPeriod"`, `"PeopleFluentUpload"`).
- Avoid adding extra/useless ID fields not present/justified by data samples.
- For multiple ID-like fields, each must be justified against actual input columns.
- Use only column names that actually appear in input/output samples.

## C) Upload/report model nuances (critical)

- In every upload model, file field is mandatory.
- In every report model, at least one Django `FileField` must exist (field can be optional in null/blank behavior, but the report model must define a file field).
- Upload/report processing is expected through `CalculationModel` and `calculate()`.
- Report models should mirror output-file semantics and extension constraints.
- Report models should include only fields necessary for required report outputs.

## D) Required folder architecture (critical)

- Create and use three top-level functional folders: `Inputs`, `Uploads`, `Reports`.
- `Inputs`: stores transformed/normalized data models populated by upload processing.
- `Uploads`: contains model classes that accept input files and transform/load data into `Inputs` models.
- `Uploads` models may inherit `CalculationModel` when transformation/loading logic is required.
- `Reports`: contains report-generation models.
- `Reports` models must inherit `CalculationModel` and define at least one Django `FileField` for generated report artifacts.
- Any files where helper/ non-model classes are implemented must start with an underscore in their name (eg. `_period_serice.py`).

## E) File generation behavior (project-specific override)

- This project is CSV-first.
- Focus only on `.csv` input/output handling for now.
- Do not add non-CSV format requirements unless user explicitly requests them.

## F) Logging behavior

- Logging should use `LexLogger` (`from lex.audit_logging.handlers.LexLogger import LexLogger`).
- `LexLogger` uses a builder pattern: chain `.add_text()`, `.add_heading()`, `.add_table()`, `.add_dataframe()`, `.add_code()` methods, then call `.log()` to persist.
- Logging is scoped to `CalculationModel` / `calculate()` contexts and is context-aware (automatically links to the current calculation and model instance).
- Key operations in business logic should be logged with `LexLogger`.
- For nested calculations, use `model_logging_context` to preserve the parent/child log hierarchy.
- See `docs/lex_topics/11-logging-and-lexlogger.md` for the full API reference.

## G) Code-generation output contracts

- Response format starts with `### path/to/file.py` followed by class code.
- One class per file.
- No placeholders / `pass` for production implementations.
- Strong import discipline: include all required imports and correct project import paths.
- Import targets must resolve to existing modules/classes; generated code must not reference non-existent modules or stale renamed files.
- Respect project constraints including `No class Meta` and `No self.is_calculated usage` in generated code where required by prompt contract.
- All of the modules from Lex must be imported from this import pool and nowhere else:
	from lex.core.models.LexModel import LexModel
	from lex.core.models.CalculationModel import CalculationModel
	from lex.audit_logging.handlers.LexLogger import LexLogger
	from lex.audit_logging.utils.ModelContext import model_logging_context
- Imports of files inside of the created project, do not need to have the project name in front of them (wrong: `project.Inputs.calc`, correct `Inputs.calc`)

## H) Data-model synthesis / structure-generation rules

- Input files are the primary source for entity model extraction.
- One distinct input model per unique input-column schema.
- Multiple files share a model only when schema is identical.
- Normalize schema and reduce redundancy through relationships.
- Relationships should be represented as fields, not separate relationship classes.
- Naming conventions: CapitalCase class/folder names; field naming according to active project contract.
- Translate non-English terms to English for model/field naming.
- Single-level folder hierarchy for generated structure where specified.
- Structure/model-stage output must be parsable JSON only (no markdown fences).

## I) Implementation boundary (project-specific)

- Implementation phase must deliver project code, not only implementation plans.
- Do not generate Django project scaffolding files as deliverables for this phase.
- Do not introduce `apps.py`, `urls.py`, `settings.py`, or similar Django project-bootstrap artifacts unless the user explicitly requests them.

## J) Lex app runtime context (baked reference, critical)

- Canonical baked Lex runtime context file for this repository is: `docs/lex_topics/21-LEX-APP-CONTEXT.yaml`.
- This YAML is a docs-local copy of `metagpt/lex_app_context.yaml` and must be treated as authoritative context for framework-level behavior.
- Planning and implementation prompts should explicitly load this YAML whenever behavior/details of `LexModel`, `CalculationModel`, or logging patterns are needed.
- If any ambiguity exists between generated assumptions and framework lifecycle/permission behavior, resolve it by consulting this YAML first.
- Trace marker for retrieval: `LEX_APP_CONTEXT_SOURCE=docs/lex_topics/21-LEX-APP-CONTEXT.yaml`.

### J.1 Minimal in-file context trace (for prompt anchoring)

```yaml
LEX_APP_CONTEXT_SOURCE: docs/lex_topics/21-LEX-APP-CONTEXT.yaml
CONTAINS:
	- CalculationModel lifecycle/status semantics
	- LexModel validation/permission hooks
	- Logger usage/logging patterns
USAGE_RULE:
	- Load before implementation when framework internals are referenced
```

## K) Mandatory implementation sequencing rule (project-specific)

- Implementation workflow must include explicit plan-validation and code-validation gates.
- Step 9 validates implementation plans against this Lex specification before coding starts.
- Step 10 is the mandatory full project code-delivery step where the LLM realizes the project by writing complete code artifacts.
- Step 11 is a second mandatory compliance gate that validates generated code itself against this Lex specification.
- Do not treat implementation as complete unless Step 10 code delivery and Step 11 code-level compliance both pass with explicit approval.

## L) Import, services, and dependency-safety rules (critical)

- Models should not import service modules unless explicitly required by the framework hook contract; prefer service-layer orchestration to reduce dependency cycles.
- Prevent circular imports by including the necessary imports inside of the functions that they are needed in. 
- Add and enforce import integrity checks in validation gates to catch unresolved imports, stale module paths, and relation target mistakes before approval.

## Enforcement rule

- Treat this file as authoritative for planning and implementation prompts in this repository.
