---
title: Navigation
aliases:
  - "interface/navigation"
---

Lex App uses a stable, professional navigation system designed for daily use. The sidebar organizes your models into logical groups, breadcrumbs track where you are, and record search takes you straight to a record.

## The Home Page

What you see first depends on your project:

- the project's own [[access-and-dashboards/streamlit/index|Streamlit]] dashboard, when it
  has one and the dashboard server is running;
- otherwise a dashboard of widgets, when the project defines one;
- otherwise a **Welcome** page — *Welcome,* and your name — with **Project Information**:
  the project's name, branch and environment.

The home icon at the start of the breadcrumb trail brings you back here.

## The Sidebar

The left sidebar is your primary navigation. Models are organized into collapsible groups — defined by your team's `model_structure.yaml` configuration — so related entities stay together.

Each group can have a custom icon and display name. For example, a fund management project might show:

- 📥 **Data Import** — upload models for CSV ingestion
- 👥 **Teams & People** — core entities
- 💶 **Expenses** — financial records
- 📊 **Reports** — calculated summaries

Groups start collapsed. Click one to open it; the sidebar remembers which groups you
opened, in this browser. A group added to the project later starts collapsed too.

When the project runs its dashboards in the app, the sidebar has a **Streamlit** group,
and a **Reflex** group for Reflex dashboards. Their entries show the dashboards inside
the app's own page.

On a screen narrower than 600 pixels — a phone — the sidebar isn't shown at all.

> [!tip]
> If you're a developer configuring the sidebar, see [[model-your-data/model structure]] for the `model_structure.yaml` reference.

![The sidebar, its search box, and the breadcrumb trail on a record page](images/navigation/sidebar-and-breadcrumbs.svg)

### Arranging the Sidebar

These choices are kept in this browser:

- **Left or right.** The handle on the sidebar's inner edge moves it to the other side of
  the window: drag it past the middle, or double-click it. Its tooltip reads *Drag to
  dock on the right — or double-click* (or *left*).
- **Full height or floating.** The icon at the top of the sidebar, beside the logo,
  switches between a sidebar that runs the full height of the window and a floating
  card. Its tooltip names the one you'll get: *Floating card sidebar* or *Full-height
  sidebar*.
- **Menu views.** Above the search box, **Default** is the menu as your project defines
  it. **New menu view** (the **+**) creates one of your own: type a name and press Enter.
  In a menu view of your own, **Reorder items** (the up-and-down arrows) lets you drag
  groups and models into the order you want; click it again (*Done reordering*) to
  finish. Switch menu views from the list, where the bin next to a name deletes it. The
  **Default** menu can't be reordered.

## Breadcrumbs

A breadcrumb trail at the top of every page shows your current location. It
always starts from the **home** icon and traces the path to where you are. On a
table, that is the group and the model:

```
⌂ › Funds › Fund
```

The group segment carries a dropdown: click **Funds** to jump sideways to another model
in the same group without going back to the sidebar. The home icon takes you to the
home page.

On a record page the trail names the model by its internal, lower-case name, followed by
`show`:

```
⌂ › fund › show
```

Click **fund** to go back to the table.

## Sidebar Search

The search box at the top of the sidebar (*Search…*) finds models and groups by name. Start typing and matching groups and models appear, groups first — select one to navigate there directly. If nothing matches, it says *No matching items found.*

This is especially useful in large projects with dozens of models: instead of scrolling through the sidebar, just type the first few letters.

## Search Records

**Search records…** in the header bar searches the records themselves, across every
model. Open it with a click, with ⌘K (Mac) or Ctrl+K (Windows), or by pressing `/` when
you are not typing in a field.

- Type at least two characters. Matches appear as you type, grouped by model. ↑ and ↓
  move through them, ↵ opens the highlighted record's page, and Esc closes the dialog.
- Before you type, the dialog lists your recent searches — kept in this browser. Click
  one to run it again, or **Clear** to forget them.
- If nothing matches, the dialog says *No matches for* and your search. If the search
  itself fails, it says so, with a **Retry** button.

What it looks at:

- **Whole words.** `invoice` finds *invoice*, but `invo` finds nothing.
- **The records' own fields** — text, dates, decimal amounts and choices. It skips yes/no
  fields, files, floating-point numbers, ordinary whole-number fields (big, small and
  positive ones are searched) and foreign-key links. Other links, such as one-to-one
  links, are matched on the linked record's id, so a record is never found by the name of
  a record it links to.
- **Only records you may read.**
- **Not every table.** Users and calculation logs are never searched, nor are a few
  internal tables.

Record search uses PostgreSQL's full-text search, so it works only on a PostgreSQL
database.

## The Header Bar

Right of the breadcrumbs, the header bar holds:

| Control | What It Does |
|---|---|
| **Delete Unused Sharepoint Files** (the bin) | Only for administrators — the `admin` role — on instances that keep files in SharePoint. See [[#Deleting Unused SharePoint Files]] |
| **Monitoring** (the chart line) | Opens a **Monitoring** panel on the right with your instance's Grafana dashboard. Every user has it |
| **Search records…** | Searches records across every model — see [[#Search Records]] |
| **Refresh** | Reloads the data on the page. It spins while the app is loading |
| The theme toggle | Switches between light and dark — see [[using-the-app/themes\|Themes]] |
| Your name | Opens the user menu with **Logout** |

**Logout** ends your sign-in for every Lex app that shares it, not just this one.

The feedback button sends feedback to Excellence Cloud. It sits a little above the
bottom corner opposite the sidebar — bottom-right while the sidebar is on the left — and
isn't shown when the app is embedded in another page. It already knows who you are, so
there is nothing to sign in to.

### Deleting Unused SharePoint Files

The page behind **Delete Unused Sharepoint Files** looks for files in SharePoint that no
record refers to any more. It starts in **Dry Run**: **Show the Files to be Deleted**
lists the **Unused Files** and the **Referenced Files** and deletes nothing. Switch to
**Real Run** and the button becomes **Delete Unused Files**; afterwards the page lists the
**Deleted Files** and any **Failed Files**.

## Page Transitions

Navigation between pages uses smooth transitions — no full-page reloads, no flickering. The application is a single-page app (SPA), so switching between models, views, and records feels instantaneous.
