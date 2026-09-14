---
title: Start Here
---

Four steps, in order. The first three take about twenty minutes; the tutorial takes an afternoon and leaves you with a working application.

1. **[[start-here/installation|Installation]]** — install the package, run `lex setup`, point it at Keycloak, and get `lex init` to pass.
2. **[[start-here/project structure|Project Structure]]** — the `Upload/`, `Input/`, `Reports/` layout, and why the framework cares where a model lives.
3. **[[start-here/running your app|Running Your App]]** — start the server, from PyCharm or a terminal, and reach the interface.
4. **[[start-here/tutorial/index|The TeamBudget tutorial]]** — build a real application in six parts: models, calculations, validation, permissions, dashboards and history.

## Before you start

- **Python 3.12.** Not 3.11, not 3.13 — check with `python3.12 --version`.
- **Access to your project's repository.**
- **Access to [Excellence Cloud](https://excellence-cloud.de)** for the Keycloak client configuration. `lex init` needs it, and without it you can install the package but not run an application.

## What you will have at the end

A running application with your own models in it, a grid to edit them through, a calculation you wrote, and an audit trail of everything you did to it — none of which you had to build.

From there, each section of these docs covers one thing you might need next — [[model-your-data/index|modelling]], [[calculations/index|calculations]], [[history-and-audit/index|history]], [[access-and-dashboards/index|access and dashboards]]. Nothing is compulsory; take what your application actually calls for.
