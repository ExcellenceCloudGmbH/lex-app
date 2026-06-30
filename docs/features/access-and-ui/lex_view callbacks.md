---
title: lex_view Callbacks
---

`lex_view()` embeds a Lex App page inside a [Streamlit](https://docs.streamlit.io/) app. It has always supported a plain embed; now it can also hand user actions in the embedded page back to your Python script so you can react to them.

If you only want an embedded page, call it without callback flags and it behaves like before — a plain iframe that returns `None`. Nothing about existing call sites changes.

If you want Python to react to what the user does in the embedded app, turn on one or more `on_*` flags.

```python
from lex.lex_app.streamlit.embed import lex_view
```

## Basic usage

```python
import streamlit as st
from lex.lex_app.streamlit.embed import lex_view

event = lex_view("investor", on_select=True)

if event and event["type"] == "select":
    st.write("Selected row IDs:", event["payload"]["ids"])
```

When at least one callback flag is set, `lex_view()` switches from a plain iframe to a bidirectional component and returns the latest **event envelope** (or `None` until the first event arrives). Each time the user acts in the embedded page, Streamlit re-runs your script with the new event as the return value.

## Callback flags

Turn on only what you need:

| Flag | Fires when… |
|---|---|
| `on_create` | A record is created in the embedded page |
| `on_update` | A record is updated |
| `on_delete` | A record is deleted |
| `on_select` | The grid selection changes |
| `on_navigate` | The user navigates to another route |
| `on_flow_step` | A step in a multi-step `flow` completes |

`on_select` is opt-in for a reason: it drives a Streamlit re-run on *every* grid selection change, which is expensive. The framework only wires the grid's selection callback when you explicitly ask for it.

## The event envelope

Every event the embedded page sends back is a dict with a stable shape:

| Key | Meaning |
|---|---|
| `type` | The event kind — `"create"`, `"update"`, `"delete"`, `"select"`, `"navigate"`, or `"flow_step"` |
| `payload` | Type-specific data (e.g. `{"id": 42}` for create/update, `{"ids": [...]}` for select) |
| `id` | A unique event ID, used internally to de-duplicate re-runs so your handler doesn't fire twice for the same event |

Guard your handler on `event and event["type"] == "..."` — `event` is `None` on the first render before anything has happened.

## Redirect flows (`flow=`)

For multi-step workflows, pass a routing table with `flow=`. Each key is `"<resource>/<operation>"` (operation is `create` or `update`); each value is the route to open next. Targets support the `{resource}` and `{id}` template tokens.

```python
event = lex_view(
    "investor",
    on_create=True,
    flow={
        "investor/create": "/cashflow/{id}/edit",
        "cashflow/update": "/investor",
    },
)
```

You can also build the table declaratively with `Flow()`, which reads a little better for longer chains:

```python
from lex.lex_app.streamlit.embed import Flow

flow = (
    Flow()
    .after_create("investor", "/cashflow/{id}/edit")
    .after_update("cashflow", "/investor")
)

lex_view("investor", on_flow_step=True, flow=flow)
```

For the simpler single-hop case you don't need a flow table at all — `redirect_after`, `redirect_after_create`, and `redirect_after_update` each take a single route (with the same `{resource}` / `{id}` tokens).

## Choosing a serializer

Use `serializer=` when the embedded view should shape its data with a specific DRF serializer registered on the model:

```python
lex_view("investor", serializer="InvestorWithFundSerializer")
```

If the name isn't a serializer registered for that model, the embedded request returns HTTP `400` with a validation error rather than silently falling back.

## Existing embed options still work

All the layout and routing options you already use remain available alongside the callbacks: `hide_toolbar`, `hide_actions`, `redirect_after` / `redirect_after_create` / `redirect_after_update`, `height`, `width`, `scrolling`, `extra_params`, and `base_url`. (In bidirectional mode `width` and `scrolling` are ignored — the component is always full width.)
