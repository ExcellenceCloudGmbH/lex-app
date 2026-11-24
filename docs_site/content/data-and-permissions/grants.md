---
title: "Grants from Keycloak"
description: "Where a user's permissions come from, and how the dashboards read them."
---

A dashboard has no permission system of its own. It reads the grants the user already holds in
lex-app's Keycloak - the same grants lex-app's grid and API enforce - so a user may do the same
things in a dashboard as anywhere else in lex-app.

## What a user holds

```python exec
from lex import ui


class MyGrants(ui.State):
    @ui.rows
    def grants(self):
        return [{"id": grant["rsname"], "resource": grant["rsname"], "scopes": grant["scopes"]}
                for grant in self.user.permissions]

    @ui.action
    def look_again(self):
        pass  # nothing to do: when an action succeeds, its state's tables read again
```

```python
from lex import ui


class MyGrants(ui.State):
    @ui.rows
    def grants(self):
        return [{"id": grant["rsname"], "resource": grant["rsname"], "scopes": grant["scopes"]}
                for grant in self.user.permissions]

    @ui.action
    def look_again(self):
        pass  # nothing to do: when an action succeeds, its state's tables read again
```

```python demo
ui.section(
    "Your grants",
    ui.button("Look again", MyGrants.look_again),
    ui.table(MyGrants.grants, columns=["resource", ui.column("scopes", kind="tags")]),
)
```

Each grant names a **resource** - one of your models, written `<app label>.<ModelName>` - and the
**scopes** the user holds on it. Switch **Viewing as** to Viewer and click **Look again**: every
resource keeps only `read`.

## Where grants come from

1. `lex init` registers each of your project's models in Keycloak as a resource, with lex-app's
   scopes: `read`, `edit`, `export`, `create`, `delete` and `list`.
2. Whoever manages your instance's users grants them scopes on those resources in Keycloak, for a
   whole model or for single records.
3. When a user signs in to lex-app, Keycloak gives their access token. A dashboard asks Keycloak
   for that token's grants once and keeps the answer for as long as the token lives.

The Permissions page of lex-app's own documentation covers the first two steps, and the
`permission_*` methods a model can define on top of them.

## Two layers

A user's access is decided in two places, and the dashboards use both:

| Layer | Decides | Applied by |
| --- | --- | --- |
| Keycloak grants on a model | whether the user may read, edit, create or delete that model at all | `requires=`, `self.can`, `visible=` and a page's `requires=` - and lex-app's API |
| your model's `permission_*` methods | which records and which fields this user may see or change | lex-app's API, so [`self.api`](/data-and-permissions/api) |

`requires=` and `self.can` read only the first layer, and only grants on the **whole model**. A
grant for single records does not satisfy `ui.read(Fund)`, though `self.api.list(Fund)` may still
return those records to the user. So a handler that should work for such a user reads through
`self.api` and leaves out `requires=`.

## When a change in Keycloak arrives

- **In handlers**, grants are fetched once per access token. lex-app renews the token while the
  user is signed in, and the renewed token's grants are fetched again: a grant given or taken away
  in Keycloak reaches the handlers within one token lifetime. If Keycloak cannot be reached, the
  user keeps the grants they were last given; a user it never answered for has none.
- **On the page** - `visible=`, a page's `requires=`, the navigation - grants are loaded each time
  a page opens, and refreshed when they are more than a minute old. Grants that have not been
  refreshed for five minutes count as none until they are loaded again.

## The Viewer and Editor switch on this site

This site has no Keycloak. Each demo's **Viewing as** switch stands in for it: the demo user,
Demo Consultant, holds `read`, `create`, `edit` and `delete` on `docs.Fund`, `docs.Investor` and
`docs.Commitment` as an Editor, and only `read` as a Viewer. The switch belongs to your browser
tab, and every handler asks it afresh, so `requires=`, `self.can` and `self.user.permissions`
follow it at once.

```md alert info
On this site only `requires=` and `self.can` follow the switch. The sample data behind
`self.api` does not check permissions, so a demo that writes without `requires=` would let a
Viewer write. In lex-app, the API refuses that write as well.
```

To give a handler a user with particular grants in a unit test, pass them to `ui.testing`: see
[Testing](/ship-it/testing).

Next: [lex-app's look](/look-and-chrome/look), how the dashboards take lex-app's design.
