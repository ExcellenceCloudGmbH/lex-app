# lex.ui pages in a project's own chrome - withdrawn

**Status:** built on 2026-11-24 and withdrawn the same day after an independent review.

`ui.chrome(frame, first=)` let a project put every `@ui.page` in its own frame and run its own gate
before the page's loads; `ui.install(app)` let a project with a hand-built `rx.App` register lex.ui's
pages. The review found that both followed one ported project's habits rather than what lex-app
projects need:

- the pages design says lex-app decides how a page looks (goal 4); a project frame is a second chrome,
  and replacing `lex_chrome` silently drops its embedded mode, theme follower, notices and navigation;
- `first=` brings back the project-made gate the tables design rejected: who may read what belongs in
  the models' `permission_*` and `@ui.page(requires=)`;
- a hand-built app gets none of `create_app`'s session, policy, header and grants middleware, so a
  public `install` made an unsupported path look supported.

What stays: `create_app` runs lex.ui's part of the app in one internal step (`declare.install`: the
declared pages, `/ui-error`, the jobs' clock, the route check). A project moving to lex.ui keeps its raw
pages in its own frame and puts lex.ui parts into them with `ui.embed`; its finished pages use lex-app's
chrome. See batch 1db (1.825-1.826).
