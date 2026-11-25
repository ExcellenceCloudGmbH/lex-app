---
title: "How sign-in works"
description: "How a dashboard knows who is looking: it borrows the user's lex-app sign-in."
---

A dashboard has no sign-in of its own. The user signs in to lex-app, as always, and lex-app hands
the dashboard a session for that sign-in. The dashboard never shows a login form, never talks to
Keycloak and never holds a Keycloak token in the browser. Nothing from Reflex Enterprise and no
Reflex account is involved.

For you, that means there is nothing to set up: every page, every handler and every table already
runs for a signed-in user, and `self.user` says who it is.

## Who is signed in

Inside a handler of a `ui.State`, `self.user` is the signed-in user:

```python exec
from lex import ui


class WhoIsSignedIn(ui.State):
    name: str = ""
    username: str = ""
    email: str = ""

    @ui.load
    def open(self):
        self.name = self.user.name
        self.username = self.user.username
        self.email = self.user.email
```

```python
from lex import ui


class WhoIsSignedIn(ui.State):
    name: str = ""
    username: str = ""
    email: str = ""

    @ui.load
    def open(self):
        self.name = self.user.name
        self.username = self.user.username
        self.email = self.user.email
```

```python demo
ui.record(
    title="Who is signed in",
    load=WhoIsSignedIn.open,
    sections=[
        ui.section(
            "You",
            ui.fact("Name", WhoIsSignedIn.name),
            ui.fact("Username", WhoIsSignedIn.username),
            ui.fact("E-mail", WhoIsSignedIn.email),
        ),
    ],
)
```

On this site you are a demo user. In your project, the page shows the person signed in to
lex-app.

| `self.user.` | Holds |
| --- | --- |
| `id` | Keycloak's stable id for the user (the `sub` claim) |
| `name` | the user's name |
| `username` | Keycloak's `preferred_username` |
| `email` | the e-mail address |
| `permissions` | the user's Keycloak permissions, as Keycloak lists them (`{"rsname": ..., "scopes": [...]}`) |

To ask about one model, use [`self.can(Model, "edit")`](/who-can-do-what/permissions) rather than
reading `permissions` yourself. `self.user` works only inside a handler's body.

## How the sign-in works

The first time a page sends an event without a session, the dashboard sends the browser on a short
round trip through lex-app and back:

```text
 Browser                     Dashboard backend               lex-app
    |                               |                            |
    |-- an event, no session ------>|                            |
    |<- load /auth/start?next=/funds|                            |
    |-- /auth/start --------------->|  sets a one-time nonce     |
    |<- 303 to lex-app -------------|                            |
    |-- /oidc/handoff?aud=reflex ------------------------------>|  signed in?
    |<- 302 back with a single-use code -------------------------|  mints a code
    |-- /auth/handoff?code=... ---->|  redeems the code          |
    |<- 303 to /funds, sets the handle cookie                    |
    |-- events, now with a session->|                            |
```

1. **`/auth/start?next=<page>`** is a route on the dashboard's backend. It remembers a random nonce
   in a cookie and sends the browser to lex-app's `/oidc/handoff?aud=reflex`.
2. **`/oidc/handoff`** is lex-app's. If the browser is signed in to lex-app, it mints a single-use
   code, valid for 60 seconds and bound to that sign-in, the dashboard's address and the nonce,
   and sends the browser back. If it is not signed in and the dashboard is open in its own tab,
   lex-app shows its normal sign-in first and then carries on.
3. **`/auth/handoff`** is the backend's again. It checks the nonce and redeems the code - in the
   same process, against the same database as lex-app - into a session, then sends the browser
   back to the page it came from.

The session is kept as an opaque handle in an HttpOnly cookie, `lex_rdash` (`__Host-lex_rdash`
over https, where it is also `Secure`, `SameSite=None` and `Partitioned`). It lasts 30 days, but
it is only as good as the lex-app sign-in behind it: before an event, the dashboard checks that the
sign-in still stands, reading the database again once its last answer is a minute old.

While the round trip runs, the address bar carries a counter, `lex_hs`. It is taken off once the
user is signed in.

## Signing out

A page in lex-app's look has the user's name in its header, with a **Sign out** item. It opens
`/logout`, which shows "Signing out" while the browser goes to the backend's **`/auth/logout`**.
That route ends the dashboard's session, deletes the cookie and sends the browser to lex-app's
sign-out, which signs the user out of lex-app and Keycloak too. The browser then comes back to the
dashboard's home page, which sends it through sign-in again.

