---
title: lex_view Callbacks & Selection
---

`lex_view()` embeds a page from the React frontend inside a Streamlit
dashboard. By default it is a one-way iframe. When you opt in to **callbacks**,
the embed becomes bidirectional: the React app emits events (record created,
record updated, AG Grid selection changed, navigation, flow step finished) and
your Streamlit script re-runs with the latest event as the return value of
`lex_view(...)`.

This document is the **contract** between the Python helper, the Streamlit
custom component, and the React app. Both sides implement against this spec —
keep them in sync.

## When callbacks are active

`lex_view()` runs in one of two modes:

| Mode | Trigger | Return value |
|---|---|---|
| **Plain iframe** (legacy) | No `on_*` flags, no `serializer`, no future bidirectional kwargs | `None` |
| **Bidirectional component** | At least one of `on_create`, `on_update`, `on_select`, `on_navigate`, `on_flow_step` is `True` | The latest event dict, or `None` on the first run |

Backwards compatible: existing call sites that don't opt in keep getting a
plain iframe.

## Transport

A small **Streamlit custom component** wraps the iframe. The React app posts
events with `window.parent.postMessage(envelope, targetOrigin)`. The component
listens for messages whose `data.source === "lex-app"`, validates the origin
against the resolved frontend base URL, and forwards the envelope to Python
via `Streamlit.setComponentValue(envelope)`. Streamlit re-runs the script and
`lex_view(...)` returns that envelope.

`postMessage` is the standard browser channel between an iframe and its
parent. There is no backend round-trip, no polling, and no new dependency.

## Envelope

Every event has the same shape:

```json
{
  "source":  "lex-app",
  "version": 1,
  "type":    "<event type>",
  "ts":      1717600000000,
  "id":      "01HV8…",
  "payload": { ... }
}
```

| Field | Type | Notes |
|---|---|---|
| `source` | `"lex-app"` | Discriminator. Messages without this are ignored. |
| `version` | `int` | Protocol version. Currently `1`. Bumped on breaking changes. |
| `type` | `str` | One of the event types below. |
| `ts` | `int` | Emit time, ms since epoch. |
| `id` | `str` | Unique event id (ULID). Lets the component dedupe re-runs. |
| `payload` | `object` | Type-specific data. Schemas below. |

Unknown `type` values are forwarded unchanged — Python user code can switch on
`type` without the component needing to know every variant.

## Event types

### `create`

Emitted after a successful create mutation.

```json
{ "type": "create",
  "payload": { "resource": "investor", "id": 42 } }
```

Opt in: `on_create=True`. Fires regardless of whether a `redirect_after_create`
or `flow` is configured.

### `update`

Emitted after a successful update mutation.

```json
{ "type": "update",
  "payload": { "resource": "investor", "id": 42 } }
```

Opt in: `on_update=True`.

### `select`

Emitted when the AG Grid selection changes inside a list view. Debounced
(150 ms) to avoid flooding Streamlit re-runs while the user shift-clicks.

```json
{ "type": "select",
  "payload": {
    "resource": "investor",
    "ids":  [42, 43],
    "rows": [
      { "id": 42, "name": "ACME", ... },
      { "id": 43, "name": "Globex", ... }
    ]
  } }
```

`rows` contains the row objects as the embedded list received them — i.e.
shaped by whatever serializer the list is currently using (see
[Serializer override](#serializer-override)). Use `ids` if you only need keys.

Opt in: `on_select=True`. The Python helper forwards this as
`?emit_select=true` and the React side wires the AG Grid `onSelectionChanged`
callback only when that flag is present.

### `navigate`

Emitted when the embedded React router changes route — useful for following a
flow without driving it.

```json
{ "type": "navigate",
  "payload": { "from": "/investor/create", "to": "/cashflow/42/edit" } }
```

Opt in: `on_navigate=True`.

### `flow_step`

Emitted after each step of a `flow` resolves. Lets Streamlit show a progress
indicator or branch on which step finished.

```json
{ "type": "flow_step",
  "payload": {
    "step":     "investor/create",
    "resource": "investor",
    "id":       42,
    "next":     "/cashflow/42/edit"
  } }
```

Opt in: `on_flow_step=True`.

## Serializer override

```python
event = lex_view(
    "investor",
    serializer="InvestorWithFundSerializer",
)
```

The name is forwarded as `?serializer=InvestorWithFundSerializer`. The
backend's `ModelEntryProviderMixin.get_serializer_class` resolves it from the
registered serializers (`lex/api/serializers/`) and uses it for the list and
detail responses for that request only. Permission and field-level checks
still apply via `PermissionAwareSerializerMixin`.

A name that does not resolve returns **400 Bad Request** — the embed surfaces
that as the standard React error toast; it does not silently fall back.

## End-to-end example

```python
import streamlit as st
from lex.lex_app.streamlit.embed import lex_view, Flow

st.set_page_config(layout="wide")
st.title("Investor pipeline")

event = lex_view(
    "investor",
    serializer="InvestorWithFundSerializer",
    on_select=True,
    on_create=True,
    on_update=True,
    flow=Flow().after_create("investor", "/cashflow/{id}/edit"),
)

if event:
    match event["type"]:
        case "select":
            ids = event["payload"]["ids"]
            st.write(f"Selected {len(ids)} investors:", ids)
        case "create":
            st.success(f"Created investor #{event['payload']['id']}")
        case "update":
            st.toast("Investor updated")
```

## Security

- The component sets `targetOrigin` to the resolved frontend base URL when
  posting *into* the iframe — never `"*"`.
- Inbound messages are dropped unless `event.origin` matches the resolved
  base URL **and** `data.source === "lex-app"`.
- Payloads are forwarded as-is; the component does no JSON-schema validation.
  Python user code is responsible for treating `payload` as untrusted input.

## Versioning

`version: 1` is the current protocol. Additive changes (new event types, new
optional payload fields) do not bump the version. Breaking changes (renamed
fields, removed types) bump it; the component will warn and refuse to forward
events whose `version` it does not understand.
