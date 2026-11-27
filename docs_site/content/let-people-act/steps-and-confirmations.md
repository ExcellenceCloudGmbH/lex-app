---
title: "Forms in steps, and confirmations"
description: "A form shown one step at a time, and a single button that confirms or asks for values first."
api:
  - "ui.step"
---

A [form](/let-people-act/forms) can be shown a step at a time, and a single button can ask a
question, or ask for values, before its handler runs.

## A form in steps

`steps=` instead of `fields=` makes a wizard: one form shown a step at a time. **Next** checks the
step's fields on the server; the handler runs once, on the last step, with the values of every step.

```python
@ui.page("/orders/new", nav="New order")
def new_order():
    return ui.form(
        title="New order",
        steps=[
            ui.step("Customer", fields=[ui.field("customer", required=True)]),
            ui.step("Lines", fields=[ui.field("amount", kind="money", required=True),
                                     ui.field("due", kind="date")]),
        ],
        submit=OrderState.create,
    )
```

## Buttons that confirm or ask

The same checks work on a single button. `confirm=` asks a question first; `ask=` asks for values
in a dialog and passes them to the handler as `values`, after the row's own values:

```python
ui.button("Close", Orders.close, row["id"], tone="danger", confirm="Close this order?")
ui.button("Decline", Orders.decline, row["id"], ask=[ui.field("reason", required=True)])


class Orders(ui.State):
    @ui.action
    def decline(self, key, values):
        orders.decline(key, values["reason"])
```

A `tone="danger"` button must have `confirm=` or `ask=`. A question is written so it names the
action and ends in `?`: "Are you sure?" stops the page with an error. More in
[Buttons and links](/let-people-act/buttons-and-links).
