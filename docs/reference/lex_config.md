---
title: "lex_config.py — project settings"
---

`lex_config.py` lives at your project root and holds project-wide settings the framework reads at boot. It's a plain Python module — the framework imports it once and pulls the keys it cares about. Most projects only set two or three of these.

> [!note]
> This page is a one-stop index. The individual feature pages linked from each section are the place to go for examples and edge cases.

## Keys at a glance

| Key                       | Purpose                                                                              | Documented in                                                                  |
| ------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ |
| `INITIAL_DATA`            | Path to the JSON file the framework loads on `lex Init` to seed your database        | [[features/data-pipeline/initial data]]                                        |
| `PROJECT_GROUPS`          | List of [Keycloak](https://www.keycloak.org/documentation) group names to create on `lex Init` | [[tutorial/Part 4 — Validation & Permissions]], [[features/access-and-ui/permissions]] |
| `TAB_DISPLAY_NAMES`       | Friendly labels for the tabs in the record-detail view                               | [[interface/record-detail/index]]                                              |
| `DEFAULT_SERIALIZER_NAME` | Name of the serializer the framework picks when no explicit one is requested         | [[features/data-pipeline/serializers]]                                         |

## `INITIAL_DATA`

```python title="lex_config.py"
INITIAL_DATA = "Tests/test_data.json"
```

The path (relative to the project root) of the JSON fixture loaded when you run `lex Init` or `lex create_db`. Use it to seed reference data — categories, lookup tables, demo records — so a fresh database isn't empty. See [[features/data-pipeline/initial data]] for the file format and the bulk-load behaviour.

## `PROJECT_GROUPS`

```python title="lex_config.py"
PROJECT_GROUPS = ["team_budget", "finance", "hr_manager"]
```

A flat list of [Keycloak](https://www.keycloak.org/documentation) group names. On `lex Init`, the framework makes sure each group exists in the configured Keycloak realm so your permission methods can check membership via `user_context.groups`. You don't assign users to groups here — that happens in the Keycloak admin UI or via your IdP — `PROJECT_GROUPS` just guarantees the groups exist.

## `TAB_DISPLAY_NAMES`

```python title="lex_config.py"
TAB_DISPLAY_NAMES = {
    "summary": "Overview",
    "timeline": "Change history",
}
```

A mapping from internal tab keys to the labels shown in the record-detail view. Tabs you don't list keep their default label. See [[interface/record-detail/index]] for the list of tab keys you can override.

## `DEFAULT_SERIALIZER_NAME`

```python title="lex_config.py"
DEFAULT_SERIALIZER_NAME = "compact"
```

The serializer the framework reaches for when an API request doesn't ask for one explicitly. Defaults to the framework's built-in full serializer if unset. The History tables pick the override up automatically, so a change here affects both live and historical views. See [[features/data-pipeline/serializers]] for the full mechanic.

## Where it fits in the project

`lex_config.py` is generated for you by `lex setup` and sits at the project root alongside `manage.py`, `lex_app/`, and your model packages. See [[project structure|Project structure]] for the full layout.

