---
date: 2026-11-24
clusters: [1]
tests_added: 24
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1dz 1ea-1ep 4n 10ag 10d): 340 pass / 0 fail, 473 subtests pass"
---

# lex.ui's frame: the rest of an application's shell (1ej-1ep)

`ui.frame(...)` gained the generic parts an application's shell has, so a project moving its pages
onto lex.ui loses nothing of its frame: what the navigation shows is decided on the server for each user
(permissions and `@ui.rule`s), sections of links with counts and per-user entries and a foot, a search
box and actions in the header, the user menu's entries, a banner, refused pages that look missing, and the
project's mark. Values that depend on the user are functions of the user, called on the server when a
page opens. Design: section 6.21 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`
(a loading bar was checked and not built: RFDS has none, lex-app's page kinds show their skeleton).
Batches [1ej, 1ek, 1el, 1em, 1en, 1eo, 1ep](../../clusters/01-init/batches.md).

Scenarios past 999: the plan's scripts read scenario numbers as integers (`\d+`), so 1.1000 onward is
accepted by `build`, `check` and `validate` with no change.

Two earlier tests changed their given, both because a navigation entry is now decided on the server:
1.962 (a raw page's entry carries its route as the server's key, not the permission string; the
permission is still on the entry) and 1.973 (a raw page's `requires=` may now be a rule, which hides its
entry; a plain function is still refused).
