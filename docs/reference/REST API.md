---
title: "REST API"
---

The standard interface talks to Lex App over one REST API, and a
[[ship-and-operate/custom frontend|custom frontend]], a script or another system can call
the same routes. This page is that contract for lex-app 2.5: the routes, what they take and
what they answer. The grid's value lists have a page of their own,
[[reference/Grid Filter API|Grid Filter API]].

| Section | Answers |
|---|---|
| [Addresses and sign-in](#addresses-and-sign-in) | Where the routes are, and the three ways to sign a request in |
| [Routes](#routes) | Every route at a glance |
| [What every row carries](#what-every-row-carries) | `id`, `short_description` and the `lex_reserved_*` fields |
| [The list endpoint](#the-list-endpoint) | Pages, ordering, filters and lookups; the grid's `POST` |
| [Single records](#single-records) | Create, read, change, delete; the `calculationId` in the path |
| [Calculations over REST](#calculations-over-rest) | Starting, cancelling and following a run |
| [Bulk changes](#bulk-changes) | Reading and deleting several records by `ids` |
| [History](#history) | A record's versions; `as_of` |
| [Model information](#model-information) | The model tree, model permissions, project info, widgets, HTML reports |
| [Search](#search) | Searching across models |
| [Exports and file downloads](#exports-and-file-downloads) | The xlsx export and file fields |
| [API keys](#api-keys) | Keys for integrations, and who the caller becomes |
| [WebSockets](#websockets) | Live calculation status, record changes and log lines |
| [Errors](#errors) | Status codes and bodies |

## Addresses and sign-in

Paths on this page are relative to Lex App's own address: `http://localhost:8000/` under
`lex start`, and `https://<instance host>/api/` on a hosted instance. So the list of the
`invoice` model is:

```
GET http://localhost:8000/api/model_entries/invoice/list
GET https://<instance host>/api/api/model_entries/invoice/list
```

On a hosted instance the first `/api/` is the instance's backend and the second lex-app's
own API prefix — see [[ship-and-operate/custom frontend#Talking to lex-app|Custom frontend]].
Write a path exactly as shown: a few routes end in `/`, most do not.

`<model>` is the model's name in lower case, as Django's `_meta.model_name` spells it:
`invoice` for `Invoice`, `investorcashflow` for `InvestorCashflow`. Any other name, like
any unknown path under `api/`, answers `404`.

A request is signed in one of three ways:

| Sign-in | Sent as | Used by |
|---|---|---|
| Session | The session cookie (`credentials: 'include'`); every request other than a `GET` also sends the `csrftoken` cookie's value as the `X-CSRFToken` header | The interface, and a custom frontend on the instance's own host |
| Access token | `Authorization: Bearer <access token>` | Scripts and dashboards acting as a user — see [[access-and-dashboards/sign-in and sessions#For frontends and API clients\|Sign-in and sessions]] |
| API key | `Api-Key: <key>` | Integrations, as a technical user — see [API keys](#api-keys) |

Whoever signs in, the model's permission methods decide what they may read, change and
filter by, exactly as in the grid — see [[access-and-dashboards/permissions|Permissions]].

## Routes

| Route | Method | Does |
|---|---|---|
| `api/model_entries/<model>/list` | `GET`, `POST` | The model's rows — [The list endpoint](#the-list-endpoint) |
| `api/model_entries/<model>/filter_values` | `POST` | One column's values with row counts — [[reference/Grid Filter API\|Grid Filter API]] |
| `api/model_entries/<model>/<calculationId>/one` | `POST` | Creates a record — [Single records](#single-records) |
| `api/model_entries/<model>/<calculationId>/one/<pk>` | `GET`, `PATCH`, `PUT`, `DELETE` | Reads, changes, calculates or deletes one record — [Single records](#single-records), [Calculations over REST](#calculations-over-rest) |
| `api/model_entries/<model>/<calculationId>/history/<pk>` | `GET` | A record's versions — [History](#history) |
| `api/model_entries/<model>/many` | `GET`, `DELETE` | Several records by `ids` — [Bulk changes](#bulk-changes) |
| `api/model_info/<model>/fields` | `GET` | The columns a serializer renders — [[reference/Grid Filter API#The fields endpoint\|Grid Filter API]] |
| `api/model-structure` | `GET` | The tree of models the sidebar shows — [Model information](#model-information) |
| `api/<model>/model-permissions` | `GET` | What the caller may do with the model in general — [Model information](#model-information) |
| `api/project-info` | `GET` | The project's name, branch and environment |
| `api/widget_structure` | `GET` | The widgets the project's structure module declares |
| `api/htmlreport/<name>` | `GET` | An HTML report page — [[model-your-data/HTML reports\|HTML reports]] |
| `api/global-search/?q=<text>` | `GET` | Search across models — [Search](#search) |
| `api/calculationlog/tree/` | `GET` | The log entries of a calculation — [Following a run](#following-a-run) |
| `api/download-pdf/<pk>/` | `GET` | A calculation log as PDF — [[calculations/logging#Downloading the log as PDF\|Logging]] |
| `api/active-calculations` | `GET` | The calculations running now — [Following a run](#following-a-run) |
| `api/<model>/export` | `POST` | An xlsx export — [Exports and file downloads](#exports-and-file-downloads) |
| `api/<model>/file-download` | `GET` | The file in a record's file field — [Exports and file downloads](#exports-and-file-downloads) |
| `api/chunked-uploads/…` | | Large files, uploaded in pieces — [[ship-and-operate/custom frontend#Large files\|Custom frontend]] |
| `api/user/`, `api/auth/session` | `GET` | The signed-in user; the sign-in's status — [[access-and-dashboards/sign-in and sessions#For frontends and API clients\|Sign-in and sessions]] |
| `api/health` | `GET` | Liveness — [[ship-and-operate/monitoring and health#Health endpoints\|Monitoring and health]] |

`<pk>` in `one/<pk>` and `history/<pk>` is a whole number: a record whose primary key is
not an integer cannot be addressed on those routes.

## What every row carries

Rows come back as the request's serializer renders them — `?serializer=<name>` on any read,
`default` when it is left out (see [[model-your-data/serializers#Multiple Serializer Views|Serializers]]) —
without the fields the caller may not read. Every row also carries:

| Key | Value |
|---|---|
| `id` | The record's primary key |
| `id_field` | The name of the primary-key field: `id`, unless the model declares another |
| `short_description` | The record as text: its `__str__` |
| `<field>__short_description` | For each foreign key in the row, the related record's `__str__` — see [[model-your-data/serializers#Foreign keys read as names, not IDs\|Serializers]] |
| `lex_reserved_scopes` | What the caller may do with this record: `{"edit": [<field names>], "delete": true, "export": true}`. `edit` lists the fields the caller may change. On a history row, for anyone but a superuser, `edit` is empty and `delete` is `false`. `{}` when the model has none of the permission methods |
| `lex_reserved_calculation_id` | Calculation models only: the `calculationId` of the newest run started from this row, or `null` |
| `lex_reserved_has_calculation_log` | Calculation models only: whether that run has a log |
| `lex_reserved_calculation_closed_reason` | Calculation models only: why the record may not be calculated again, or `null` while it may — see [[reference/CalculationModel Internals#Closing a Record\|Closing a Record]] |

`lex_reserved_calculation_id` and `lex_reserved_has_calculation_log` are worked out for a
whole page at once, in the rows the list's `POST` returns — the grid's rows. Everywhere else,
the `GET` list and single records included, they read `null` and `false`. A run counts for a
row when its `calculationId` begins with `<model>_<pk>_`, which is why
[Calculations over REST](#calculations-over-rest) asks for that shape.

## The list endpoint

`GET api/model_entries/<model>/list` answers the rows the caller may read.

| Parameter | Meaning |
|---|---|
| `perPage` | Rows per page. **Without it the answer is every row, as a bare JSON array**: no default page size is configured. `-1` returns every row in the paginated shape below |
| `page` | Which page, counting from `1`, the default. Read only together with `perPage`; a page past the last answers `404` |
| `ordering` | Comma-separated fields, `-` for descending: `ordering=status,-amount`. A path through foreign keys works with `.` or `__`: `customer.name` |
| `pk_only` | `true`: only the primary keys of the matching rows, as `{"ids": [...], "count": n}` — no pages, nothing serialized |
| `serializer` | The serializer that renders the rows, `default` when left out. An unknown name answers `400` with the names there are |
| `as_of` | A date-time: the rows as they were valid at that moment — see [History](#history) |
| *a field* | A filter — see [Filters](#filters) |

With `perPage`, the answer is a page:

```json
{
  "count": 132,
  "next": "…/list?page=3&perPage=50",
  "previous": "…/list?page=1&perPage=50",
  "results": [{"id": 51, "status": "open", "amount": "1200.00"}]
}
```

The interface always sends an `ordering` — `id` unless the user sorted — so its pages are
stable. A name in `ordering` that is not a field is ignored.

### Filters

Every other parameter names a field and filters by it: `?status=open`,
`?amount__gte=1000`, `?customer__country=DE` — or `customer.country`. A path may go
through foreign keys, or into a JSON field: `?metadata__source=import`.

| Lookup | Keeps the rows whose value |
|---|---|
| *(none)*, `exact` | equals the value. Without a lookup, `null` or `none` mean NULL and `true` or `false` a boolean, whatever the column |
| `iexact` | equals it, ignoring case |
| `contains`, `icontains` | contains it (`i`: ignoring case) |
| `startswith`, `istartswith`, `endswith`, `iendswith` | starts or ends with it |
| `gt`, `gte`, `lt`, `lte` | is greater, greater or equal, less, less or equal |
| `in` | is one of a comma-separated list: `?status__in=open,overdue` |
| `range` | lies between two values, both included: `?amount__range=100,500` |
| `isnull` | is NULL (`true`) or is not (`false`) |
| `date`, `date__gt`, `date__gte`, `date__lt`, `date__lte` | falls on, after or before a date — for a date-time column |
| `blank` | is blank — see [[reference/Grid Filter API#Value-list lookups on the list GET\|Grid Filter API]] |

- Several parameters must all match. A parameter given twice matches either value:
  `?status=open&status=overdue`.
- `!` after the name negates it: `?status!=closed` keeps every row whose status is not
  `closed`, and `?name__icontains!=test` every row whose name does not contain *test*.
- `__in` and `__exact` read their values by the column's type, as value lists send them;
  [[reference/Grid Filter API#Value-list lookups on the list GET|Grid Filter API]] has the
  details.
- **A name the list does not know is ignored, not refused.** A parameter that names no
  field — a typo — or that uses a lookup not in the table drops out without an error, so the
  answer is every row. A parameter with an empty value is ignored too.
- Filtering or ordering by a field the caller may not filter by answers `400`
  `FILTER_NOT_ALLOWED` — see
  [[reference/Grid Filter API#Which fields may be filtered|Grid Filter API]].

The same parameters narrow the grid's `POST` below and `filter_values`.

### The grid's POST

`POST api/model_entries/<model>/list` answers what AG Grid's server-side row model asks
for as the grid scrolls, groups and pivots. The query parameters above still apply —
`serializer`, `as_of` and the filters. The body is the grid's request, under `request` or on
its own:

```json
{
  "request": {
    "startRow": 0,
    "endRow": 100,
    "sortModel": [{"colId": "amount", "sort": "desc"}],
    "filterModel": {"status": {"filterType": "set", "values": ["open", "overdue"]}},
    "rowGroupCols": [],
    "groupKeys": [],
    "valueCols": [],
    "pivotCols": [],
    "pivotMode": false
  }
}
```

| Key | Meaning |
|---|---|
| `startRow`, `endRow` | The slice to answer. `endRow` defaults to `startRow + 100` |
| `sortModel` | `[{"colId": <field>, "sort": "asc"}]` or `"desc"`. Without one, rows come in primary-key order |
| `filterModel` | AG Grid's text, number and date filters, and the set and multi filters of [[reference/Grid Filter API#Set and multi filters\|Grid Filter API]] |
| `rowGroupCols` | The columns to group by, outermost first: `[{"id": …, "field": …}]` |
| `groupKeys` | The group being opened: one key per level, from the outside in |
| `valueCols` | The columns to aggregate: `[{"id": …, "field": …, "aggFunc": …}]`, with `sum` (the default), `avg`, `min`, `max`, `count`, `distinctCount`, `first` or `last`. `first` is the smallest value and `last` the largest |
| `pivotCols` | The columns to pivot by |
| `pivotMode` | `true` to pivot |

The answer is `{"rowData": [...], "rowCount": n}`:

- **Rows** — no grouping, or a group opened down to its last level: the records, as the
  `GET` serializes them, and `rowCount` counts all records that match. On a calculation
  model these rows are the ones that fill `lex_reserved_calculation_id`.
- **Groups** — fewer `groupKeys` than `rowGroupCols`: one object per group of the next
  level, holding the group column's value, `__childCount` and each `valueCols` aggregate
  under the column's `id`. `rowCount` counts the groups.
- **Pivot** — with `pivotMode`, the answer adds `pivotResultFields`, the names of the
  columns the pivot made. At most 200 combinations of pivot values become columns.

A field the caller may not filter by, or one with no database column behind it, is skipped
in `filterModel`, `sortModel` and grouping rather than refused; grouping by such a column
answers an empty level.

## Single records

| Request | Answers |
|---|---|
| `POST api/model_entries/<model>/<calculationId>/one` | `201` and the new record |
| `GET api/model_entries/<model>/<calculationId>/one/<pk>` | `200` and the record; `404` when the caller may not read it |
| `PATCH …/<calculationId>/one/<pk>` | `200` and the record as saved. Only the fields sent change |
| `PUT …/<calculationId>/one/<pk>` | The same, but every required field must be sent; the interface uses `PATCH` |
| `DELETE …/<calculationId>/one/<pk>` | `200` and the record as it was |

Bodies are JSON or `multipart/form-data`. The interface sends multipart, with
`?serializer=` set to the model's `default_serializer_name` (see
[Model information](#model-information)). A file field takes a file part, or the token of a
chunked upload — see [[ship-and-operate/custom frontend#Large files|Custom frontend]]; an
empty value removes the stored file, and leaving the field out keeps it.

A `PATCH` that changes nothing saves nothing and answers `200` with the record as it is. On
a calculation model, an edit resets `is_calculated` to `NOT_CALCULATED` unless the record is
closed — see [[calculations/calculation models#Closing a Record|Closing a Record]]. A model
with `calculate_on_create` answers a create with the record already `IN_PROGRESS` — see
[[reference/CalculationModel Internals#Calculating on Create|Calculating on Create]].

`permission_create` is asked twice: on a blank record, then on the record as the request
fills it, before anything is saved. A refused create or delete answers `401` or `403`, a
refused edit `400` — see [Errors](#errors).

### The calculationId

`<calculationId>` is chosen by the client. It names the request as an operation: the audit
entry the request writes is filed under it, and so are any log lines it produces. For a
calculation it becomes the run's id. The interface sends:

| Request | `calculationId` |
|---|---|
| Create | `<model>_create_<uuid>` |
| Change, calculate, cancel | `<model>_<pk>_update_<uuid>` |
| Read | Anything — the interface sends `undefined` |
| History | Anything — the interface sends `default` |

Use a fresh id for every write, in these shapes. A run is found again by its prefix
`<model>_<pk>_`: that is how `lex_reserved_calculation_id` picks a row's newest run, and how
the grid opens its log.

## Calculations over REST

A calculation model's Calculate and Cancel buttons are `PATCH` requests on the record's own
route, with a control key in the body instead of fields. The caller must be allowed to edit
the record: one whose `permission_edit` refuses it answers `400` `EDIT_NOT_ALLOWED` to both.

### Starting a run

```
PATCH api/model_entries/invoice/invoice_42_update_6f1c2b9e-0d4a-4f7e-9a51-3c2d8e7b1a00/one/42

{"calculate": true}
```

| Status | Body | When |
|---|---|---|
| `202` | The record, with `is_calculated: "IN_PROGRESS"` | The run started. It goes on after the answer, in the web process, or on Celery when Celery is on |
| `200` | The record as it is | A run of this record is already going; no second one starts |
| `409` | `{"detail": "<reason>", "code": "calculation_closed"}` | The record is closed: its `calculation_closed_reason()` gave a reason — see [[reference/CalculationModel Internals#Closing a Record\|Closing a Record]] |

`calculate` is read from the body — not from the query string — as text in any letter case,
so JSON `true`, `"true"` and the multipart field `calculate=true` the interface sends all
start a run. Send it on its own. The `calculationId` in the path becomes the run's id — its
log lines and status messages carry it — so give it the `<model>_<pk>_update_<uuid>` shape
the interface uses.

### Cancelling a run

```
PATCH api/model_entries/invoice/invoice_42_update_<uuid>/one/42

{"cancel": true}
```

Only a run on Celery can be cancelled: its task is revoked, the record is set to `CANCELLED`,
and the runs started under it in the same calculation are cancelled too — see
[[reference/CalculationModel Internals#Cancelling a Running Calculation|Cancelling a Running Calculation]].
The answer is the cancel report, `202` when it cancelled and `409` when it did not:

```json
{"cancelled": true, "cancellable": true, "status": "CANCELLED", "revoked_tasks": ["5b0e…"], "descendants_cancelled": 2}
```

A `409` report has `"cancelled": false` and a `reason`: `not_in_progress` when the record is
not running, `sync_calculation_not_cancellable` when its run is in the web process rather
than on Celery. Like `calculate`, `cancel` is read from the body only; it is read before
anything else there and changes no field.

### Following a run

| To | Use |
|---|---|
| Know when it ends | `ws/calculation_status_update` — see [WebSockets](#websockets) |
| Read its lines as they are written | `ws/calculation_logs/<model>_<pk>-<calculationId>` |
| List its log entries | `GET api/calculationlog/tree/?calculation_id=<calculationId>` |
| Download its log | `GET api/download-pdf/<pk>/` — see [[calculations/logging#Downloading the log as PDF\|Logging]] |
| See every run going now | `GET api/active-calculations` |

`api/calculationlog/tree/` answers the run's log entries a page at a time — `limit`
(default 1,000, at most 5,000) and `offset`:

```json
{
  "data": [
    {"id": 311, "title": "Invoice 42", "isRoot": true, "children": [312, 315]},
    {"id": 312, "title": "Loading rates", "children": []}
  ],
  "pagination": {"limit": 1000, "offset": 0, "total": 2, "has_more": false}
}
```

`isRoot` appears on top-level entries only. With `LEX_SEC_INTERNAL`, the default, a caller
who is not a superuser sees a calculation's entries only when one of them belongs to a record
they may read, and nothing at all without `calculation_id`.

`api/active-calculations` answers `{"active_calculations": [...]}`, with entries shaped like
those of the reconciliation message under [WebSockets](#websockets). Besides a signed-in
user or an API key, it accepts the instance's own `LEX_API_KEY`.

How a run and its log look in the interface: [[using-the-app/the-grid/calculations|Calculations]].

## Bulk changes

`api/model_entries/<model>/many` reads or deletes the records whose primary keys are in
`ids`, repeated once per key: `?ids=42&ids=43`.

| Request | Answers |
|---|---|
| `GET …/many?ids=42&ids=43` | `200` and the records the caller may read, as an array. An id the caller may not read is left out, not refused |
| `DELETE …/many?ids=42&ids=43` | `200` and the ids deleted: `[42, 43]` |

> [!warning] Without `ids`, every row
> `ids` is the only thing that narrows this route. A request with **no `ids` parameter**
> addresses every row of the model, so `DELETE api/model_entries/<model>/many` deletes the
> whole table when the caller may read and delete every row. A client that builds the query
> string from an empty selection sends no `ids` at all: check the selection before you send
> the request. (`?ids=` with an empty value addresses no row.)

A bulk delete is all or nothing. It is checked in this order, before anything is deleted:

1. A history table, or Django's users and groups: anyone but a superuser gets `400`
   `EDIT_NOT_ALLOWED`.
2. Any addressed row the caller may not read: `404`.
3. Every row is asked `permission_delete`, as a single delete is. When it refuses any of
   them, the answer is `400` with the refused ids:

   ```json
   {
     "code": "DELETE_NOT_ALLOWED",
     "detail": "You may not delete some of these records.",
     "error": "You may not delete some of these records.",
     "ids": [43]
   }
   ```

4. Otherwise every row is deleted, each with its own audit entry, and one `record_mutation`
   message tells open lists.

Steps 2 and 3 follow `LEX_SEC_NOT_FOUND` and `LEX_SEC_EDIT`, both on by default; with them
off, neither check runs — see [[reference/Environment Variables#Security|Environment Variables]].

`PATCH` on this route is not a working bulk edit in lex-app 2.5: `permission_edit` is asked
for every row, but no row is ever changed — the request ends in an error. Change records one
at a time, as under [Single records](#single-records).

## History

`GET api/model_entries/<model>/<calculationId>/history/<pk>` answers a record's versions,
newest first. `<calculationId>` is not read — the interface sends `default` — and neither is
`?serializer=`: each snapshot is rendered with the model's `default` serializer.

```json
[
  {
    "history_id": 7,
    "valid_from": "2026-03-01T00:00:00Z",
    "valid_to": null,
    "history_type": "~",
    "change_reason": "Rate corrected",
    "user": {"id": 3, "email": "jane@example.com", "name": "Jane Doe"},
    "snapshot": {"id": 42, "amount": "1250.00"},
    "system_history": [
      {"sys_from": "2026-03-02T09:30:00Z", "sys_to": null, "task_status": "NONE", "task_name": null, "change_reason": null}
    ]
  }
]
```

| Key | Meaning |
|---|---|
| `valid_from`, `valid_to` | When this version was true; `valid_to` is `null` for the current one |
| `history_type` | `+` created, `~` changed, `-` deleted |
| `change_reason` | Why it changed, when a reason was recorded |
| `user` | Who changed it: `{"id": …, "name": "Unknown User"}` when that user is gone, `null` when none is recorded |
| `snapshot` | The record's fields in this version |
| `system_history` | When the system held this version: one entry per stored state, with `sys_from` and `sys_to`. For a future-dated version, `task_name` names what will activate it — a Celery task, a local timer, or a `db_applier_…` marker when the database applies it — and `task_status` its state; otherwise `task_name` is `null` and `task_status` is `NONE` |

With `?as_of=<date-time>` the answer is the versions as the system knew them at that
moment — system time — where the list's `as_of` answers the rows as they were valid at that
moment — valid time. [[history-and-audit/bitemporal history|Bitemporal history]] explains the
two.

`as_of` takes an ISO 8601 date-time: `2026-02-01T09:00:00Z`, or with an offset. A value
without an offset is read as UTC, and a date alone — `2026-02-01` — as midnight UTC of that
day. A value that cannot be read is ignored — a date alone that does not exist, such as
`2026-13-45`, among them: the list answers its current rows, the history every version. A
date-time whose date or time does not exist — `2026-13-45T10:00`, `2026-02-01T25:00` —
answers `500`. On the list, `as_of` works only for a model that keeps history; on any other
the request fails with `500`.

| Status | Body | When |
|---|---|---|
| `400` | `{"error": "Model Invoice does not track history."}` | The model keeps no history |
| `404` | `{"detail": "Not found."}` | The caller may not read the record (`LEX_SEC_READ_SURFACES`). For a deleted record, its last version decides |

A version the caller may not read comes back with an empty `snapshot`, and its `user` and
`change_reason` are `null`.

## Model information

### The model tree

`GET api/model-structure` answers the tree the sidebar draws, without the models the caller
may not list:

```json
{
  "modelHierarchy": {
    "Billing": {
      "readable_name": "Billing",
      "type": "Folder",
      "children": {
        "invoice": {
          "readable_name": "Invoice",
          "type": "Model",
          "available_serializers": ["default", "summary"],
          "default_serializer_name": "default"
        }
      }
    }
  },
  "historicalModels": {
    "historicalinvoice": {"type": "Model", "...": "..."}
  }
}
```

| Key | Meaning |
|---|---|
| `readable_name` | The name the sidebar shows |
| `type` | `Folder`, `Model` or `HTMLReport` |
| `children` | A folder's nodes, keyed by folder name or by `<model>` |
| `available_serializers` | The names `?serializer=` accepts for this model |
| `default_serializer_name` | The name the framework's own serializer is registered under: `default`, unless the project renamed it — see [[model-your-data/serializers#Renaming the framework serializer\|Serializers]]. The interface writes through it |
| `tab_display_names` | The record page's tab labels, when `TAB_DISPLAY_NAMES` in [[reference/lex_config\|lex_config.py]] sets them |

`historicalModels` holds the history tables the tree does not place, in the same shape. A
model whose `permission_list` refuses the caller is left out, and a folder left empty goes
with it. How the tree is built: [[model-your-data/model structure|Model structure]].

### Model permissions

`GET api/<model>/model-permissions` answers what the caller may do with the model in
general — what the interface asks before it offers to create, edit or delete:

```json
{"invoice": {"can_read_in_general": true, "can_modify_in_general": true, "can_create_in_general": false, "can_delete_in_general": true}}
```

Each flag is a permission method of the model, asked on a blank record: `permission_list`
for `can_read_in_general`, then `permission_edit`, `permission_create` and
`permission_delete`. A legacy `modification_restriction` that refuses makes the flag `false`
as well. Whether one record may be changed is in its `lex_reserved_scopes`.

### Project, widgets and reports

| Request | Answers |
|---|---|
| `GET api/project-info` | `{"project_name": …, "branch_name": …, "environment": …}`, read from `LEX_SUBMODEL_NAME`, `LEX_SUBMODEL_BRANCH` and `DEPLOYMENT_ENVIRONMENT`; `null` where unset |
| `GET api/widget_structure` | `{"widget_structure": [...]}`: what `get_widget_structure()` in the project's `*_structure.py` returns, `[]` without one — see [[model-your-data/model structure\|Model structure]] |
| `GET api/htmlreport/<name>` | The report page's HTML as a JSON string, with `Cache-Control: no-store`. `<name>` is the report's class name in lower case; any other name answers `404` — see [[model-your-data/HTML reports\|HTML reports]] |

## Search

`GET api/global-search/?q=<text>` — or `api/global-search/<text>` — searches the models the
caller may read, with PostgreSQL full-text search:

```json
{
  "data": [
    {
      "id": "42",
      "type": "Invoice",
      "model": "invoice",
      "url": "/invoice/42/show",
      "content": {"id": "42", "label": "Model: Invoice", "description": "Invoice 42"}
    }
  ],
  "total": 1
}
```

- It matches words, not parts of words: `inv` does not find *Invoice*.
- Columns of the types `IntegerField`, `FloatField`, `BooleanField`, `ForeignKey`,
  `FileField`, `ImageField`, `XLSXField` and `PDFField` are skipped; every other column is
  searched, text, choice, date and decimal columns among them.
- Users, groups, permissions, content types, calculation logs and a few internal tables are
  never searched.
- Only rows the caller may read come back (`LEX_SEC_READ_SURFACES`).
- `limit`, `offset` and `model` are ignored — the interface sends them — and every match
  comes back at once.
- When nothing matches, or `q` is empty, the answer is `200` with the bare JSON string
  `"No match found"`, not an empty `data`.

## Exports and file downloads

### Exports

`POST api/<model>/export` answers an Excel workbook — `.xlsx`, the only format — of the rows
the caller may read:

```json
{
  "ag_export": {
    "request": {"filterModel": {"status": {"filterType": "set", "values": ["open"]}}, "sortModel": [], "rowGroupCols": []},
    "columns": [
      {"colId": "status", "dataKey": "status", "field": "status", "headerName": "Status"},
      {"colId": "amount", "dataKey": "amount", "field": "amount", "headerName": "Amount"}
    ],
    "includeColumnLabels": true,
    "selection": {"groupKeyPaths": [[{"field": "region", "key": "3"}]]}
  },
  "filtered_export": "aWRzPTQyJmlkcz00Mw==",
  "timezone": "Europe/Berlin"
}
```

| Key | Meaning |
|---|---|
| `ag_export.request` | The grid's request, as the list's `POST` takes it: filters, sorting, grouping, pivot. `startRow` and `endRow` are ignored — the export reads up to 1,000,000 rows and stops there |
| `ag_export.columns` | The columns, in order. Each names its data by `dataKey`, else `field`, else `colId`, and its heading by `headerName`. Columns the grid draws itself (`ag-Grid-…`) are skipped |
| `ag_export.includeColumnLabels` | `false` heads the columns with their field names instead. Default `true` |
| `ag_export.selection.groupKeyPaths` | Selected row groups, each the path of `{field, key}` from the outermost group in |
| `filtered_export` | The selected rows: the query string `ids=42&ids=43`, base64-encoded. Only its `ids` are read |
| `timezone` | The IANA zone date-times are written in, since Excel has none. Also a query parameter, which wins. Default: the server's `TIME_ZONE` |

`?as_of=<date-time>` exports the rows as they were valid then, as on the list.

- With `ag_export`, a selection — ids, group paths or both — narrows the export to those rows
  and groups. Without `ag_export`, the ids in `filtered_export` narrow it, and the workbook
  has every field of the model.
- Rows the caller may not read are left out (`LEX_SEC_READ_SURFACES`); fields
  `permission_export` withholds are left out or left empty.
- When nothing is left, the answer is `404` `{"error": "No data available for export"}`.
- Every export writes an audit entry — see
  [[history-and-audit/audit logs#Exports and Downloads|Audit logs]].

### File downloads

`GET api/<model>/file-download?pk=<pk>&field=<field>` answers the file in a record's file
field. What comes back depends on the instance's storage, `STORAGE_TYPE` — see
[[reference/Environment Variables#File storage|Environment Variables]]:

| Storage | Answer |
|---|---|
| Local disk | The file |
| `SHAREPOINT` | The file, fetched from SharePoint |
| `GCS` | `{"download_url": "<url>"}` — fetch the file from that URL |

| Status | Body | When |
|---|---|---|
| `400` | `{"error": "Both 'pk' and 'field' are required."}` | `pk` or `field` is missing |
| `404` | `{"error": "File not found"}` | No such record; `field` is not a file field, or it is empty; or the caller may not read the record or that field (`LEX_SEC_READ_SURFACES`) |
| `404` | `{"error": "File not found on server"}` | Local storage: the file is gone from disk |

Downloads are audited too. SharePoint files have routes of their own, listed with their audit
entries in [[history-and-audit/audit logs#Exports and Downloads|Audit logs]]; the calculation
log's PDF is in [[calculations/logging#Downloading the log as PDF|Logging]].

## API keys

An API key signs a request in without a user — for a scheduler, an ETL job or another
system. Keys are rows of the `APIKey` model from `rest_framework_api_key` (the
`exc-djangorestframework-api-key` package lex-app installs). Lex App 2.5.0 has no screen for
them — its Django admin does not register the model — so create one from a shell on the
instance, `lex shell`:

```python
from rest_framework_api_key.models import APIKey

api_key, key = APIKey.objects.create_key(name="nightly-export")
print(key)  # only now: the database keeps a hash
```

Keep the printed key then; it cannot be shown again. A key stops working when it is revoked
(`revoked=True`), which cannot be undone, or when its `expiry_date` has passed.

A request carries the key in one of three places. They are tried in this order, so a header
wins:

| Where | Example |
|---|---|
| The `Api-Key` header | `Api-Key: <key>` |
| The `Authorization` header | `Authorization: Api-Key <key>` |
| The `api_key` query parameter | `?api_key=<key>` |

```bash
curl -H "Api-Key: <key>" \
  "https://<instance host>/api/api/model_entries/invoice/list?perPage=100&ordering=id"
```

> [!warning] A key in a URL gets written down
> `?api_key=` is for callers that can only be configured with a URL. Access logs, proxies and
> browser histories record URLs, and the key with them. Send a header wherever you can.

**Who the caller is.** A request with a key and no sign-in is a technical user, the same for
every key:

| In `user_context` | For a key |
|---|---|
| `user` | A technical user named after the key — not a Django user |
| `keycloak_scopes` | All six — `create`, `read`, `edit`, `delete`, `list`, `export` — when a record is checked on its own; empty in a list's check of each row, since those are rebuilt from Keycloak permissions, which a key has none of |
| `client_roles` | `{"api_key"}` |
| `groups` | Empty |
| `is_superuser` | `False`, always |

The audit log records it as `Technical User`. A request that also carries a session or a
bearer token is that user, and the key plays no part in its permissions. A permission method
can tell a key by `"api_key" in user_context.client_roles`.

With LexModel's default permission methods a key may therefore create, read, change, delete,
list and export. Three things differ from a user's sign-in:

- **Lists can come back empty.** A model that keeps the default `permission_read` is listed
  by the caller's Keycloak permissions, which a key does not have. For a key, its list, value
  lists, bulk reads and search results are empty, and its exports and bulk deletes answer
  `404` — while the same record read on its own, `one/<pk>`, answers. Override
  `permission_read` and the list asks your method for each row instead; there a key's
  `keycloak_scopes` are empty, so let the method recognise the key by
  `"api_key" in user_context.client_roles` — see
  [[access-and-dashboards/permissions#Callers with an API key|Permissions]].
- **A refusal reads as signed out.** A create or single delete that a permission method
  refuses answers a key `401` `not_authenticated`, not `403`: that answer is chosen by the
  Django user, which a key does not have.
- **Not everywhere.** WebSockets read the session cookie only — see
  [WebSockets](#websockets) — and `api/user/` and the calculation-log PDF need a sign-in.

`LEX_API_KEY`, the instance's own key for the platform (see
[[reference/Environment Variables#Deployment identity|Environment Variables]]), is not a key
row: of the routes on this page it opens only `api/active-calculations`.

## WebSockets

Live updates come over WebSockets at `ws/<route>` on Lex App's address:
`ws://localhost:8000/ws/<route>` locally, `wss://<instance host>/api/ws/<route>` on a hosted
instance. A socket is signed in by the session cookie only: an API key or a bearer token signs
no socket in. Under `LEX_WS_AUTH=enforce` such a socket is accepted and then closed with
`4401`; under the default, `log`, it is served and logged — see
[[ship-and-operate/deploying#Live updates and sign-in|Deploying]].

| Route | Sends |
|---|---|
| `ws/calculation_status_update` | `calculation_reconciliation` once, on connect; then a message whenever a calculation's status changes |
| `ws/model_data_update` | `record_mutation` after each create, change and delete through the REST API |
| `ws/calculation_logs/<model>_<pk>-<calculationId>` | `calculation_log_real_time`, as log lines are written |
| `ws/calculations` | `calculation_id` when a run starts |
| `ws/logs` | Nothing pushed: lex-app 2.5 sends no messages on it. A text message you send gets a fixed `{"STATUS": …}` reply |
| `ws/health` | Public. Answers any message with `{"status": "Healthy :)"}` |

### Calculation status

On connect, `ws/calculation_status_update` sends the runs going now:

```json
{
  "type": "calculation_reconciliation",
  "payload": {
    "active_calculations": [
      {
        "record_id": "invoice_42",
        "record": "Invoice 42",
        "calculation_id": "invoice_42_update_6f1c2b9e-…",
        "model_name": "Invoice",
        "model_label": "billing.invoice",
        "record_pk": "42"
      }
    ]
  }
}
```

Then one message for each change:

```json
{
  "type": "calculation_success",
  "payload": {
    "record": "Invoice 42",
    "record_id": "invoice_42",
    "model_name": "invoice",
    "calculation_id": "invoice_42_update_6f1c2b9e-…"
  }
}
```

| `type` | Sent when |
|---|---|
| `calculation_in_progress` | A run started |
| `calculation_success` | It finished |
| `calculation_error` | It failed. The payload can add `message` and `traceback` |
| `calculation_aborted` | At startup, for a run that a crash left `IN_PROGRESS` |
| `calculation_cancelled` | It was cancelled |

`record_id` is `<model>_<pk>`. `calculation_id` is left out when the run's id is not known.

### Record changes

```json
{"type": "record_mutation", "payload": {"model_name": "invoice", "action": "updated", "record_id": "invoice_42"}}
```

`action` is `created`, `updated` or `deleted`. A bulk delete sends one message, without
`record_id`. The message goes out once the change is committed. Only the REST routes send it:
rows that code writes — a calculation's results, for example — announce nothing, and a
calculate request reports through the status messages instead.

### Log lines

`ws/calculation_logs/<model>_<pk>-<calculationId>` joins the channel of the record named
before the first `-`, and sends each line logged for that record — or for a run nested under
it — as it is written:

```json
{"type": "calculation_log_real_time", "logs": "\n## Loading rates"}
```

`logs` is the new Markdown, starting with a line break. The socket does not filter by
`calculationId`; the stored log of one run is listed by `api/calculationlog/tree/` — see
[Following a run](#following-a-run).

`ws/calculations` sends
`{"type": "calculation_id", "payload": {"calculation_record": "invoice_42", "calculation_id": "…"}}`
when a run starts: from the Calculate button, a `calculate_on_create` create, or a scheduled
job.

## Errors

Sign-in problems answer as the HTTP auth contract says — `401` to sign in again, `403`
`permission_denied` or `csrf_failed`, `503` `auth_unavailable` to retry — see
[[access-and-dashboards/sign-in and sessions#For frontends and API clients|Sign-in and sessions]].
The routes' own answers:

| Status | Body | When |
|---|---|---|
| `400` | `{"<field>": ["…"]}` | A create or change the serializer rejects |
| `400` | `{"code": "EDIT_NOT_ALLOWED", "detail": "You may not edit: amount.", "error": "…", "fields": {"amount": "Not editable."}}` | A change to fields that `permission_edit` withholds. A record it refuses outright answers without `fields`, for `calculate` and `cancel` too; so does any write to a history table, or to Django's users and groups, by anyone but a superuser |
| `400` | `{"code": "DELETE_NOT_ALLOWED", "detail": "…", "error": "…", "ids": [43]}` | A bulk delete with rows that `permission_delete` refuses — see [Bulk changes](#bulk-changes) |
| `400` | `{"code": "FILTER_NOT_ALLOWED", "detail": "…", "error": "…"}` | A filter or ordering on a field the caller may not filter by |
| `400` | `{"error": "Unknown serializer 'summary' for model 'invoice'", "available": ["default"]}` | An unknown `?serializer=`. The fields endpoint answers this with `500` |
| `401` or `403` | `{"message": "You are not authorized to create a record in Invoice", "detail": "…", "code": "permission_denied"}` | A create or a single delete that the permission methods refuse: `403` for a signed-in user; `401`, with `"code": "not_authenticated"`, `error` and `login_url`, for a caller without a sign-in — an API key included |
| `404` | `{"detail": "Not found."}` — on an unknown route also `"error": "Not found."` | A record, history or bulk request naming a row the caller may not read; a list whose model's `permission_list` refuses the caller; an unknown route under `api/` |
| `409` | `{"detail": "…", "code": "calculation_closed"}` | `calculate` on a closed record |
| `409` | The cancel report | `cancel` with nothing to cancel |
| `500` | `{"error": "…"}`; a failing calculation on a change answers `{"message": "…", "traceback": …}` instead | Anything else that fails in a create, change or delete. A `traceback` is added only where tracebacks may be shown — see `LEX_API_TRACEBACKS` in [[reference/Environment Variables#Security\|Environment Variables]] |

## See also

- [[ship-and-operate/custom frontend|Custom frontend]] — serving your own app against this API
- [[reference/Grid Filter API|Grid Filter API]] — value lists, the fields endpoint, and what may be filtered
- [[access-and-dashboards/sign-in and sessions|Sign-in and sessions]] — the HTTP auth contract
- [[access-and-dashboards/permissions|Permissions]] — the methods behind every answer
- [[history-and-audit/audit logs|Audit logs]] — what each request records
- [[reference/Environment Variables#Security|Environment Variables]] — the security switches
