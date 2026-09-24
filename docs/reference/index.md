---
title: "Framework Reference"
---

Quick-lookup reference for Lex App development. All source code is available on [GitHub](https://github.com/ExcellenceCloudGmbH/lex-app).

The sections below group these pages by what they are. If you know the question
but not the page, this groups them by what you are trying to find out:

```mermaid
flowchart TB
    Q{"What are you<br/>looking up?"}
    Q --> M["A model's own behaviour"]
    Q --> C["Something that computes"]
    Q --> S["A setting"]
    Q --> V["A V1 name"]

    M --> M1["<b>LexModel Internals</b><br/><i>fields, hooks, permissions</i>"]
    C --> C1["<b>CalculationModel Internals</b><br/><i>one record recomputes</i>"]
    C --> C2["<b>CalculatedModelMixin Internals</b><br/><i>many combinations do</i>"]
    C --> C3["<b>LexLogger API</b><br/><i>what it writes while it runs</i>"]
    S --> S1["<b>lex_config.py</b><br/><i>set in the project</i>"]
    S --> S2["<b>Environment Variables</b><br/><i>set in .env</i>"]
    V --> V1["<b>V1 to V2 Import Map</b>"]
```

`CLI Commands` and `Report File Fields` sit outside that split — the first is
every `lex` command, the second is what a report model writes to disk.

## Core Classes

- [[reference/LexModel Internals|LexModel Internals]] — fields, lifecycle hooks, permissions, and how the base model works
- [[reference/CalculationModel Internals|CalculationModel Internals]] — the state machine, `calculate()`, and async dispatch
- [[reference/CalculatedModelMixin Internals|CalculatedModelMixin Internals]] — the combination engine, `defining_fields`, parallel dispatch

## APIs & Tools

- [[reference/LexLogger API|LexLogger API]] — every `LexLogger` method with examples
- [[reference/CLI Commands|CLI Commands]] — every `lex` command at a glance
- [[reference/Report File Fields|Report File Fields]] — generated Excel and PDF files on report models

## Configuration

- [[reference/lex_config|lex_config.py]] — the project-wide settings file: `INITIAL_DATA`, `PROJECT_GROUPS`, `TAB_DISPLAY_NAMES`, `DEFAULT_SERIALIZER_NAME`
- [[reference/Environment Variables|Environment Variables]] — runtime variables the framework reads from `.env`

## Migration

- [[reference/V1 to V2 Import Map|V1 → V2 Import Map]] — complete import replacement table for migration
