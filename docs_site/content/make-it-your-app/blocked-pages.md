---
title: "Pages a user can't open"
description: "What a user sees on a page they may not open, a page that looks missing instead, and where Sign out leads."
api:
  - "ui.frame"
---

A few arguments of `ui.frame(...)` decide what a user sees on a page they may not open, and where
**Sign out** leads. The navigation, the header and the rest of the frame are on
[Navigation, header and menu](/make-it-your-app/navigation).

## When a user may not see a page

A page whose `requires=` the user does not meet shows RFDS's access-denied notice instead of its
content, inside the frame. Two arguments word it for your project:

- **`denied=`** is the sentence the user reads, instead of RFDS's. A rule that refuses with a
  sentence of its own shows that sentence instead.
- **`request_access=`** adds a **Request access** link: a page (a `@ui.page` or a
  `ui.reflex_page`, without route arguments), or an `https://` or `mailto:` address.

The notice's **Switch account** link, to sign in as someone else, goes to the sign-out address
(below). The page a refused load goes to shows the same notice, with the refusal's own sentence.

### Refused pages that look missing

Some applications must not even say that a page exists. With **`denied_as_missing=True`**, a
page the user may not open - by a permission or by a rule - looks exactly like a page that does
not exist: lex-app's "Page not found", with a link to the overview. The browser tab's title, the
breadcrumbs and the navigation entry give nothing away either.

```python
ui.frame(denied_as_missing=True)
```

- The page's opener decides on the server, and tells the browser only yes or no, never a rule's
  sentence.
- A refused load's error page is the same "Page not found".
- lex-app adds its own not-found page for addresses that match no page, so a missing page and a
  refused one look the same. A project that adds its own `404` page keeps it.
- `denied=` and `request_access=` word a notice this never shows, so they are refused beside it.
- The name shares its stem with `denied=`. The earlier name, `refused_as_missing=`, is refused
  with the argument to write.

```md alert warning
This hides a page from the screen, not from someone who reads the app's code: every page's code is
in the app the browser loads. What a page shows is still guarded by its loads' and handlers'
`requires=`.
```

## Where Sign out leads

**`sign_out=`** is where the user menu's **Sign out** leads, and where **Switch account** goes: a
page, or an `https://` address. By default it is lex-app's `/logout`, which ends the dashboard's
sign-in and lex-app's. An address of your own should end at lex-app's sign-out too, or the
dashboard's sign-in lasts until it expires.

A route is never written as text in `ui.frame`: `request_access="/help"` is refused, with a sentence
asking for the page itself.

### The next user of a browser tab

Reflex keeps a browser tab's states on the server under a client token that the tab keeps, and a
sign-in does not change it. Two things make sure the next user of a tab sees nothing of the last:

- **What lex-app decided for one user is that user's.** The frame's entries, counts, links and
  words, the pages' rules' answers, a kept form's result and refusal: when a page opens for
  another user on the tab, lex-app clears them first.
- **lex-app's `/logout` gives the tab a fresh client token** before the browser leaves, so whoever
  signs in next starts with states of their own - your own fields included, such as a record a
  load filled, which lex-app could not know to clear.

A sign-out page of your own keeps the tab's token, so the next user's states would be the last
user's, apart from what lex-app clears. Have it end the same way, with one of:

- **`/logout`**: send the browser there last (a link to `/logout`, or the event `LexUser.sign_out`
  from `lex.lex_app.reflex`), which also ends lex-app's sign-in.
- **`session.navigate(url, new_tab_token=True)`** from `lex.lex_app.reflex`, returned from a
  handler: the tab forgets its token, then loads `url` as a whole page.

```python
import reflex as rx

from lex.lex_app.reflex import session


class PortalSignOut(rx.State):
    @rx.event
    def leave(self):
        return session.navigate("https://portal.example.com/sign-out", new_tab_token=True)
```