`/logout` also drops every [private value](/long-running-work/private-results) the user holds in the
dashboard's server process. A project can send **Sign out** somewhere else with
[`ui.frame(sign_out=...)`](/make-it-your-app/blocked-pages#where-sign-out-leads).

Signing out works the other way round as well. When the user signs out of lex-app, or the sign-in
ends for any other reason, the dashboard notices at its next check, within a minute, and the next
event goes through sign-in again.

Inside lex-app's frame the dashboard's header is hidden: lex-app's own header shows the user and
its own sign-out. Opened with `?is_logout_enabled=false`, the header shows the user's name without
the **Sign out** item.

## When the sign-in cannot finish

- **Not signed in to lex-app, inside lex-app's frame.** Keycloak never shows its form in a frame,
  so the frame shows "Your sign-in for this dashboard has ended." and tells lex-app around it,
  which can renew its own sign-in and reload the dashboard. Opened on its own, the same page
  offers **Sign in again**.
- **The browser keeps no cookie here** - a frame in a browser that blocks third-party cookies.
  After two round trips that left no session, the dashboard stops trying and shows
  `/auth/blocked`: "This browser does not keep the dashboard's sign-in here, so it cannot stay
  signed in inside lex-app.", with a link that opens the dashboard in its own tab.
- **The database cannot be reached.** `/auth/handoff` answers "Sign-in is temporarily
  unavailable. Retrying..." and tries again after 5 seconds.
- **An address the instance does not list.** `/auth/start` answers "This address is not one of
  this instance's dashboards." and sends the browser nowhere. Which addresses are listed is a
  deployment setting: see [Deploy](/test-and-ship/deploy).

## What the browser never sees

The Keycloak tokens stay in lex-app, on the server. lex-app refreshes them; the dashboard asks for
the access token only when a handler needs it, and that token never leaves the backend.

The browser holds one thing: the handle, in an HttpOnly cookie that page scripts cannot read.
Even that does not reach Reflex. Reflex copies the event connection's request headers into the
state it sends to the browser, so the backend removes every cookie from that connection first and
passes on only the handle's SHA-256 digest, in an `X-Lex-Binding` header. A digest is of no use
outside the server, and a browser that sends its own `X-Lex-Binding` has it dropped.

`self.api` calls lex-app's API with the user's access token for you, so lex-app's permissions
apply to the dashboard exactly as they do in lex-app's own pages.

## In raw Reflex code

A page you write in Reflex yourself (see [Mix in your own Reflex](/make-it-your-app/your-own-reflex)) has the
same sign-in. What a `ui.State` gives you as `self.user` and `self.api`, raw code reads from
`lex.lex_app.reflex`:

```python
import reflex as rx

from lex.lex_app.reflex import LexUser, current_access_token, session


class Profile(rx.State):
    email: str = ""
    has_token: bool = False

    @rx.event
    async def load(self):
        info = session.profile(self)  # sub, email, preferred_username, name; {} when anonymous
        self.email = info.get("email", "")
        token = await current_access_token(self)  # "" when there is no sign-in
        self.has_token = bool(token)


def profile_card() -> rx.Component:
    return rx.vstack(
        rx.text("Signed in as ", LexUser.display_name),
        rx.button("Sign out", on_click=LexUser.sign_out),
    )
```

| Name | What it is |
| --- | --- |
| `LexUser.display_name`, `.username`, `.email`, `.sub` | the user, as vars a component can show; empty until the sign-in is known |
| `LexUser.sign_out` | the event that signs out, as **Sign out** in the header does |
| `session.profile(state)` | the user's `sub`, `email`, `preferred_username` and `name`, as a dict; empty when anonymous |
| `await current_access_token(state)` | the user's access token for lex-app's API, valid at least 60 seconds; `""` without a sign-in, or while Keycloak cannot be reached and no valid token is left |
| `await session.access_token(state)` | the same token, from the `session` module |
| `session.sign_out_url(state)` | the backend's `/auth/logout` address for this session |

`session.sign_out_url` is a backend route, so it must be loaded as a whole page:
`LexUser.sign_out` returns `session.navigate(session.sign_out_url(self))`, which does that.

### Building the app yourself

lex-app's app, the one `lex_config()` points Reflex at, installs the sign-in for you, and so does
every page `lex reflex add` writes into it: there is no call to make. If your project builds its
own `rx.App` instead, `session.install(app)` adds the sign-in to it: the four `/auth/` routes, the
guard on the event connection, and the middleware that sends a session without a sign-in through
the round trip. It is one of several things lex-app's `create_app()` installs - lex-app's look,
permission checks and database connections are others - so starting from `create_app()` is
simpler.

## Coming from Reflex Enterprise

Earlier versions of lex-app signed dashboards in through Reflex Enterprise's `AuthPlugin`. A
project written for that changes in these places:

| With Reflex Enterprise | Now |
| --- | --- |
| `reflex-enterprise` and `rfds[enterprise]` in the requirements | nothing: lex-app requires plain `reflex` and `rfds` |
| `lex reflex login`, or `REFLEX_ACCESS_TOKEN` in a deployment | nothing: no Reflex account is needed |
| the dashboard's `/callback` among the Keycloak client's redirect URIs | no longer used: the dashboard never talks to Keycloak |
| `import reflex_enterprise as rxe`, `@rxe.event(auth=check)` | `from lex.lex_app.reflex import lex_event`, `@lex_event(auth=check)`; in a `ui.State`, `requires=` on the handler |
| `rxe.field(auth=...)`, `rxe.var(auth=...)` | no counterpart: check in the handler that fills the field |
| `@rxe.page(..., auth=False)` for a public page | no counterpart: a page's events always need a sign-in |
| `from reflex_enterprise.auth import User`; `User.email`, `User.sub` | `LexUser.email`, `LexUser.sub`, `LexUser.display_name`, `LexUser.username`; `self.user` in a `ui.State` |
| `await User.current()` | `session.profile(self)`; `self.user` in a `ui.State` |
| `User.logout` | `LexUser.sign_out`, or a link to `/logout` |

`current_access_token`, `current_permissions` and `has_permission` keep their names and what they
return. An `rxconfig.py` that holds `config = lex_config()` needs no change.
