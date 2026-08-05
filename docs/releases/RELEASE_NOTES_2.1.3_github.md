## Main changes

- **New design and theme.** The whole app has a refreshed look — cleaner tables, forms, and navigation, in both light and dark mode.
- **New sidebar.** A full-height side navigation with a consolidated header bar. More room for your data, and models are easier to find.
- **Settings option.** A settings panel lets each user tune how a grid looks and behaves — row density, which columns are pinned, the column layout, and enabling or disabling the column filter toggle. Your choices are remembered.
- **Less cluttered table view.** Columns auto-fit to their content, date columns show the date only by default (the full timestamp is on hover), and the toolbar is tidier — so the grid shows more data with less noise.
- **Number formatting per column.** Choose how a numeric column displays — a plain number, a currency (€, $, £, CHF, ¥), or a percentage — and how many decimal places to show. Set it once and the column stays formatted that way.
- **Card-like foreign keys.** A column that links to another record now shows that record's name as a compact chip, whose text can be customized with a serializer.
- **Colorful calculation status, pinned left.** The calculation status is shown as a color-coded pill (in-progress, success, error…) and pinned to the left of the grid, so you can always see it while scrolling.
- **Action column pinned right.** The per-row actions column is pinned to the right by default, so the controls stay put no matter how wide the table gets.
- **Calculate moved into the actions bar.** The **Calculate** button now lives with the other per-row actions, next to the status pill — keeping calculation controls together.
- **Toggle the selection column.** The row-selection checkbox column can be shown or hidden, so you only see it when you actually need to select rows.
- **New quick-edit panel.** Editing opens a sliding panel that's part of the grid surface — quicker than a separate page, and you stay in context with your data.
- **Current / As-of / History.** A single control switches a table between its live values, a point-in-time "as of" view (the data as it was at any past moment), and the full history of changes.
- **Calculation logs that read like a document.** Calculations can group their output into titled sections — a table of contents for a run, with nested sub-sections. In the log view (the `CalculationLogTreeView`), the left pane lists every section so you can jump straight to it, and sections fold away to focus on one phase at a time. Section titles, severity badges, and a copy button round it out.

## Optimizations

- **Live-updating tables.** Open grids refresh themselves when the underlying data changes — no manual **Refresh**.
- **Faithful PDF export.** The **Download PDF** of a calculation log now renders just like the on-screen view — headings, tables, and code blocks intact.
- **Lower memory on heavy operations.** Data-heavy calculations use noticeably less memory per record, and large fan-out calculations stream their work instead of building everything up front — so big runs stay within a bounded memory budget instead of exhausting the instance.

## Bug fixes

- **Timezone bug.** Timestamps (edit times, calculation-log times, history) and the "as-of" time-travel could appear shifted by a couple of hours. Times now display correctly in your local timezone, and time-travel lands on the right moment.
- **File upload / removal problem.** Removing a file from a file field and saving no longer brings the old file back — a removed attachment stays removed, while replacing or leaving a file untouched behaves as expected.
- **Duplicate records on save.** Double-clicking **Save** could create two copies of a record. Save is now guarded, so a record is created once.
- **Form validation errors are now shown.** When a save was rejected by a validation rule, the form could fail without saying why. The message now appears on the relevant field.
- **Number and yes/no column filters.** Filtering a numeric or true/false column could fail to load the list. These filters now work correctly.
- **Exports respect your sort order.** An Excel/CSV export now comes out in the same order as the grid on screen.
- **Exports respect field permissions.** Columns you don't have permission to see are no longer included in exports.
- **New table views activate automatically.** A list view you just created now opens straight away instead of staying inactive.
- **Safer content rendering.** Process-flow output is now sanitized before it's displayed, closing a path where crafted content could run in the browser.
- **History stays intact.** Historical records can no longer be deleted, protecting the audit trail.

---

**Upgrade note:** run database migrations on upgrade (this release adds one new, nullable column for the calculation-log sections — instant, no data rewrite). No configuration changes are needed.
