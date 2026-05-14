# Showcase CI — Business Guide

> **Audience:** Business stakeholders, product owners, customers, non-engineer reviewers
> **Workflow:** [`.github/workflows/showcase_tests.yml`](../../.github/workflows/showcase_tests.yml)
> **Report builder:** [`.github/scripts/build_showcase_report.py`](../../.github/scripts/build_showcase_report.py)
> **Email sender:** [`.github/scripts/send_showcase_email.py`](../../.github/scripts/send_showcase_email.py)

---

## What this is

**Showcase Tests (Business View)** is a CI workflow that runs **two hand-picked tests** from the LEX framework and produces a **customer-facing Platform Health Report** in two complementary layouts:

- **HTML email body** — compact headline layout. Big verdict at the top, one expanded card per **failing** capability, passing capabilities listed as one-line rows. Stays short no matter how many tests we eventually add.
- **PDF attachment** — the full archival version. One detailed card per test plus the _"What we test and why"_ glossary explaining the capabilities in customer terms.

The report is branded with the Excellence Cloud logo (navy `#283067` + teal `#24b6bb`) and designed for people who do not read CI logs.

No tracebacks, no dotted test paths, no jargon.

This workflow does **not** replace [`django_tests.yml`](../../.github/workflows/django_tests.yml) (the full release gate). It is a narrow "does the product do what we promise?" check.

---

## What the two tests prove

| # | Capability | What passing means | What failing means |
|---|------------|--------------------|--------------------|
| 1 | **Project initialisation** | When a customer presses **Init** on a new project, the platform detects their data model, generates migrations, applies them, and registers the project for access management — all in one step. | New customers cannot reliably onboard. A project may be left half-configured. |
| 2 | **Create a record through the public API** | A record posted to the public REST API is accepted, stored, and retrievable. | Any customer-facing flow that creates data is broken — from forms to integrations to data loaders. |

The test ids behind these capabilities are hidden from the report itself but are tracked in the test plan:

- Scenario **1.6b** — `lex.test_project.tests.init.test_1b_lex_init.TestCluster01b_LexInit.test_1_6b_init_runs_full_pipeline`
- Scenario **2.1** — `lex.test_project.tests.crud_api.test_2a_create.TestCluster02a_Create.test_2_1_post_creates_record`

---

## When the workflow runs

- **On demand** — anyone with repo access can click _Actions → Showcase Tests (Business View) → Run workflow_.
- **Automatically on every merge to the default branch** (`lex-app-v2`) — so the most recent report always reflects the current production-candidate code.

It deliberately does **not** run on pull requests. PR gating is `django_tests.yml`'s job.

---

## Configuration

### Required secrets

| Secret | Purpose |
|---|---|
| `SENDGRID_API_KEY` | SendGrid API key used to send the report. |
| `SHOWCASE_REPORT_RECIPIENTS` | Comma-separated list of recipient email addresses. |
| `SHOWCASE_REPORT_FROM` | A **SendGrid-verified** sender email address. |

### Optional

| Key | Kind | Purpose |
|---|---|---|
| `SHOWCASE_REPORT_FROM_NAME` | secret | Display name for the sender (defaults to _"Platform Health"_). |
| `SHOWCASE_BRAND` | repo variable | Brand name shown in the report header (defaults to _"LEX Platform"_). |

### Behaviour when secrets are missing

If any required secret is missing, the email step **logs a clear "skipping email" message and exits 0** — the HTML + PDF are still produced and uploaded as a workflow artefact. The workflow does not fail just because SendGrid is not configured. This lets the pipeline run end-to-end during setup, with email switched on only when the secrets land.

---

## How to read the result

A stakeholder typically reads the report in their inbox — the email body is the report. The attached PDF is identical and is meant for forwarding, archiving, or printing.

If they want the raw run, the workflow run page on GitHub has:

- A quiet engineer-only summary on the **Summary** tab (pass/fail for each test).
- The HTML + PDF attached as a run artefact named **`platform-health-report`**.
- Full logs under each step.

---

## What to do when the report is red

1. Read the body — it tells you in plain English what is broken.
2. Flag the run to engineering, ideally by forwarding the email or sending a link to the run.
3. Engineering can look at the step logs to get the raw test output and dotted test ids.

A red report means a customer-visible promise is broken. It should be taken seriously, but it does not automatically block releases (that's `django_tests.yml`'s job).

---

## For engineers — changing the showcase set

Everything a stakeholder sees is driven from two files:

- **Business copy** (labels, descriptions, "what it proves" / "what it means if broken" paragraphs) — the `CAPABILITIES` mapping at the top of `build_showcase_report.py`.
- **Test selection** — the two `lex test …` invocations in `.github/workflows/showcase_tests.yml`.

To swap one of the tests, update **both** places so the copy stays in sync with what's actually running. To add a third showcase, extend the mapping, add a third `test_<name>` step, and pass the extra outcome/duration into the report builder.

Keep the set small (2–3). The whole point is that a non-engineer can read it.

### Scaling to more tests

The email layout is designed to stay short as the showcase set grows:

- **Passing capabilities** are rendered as one-line rows in a "Working" group — negligible vertical space per test.
- **Failing capabilities** are rendered as expanded cards in a separate "Needs attention" group above the passes, so the reader's eye lands on what needs action first.

The PDF has no such constraint — every test gets a full detailed card plus the glossary entry.

### Local dev

```bash
# HTML only (no pango install needed)
python .github/scripts/build_showcase_report.py \
  --init-outcome success --init-duration 0.45 \
  --crud-outcome success --crud-duration 0.28 \
  --out-html report.html --skip-pdf

# With PDF (requires WeasyPrint + pango + cairosvg for the logo)
pip install weasyprint cairosvg
python .github/scripts/build_showcase_report.py \
  --init-outcome success --init-duration 0.45 \
  --crud-outcome failure --crud-duration 0.31 \
  --out-html report.html --out-pdf report.pdf
```

