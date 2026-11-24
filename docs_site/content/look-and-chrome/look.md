---
title: "lex-app's look"
description: "How the dashboards take lex-app's design, and what you leave to it."
---

A dashboard looks like lex-app without any work from you. Its components come from RFDS, a
design system for Reflex, and lex-app dresses every one of them in its own design: its colours,
its Inter type, its navy header and side navigation, its rounded cards and its buttons, in light
and in dark. You say what is on a page; lex-app decides how it looks.

## What you get

```python exec
from lex import ui
```

```python demo
ui.section(
    "Harbour Infrastructure",
    ui.status("Investing", tone="info"),
    ui.fact("Size", 600_000_000, kind="money", currency="USD"),
    ui.fact("Called", 0.62, kind="percent"),
    ui.fact("Investors", 14, kind="number"),
    ui.message("The next capital call is due in March.", tone="warning"),
)
```

Nothing in this code chooses a colour, a font or a spacing. The badge's tone, the money and
percentage formats and the notice's colour all come from lex-app.

## Where the look comes from

lex-app keeps its design as tokens: named values for every colour, radius, shadow and font, for
the light and the dark mode. When the dashboards are compiled, lex-app turns those tokens into the
stylesheet every page carries, which sets each of RFDS's own style variables to lex-app's value.

- **One source.** A new release of lex-app's tokens restyles the dashboards with no change to
  your code.
- **Both modes.** Every value exists for light and for dark. Inside lex-app, a dashboard follows
  lex-app's mode, and changes with it when the user switches.
- **Charts** keep RFDS's own colour palette, which is chosen to stay readable for colour-blind
  readers.

## How values are written

Numbers, money and dates are formatted for the instance, from four settings:

| Setting | Default | Decides |
| --- | --- | --- |
| `LEX_FORMAT_LOCALE` | `en-US` | how numbers, money and dates are written |
| `LEX_UI_LANGUAGE` | `en` | the language of the built-in labels and messages |
| `LEX_BASE_CURRENCY` | `EUR` | the currency of a money value that names none |
| `LEX_TIME_ZONE` | `Europe/Berlin` | the time zone dates and times are shown in |

`ui.fact(..., kind="money")` and a money column use them, so a page written once reads correctly
in every instance.

## The frame around a page

Opened on its own, every page sits in lex-app's frame: a header with the dashboard's name, the
signed-in user and the way to sign out, a side navigation listing the pages in their areas, and
breadcrumbs above the page. You do not draw it; `ui.frame(...)` sets how it behaves in your project.
See [The frame](/look-and-chrome/frame).

## What you should not style

- **`ui.*` components take no styling arguments**: no colours, sizes or margins. Where a part
  sits is decided by the page kind - [`ui.record`](/pages/record), [`ui.list`](/pages/list) - and
  how it looks by lex-app. To say something about meaning, use the words lex.ui has: a `tone=` on
  a status or a message, a `kind=` on a fact or a column.
- **No stylesheets or colours of your own.** They would drift from lex-app's design with its next
  release, and would be wrong in one of the two modes.
- **A component lex.ui does not have** goes in a `ui.custom_block`, with a sentence saying why;
  build it from RFDS's components, which take lex-app's look like everything else. See
  [Raw Reflex pages](/look-and-chrome/raw-reflex).

RFDS checks a dashboard's pages when they are compiled - that they use its components and its
style values. On a developer's machine a finding is a warning; in CI it fails the build.
`LEX_RFDS_CONFORMANCE` (`off`, `warn` or `error`) sets this yourself.

Next: [The frame](/look-and-chrome/frame), the navigation, breadcrumbs and no-access notice
around every page.
