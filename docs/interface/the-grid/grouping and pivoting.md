---
title: Grouping & Pivoting
---

Sometimes you don't want a flat list — you want a structure. Grouping lets you organize rows into collapsible hierarchies, and pivoting rotates your data into a cross-tabulation. Both happen in the grid, without leaving the page, and without changing the underlying data.

## Row Grouping

Drag any column header to the grouping area above the grid (or right-click a column → **Group by this column**). The grid instantly reorganizes into expandable groups.

For example, group your expenses by **Team**:

```
▼ Engineering (12 expenses, €24,500)
    ├── Software license renewal — €3,200
    ├── Conference travel — €1,800
    └── ...
▼ Marketing (8 expenses, €15,200)
    ├── Ad campaign Q1 — €8,000
    └── ...
▼ Finance (5 expenses, €6,300)
    └── ...
```

Each group header shows aggregate values — the count of records and totals for numeric columns. Click to expand or collapse.

> [!example]- 🎬 Video — Drag-to-group row grouping
> <video controls width="100%">
>   <source src="../../videos/grid-drag-to-group.mp4" type="video/mp4">
> </video>
> Drag the "team" column to the group bar, see the grid reorganize into collapsible groups.

### Multi-Level Grouping

Add more columns to create nested groups. Group by Team, then by Category within each team:

```
▼ Engineering
    ▼ Software (4)
    ▼ Travel (3)
    ▼ Equipment (5)
▼ Marketing
    ▼ Meals (2)
    ▼ Travel (6)
```

Drag columns in the group bar to reorder the hierarchy. Remove a group level by dragging it back to the column area.

<!-- 📸 SCREENSHOT: Multi-level grouping with two group columns in the group bar -->

### Aggregation Functions

When data is grouped, numeric columns automatically show aggregate values in the group row. You can choose the aggregation function per column:

| Function | What It Shows |
|---|---|
| **Sum** | Total of all values in the group |
| **Average** | Mean value across the group |
| **Count** | Number of records in the group |
| **Min / Max** | Smallest or largest value |
| **First / Last** | First or last value in the group |

Right-click a column header → **Aggregation** to change the function.

## Pivot Mode

Pivot mode transforms your grid into a cross-tabulation — think pivot tables in Excel, but live and interactive.

To enable pivot mode:

1. Click the **Pivot Mode** toggle in the column panel (or right-click a column → **Pivot by this column**)
2. Drag a column (like **Quarter**) to the pivot area
3. The grid flips: each unique value in the pivot column becomes its own column header

For example, with expenses grouped by **Team** and pivoted by **Quarter**:

| Team | Q1 2026 | Q2 2026 | Q3 2026 | Q4 2026 | **Total** |
|---|---|---|---|---|---|
| Engineering | €8,200 | €6,100 | €5,400 | €4,800 | **€24,500** |
| Marketing | €4,500 | €3,800 | €3,200 | €3,700 | **€15,200** |
| Finance | €1,800 | €1,500 | €1,200 | €1,800 | **€6,300** |

> [!example]- 🎬 Video — Pivot mode cross-tabulation
> <video controls width="100%">
>   <source src="../../videos/grid-pivot-mode.mp4" type="video/mp4">
> </video>
> Enable pivot mode, drag Quarter to the pivot area, see the grid transform into a cross-tabulation.

> [!tip]
> When you [[interface/the-grid/exporting data|export]] in pivot mode, the exported file reflects the pivoted layout — rows, columns, and aggregations exactly as you see them on screen.

## When to Use What

| Scenario | Best Tool |
|---|---|
| "Show me expenses organized by team" | **Row Grouping** — one level |
| "Show me expenses by team, then by category" | **Multi-level Grouping** |
| "Compare quarterly spend across teams" | **Pivot** on Quarter, group by Team |
| "Find the highest single expense per department" | **Group** by Team, aggregate with **Max** |

Both grouping and pivoting are preserved when you [[interface/the-grid/saved views|save a view]], so you can build complex analytical layouts once and revisit them instantly.

> [!note]
> Columns backed by computed values (such as a `SerializerMethodField` in a custom serializer) cannot be grouped or pivoted — they have no underlying database column for the server to query against. These columns are automatically disabled in the group and pivot panel.
