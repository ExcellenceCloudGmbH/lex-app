---
title: "Grid Filter API"
---

The grid's [[using-the-app/the-grid/filtering and sorting#Value Lists|value lists]] run on
two endpoints, and the list endpoint's GET has lookups for the values they list. This page
is for calling them yourself — from a script, or from a dashboard of your own. Which
columns get a value list is explained under
[[model-your-data/serializers#Value lists|Serializers]].

They take the same sign-in as the rest of Lex App's [[reference/REST API|REST API]], and
apply the user's read permissions exactly as the grid does;
[[access-and-dashboards/reflex/sessions and authentication#Calling the Lex App API as the user|Calling the Lex App API as the user]]
shows a request with a bearer token. Paths are relative to Lex App's own address.

## The fields endpoint

`GET api/model_info/<model>/fields` describes a list's columns: one entry for each field of
the serializer `?serializer=` names (`default` when it is left out), except `id_field` and
`short_description`.

```json
{
  "fields": [
    {
      "name": "status", "readable_name": "Status", "type": "string",
      "editable": true, "required": false, "default_value": "open", "is_pk": false,
      "choices": [{"id": "open", "name": "Open"}, {"id": "overdue", "name": "Overdue"}],
      "is_groupable": true, "value_list": true
    },
    {
      "name": "region", "readable_name": "Region", "type": "foreign_key",
      "editable": true, "required": false, "default_value": null, "is_pk": false,
      "target": "region", "limit_choices_to": {},
      "is_groupable": true, "value_list": true
    },
    {
      "name": "amount", "readable_name": "Amount", "type": "float",
      "editable": true, "required": true, "default_value": null, "is_pk": false,
      "is_groupable": true, "value_list": false
    },
    {
      "name": "days_overdue", "readable_name": "Days Overdue", "type": "int",
      "editable": false, "required": false, "default_value": null,
      "is_groupable": false, "value_list": false
    }
  ],
  "id_field": "id",
  "list_ui": {"hide_actions_column": false, "columns": {"id": {"max_width": 90}}}
}
```

Here `amount` is a `DecimalField`, and `days_overdue` a `SerializerMethodField` the
serializer types as `int`.

| Key | Meaning |
|---|---|
| `name` | The field's name — for a serializer field renamed with `source=`, the name of the model field behind it |
| `readable_name` | Its label in title case: the model field's `verbose_name`, or the serializer field's label |
| `type` | `string`, `int`, `float`, `boolean`, `date`, `date_time`, `foreign_key`, `file`, `pdf_file`, `xlsx_file`, `image_file` or `json`. A model field is typed by its class, subclasses included: a `DecimalField` reports `float`, a `OneToOneField` `foreign_key`, and a class not in this list — `TextField`, `TimeField`, a many-to-many field — `string`. A field only the serializer has is typed by its serializer field class, `string` when that is unknown, unless the serializer's `Meta.lex_field_type_overrides` names its type |
| `editable` | Whether a form may change it: `false` for a model field declared `editable=False` and for an auto-increment primary key; for a serializer-only field, `false` when it is read-only |
| `required` | Whether a create must send it: a model field is required unless it has `blank=True`, `null=True` or a default. A serializer-only field is required when its serializer field is |
| `default_value` | The model field's default. For a serializer-only field, its default when that is text, a number or a boolean; otherwise `null` |
| `is_pk` | Whether it is the primary key. Model fields only |
| `is_groupable` | Whether the grid may group and pivot by it: `true` for a model field, `false` for a field with no database column behind it |
| `choices` | The allowed values, `[{"id": <value>, "name": <label>}]`, grouped choices flattened. Only on a field with choices |
| `target` | For a foreign key, or a serializer's `PrimaryKeyRelatedField`: the related model, named as `<model>` in a path |
| `limit_choices_to` | For a model foreign key: its `limit_choices_to`, `{}` when it has none |
| `value_list` | Whether `filter_values` lists its values — see [Which columns have a value list](#which-columns-have-a-value-list) |

At the top level, `id_field` names the model's primary key, and `list_ui` carries the
serializer's display defaults: `hide_actions_column`, and `columns`, the per-column options
of `Meta.lex_column_options` that passed validation — `{}` when there are none. These `Meta`
options, with `lex_field_type_overrides`, are explained in
[[model-your-data/serializers|Serializers]].

An unknown `?serializer=` answers `500` here, with the names there are —
`{"error": "Unknown serializer 'summary' for model 'invoice'", "available": ["default"]}` —
where the list and record routes answer the same body with `400`.

## Which columns have a value list

Every entry of the fields endpoint carries `value_list`:

- `value_list` is `true` exactly when `filter_values` lists that column's values through
  this serializer, for the caller asking. A field with no database column behind it is
  always `false`, and so is a column the caller may not filter by — see
  [Security switches](#security-switches).
- The grid offers a value list on every column where it is `true`, except the primary
  key.

## A column's values: `filter_values`

`POST api/model_entries/<model>/filter_values`

`as_of` and `serializer` are query parameters, read exactly as the list endpoint reads
them. The body:

```json
{
  "field": "region",
  "filterModel": {
    "status": {"filterType": "set", "values": ["open", "overdue"]}
  },
  "selected": ["3"],
  "limit": 200
}
```

| Key | Required | Meaning |
|---|---|---|
| `field` | yes | The column to list, by name: a value-list column of this list |
| `filterModel` | no | The grid's filter model, in AG Grid's format. Every entry applies except `field`'s own, so the answer is what the *other* filters leave |
| `selected` | no | The keys ticked now. Each appears in the answer even when no row has it any more. The first 1,000 are read |
| `search` | no | Keeps the values whose label contains this text, ignoring case — `"no"` keeps *North* |
| `limit` | no | How many values to return. Default `200`, at most `1000` — the grid's own column filters ask for `200`, and reach further values with `search` rather than a higher `limit` |

The answer:

```json
{
  "values": [
    {"key": "3", "label": "North", "count": 41},
    {"key": "5", "label": "South", "count": 12},
    {"key": null, "label": "(Blank)", "count": 3}
  ],
  "truncated": false
}
```

| Key | Meaning |
|---|---|
| `key` | The stored value as a string: `"true"` or `"false"`, a date as `YYYY-MM-DD`, a number, or for a foreign key the related record's primary key — the value of its `to_field` instead, when the foreign key names one. `null` is blank — on a text column without choices, empty text too. It is the key a `set` filter takes |
| `label` | What the value list shows: the choice's label, the related record's name (its `__str__`), `Yes` or `No`, `(Blank)`, or else the key itself |
| `count` | The rows with this value, under the other filters |
| `truncated` | `true` when the list is not complete: more values matched than `limit`, or the server stopped counting — after 5,000 distinct values, or after reading 50,000 rows of a model whose read permission is decided row by row. Narrow it with `search` |

- Values are sorted by label — integer columns numerically — with blank last.
- A key from `selected` that missed the cut is added after it, with its count or `0`, so
  `values` can be longer than `limit`. While `search` is set, only the ones whose label
  matches are added.
- A row the user may not read is never counted. On a model whose read permission can
  hide a field on some rows, only the rows where the user may read this column count.
  A ticked key that no readable row holds is listed under its own key, never under the
  related record's name.
- A row whose permission check raises is not counted either — see
  [Security switches](#security-switches).

### Set and multi filters

`filterModel` takes AG Grid's condition filters, and the two shapes value lists add.
The list endpoint and exports read them the same way:

| Shape | Matches |
|---|---|
| `{"filterType": "set", "values": ["open", null]}` | Rows whose column holds any of the keys, written as `key` above. `null` matches blank. An empty `values` list matches no rows |
| `{"filterType": "multi", "filterModels": [condition, set]}` | Rows that match both parts. Either part may be `null` |

The grid never sends an empty `set`: a value list with nothing ticked leaves the column
out of the filter model.

### Errors

| Status | Body | When |
|---|---|---|
| `400` | `{"field": "'notes' is not filterable."}` | `field` isn't a value-list column of this list, or the caller may not filter by it. The message is the same whatever the reason |
| `400` | `{"filterModel": {"year": "'abc' is not a valid value for year."}}` | A `set` key that can't be read as the column's type — the list endpoint answers the same |
| `400` | `{"filterModel": "must be an object"}` or `{"selected": "must be a list"}` | The body has the wrong shape |
| `404` | | The model's `permission_list` refuses the caller — its list answers `404` too |
| `405` | | The request is a `GET` |

## Value-list lookups on the list GET

`GET api/model_entries/<model>/list` narrows its rows by query parameters, such as
`?status=open` or `?year__gte=2025` — every parameter and lookup is listed under
[[reference/REST API#The list endpoint|REST API]]. Three lookups match what value lists
hold: keys with `__in` and `__exact`, and blanks with `__blank`. On a field the caller may not
filter by, each of them answers `400` with the code `FILTER_NOT_ALLOWED`, like any
other filter on it — see [Security switches](#security-switches).

### Keys: `__in` and `__exact`

| Parameter | Keeps |
|---|---|
| `?status__in=open,overdue` | The rows holding any of these keys. The parameter can also be repeated |
| `?region__exact=3` | The rows holding this key |

- A key is written as a value list's `key`, and read as the column's type, the way a
  `set` filter reads it. So `?status__exact=null` no longer means *is empty*: it looks
  for the text `null`. Ask `?status__isnull=true` for NULL, or `?status__blank=true`
  for what *(Blank)* finds.
- A key that can't be read as the column's type is dropped, and when none is left no
  row matches: `?region__in=abc` keeps no rows.
- Two readings are unchanged: a bare `?status=null`, with no lookup, still means NULL,
  and a JSON field, or a path into one, still reads `5`, `true` and `null` as JSON.

### Blanks: `__blank`

The `__blank` lookup finds the rows a value list's *(Blank)* finds:

| Parameter | Keeps |
|---|---|
| `?region__blank=true` | The rows whose `region` is blank |
| `?region__blank=false` | Every other row |

- Blank means NULL — and on a text column without choices, empty text as well.
- Only a column of the model itself, a foreign key included: `customer__blank` tests the
  foreign key. A path through a relation (`customer__name__blank`) or into a JSON field
  is ignored, like any lookup the list doesn't know.
- A value other than `true` or `false`, in any letter case, is ignored too.

## Which fields may be filtered

Filtering by a field reveals its values even when the field is never returned:
`?salary__gte=100000` answers who earns that much, one request at a time. So with the
`FILTERS` switch on — the default — a list request may filter, order and group only by
fields the caller can already see. A field may be used when it is

- a field of any serializer registered for the model, not only the one the request names;
- a key of the `limit_choices_to` of a foreign key that points at the model, since the
  interface's pickers filter by those;
- or listed in the model's `lex_filterable_fields`;

and it is neither

- named like a credential — the names no response carries, see
  [[model-your-data/serializers#Some fields never reach a response, whatever you declare|Serializers]] —
  even when `lex_filterable_fields` lists it;
- nor a field `permission_read` withholds from the caller on every record, as asked on a
  blank record.

The primary key is always allowed. A path through a relation, `customer__country`, needs
every step allowed on its own model, and a related model that no route serves allows
nothing. A name that is not a field at all is ignored, as on the list, never refused.

A field the framework's own serializer renders is filterable already.
`lex_filterable_fields` is for a field your serializers leave out:

```python
from django.db import models

from lex.core.models.LexModel import LexModel


class Invoice(LexModel):
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    internal_ref = models.CharField(max_length=50)

    # The serializers in api_serializers leave internal_ref out;
    # list requests may still filter and order by it.
    lex_filterable_fields = ["internal_ref"]
```

`lex_filterable_fields = "__all__"` takes a model out of the check: every field may be
filtered again. The framework's own tables — the audit log, the calculation log and the
history tables — are never restricted.

What a refused field answers depends on where it is used:

| Where | Answer |
|---|---|
| A GET filter, or `ordering` | `400` `{"code": "FILTER_NOT_ALLOWED", "detail": "Filtering or ordering by 'salary' is not allowed.", "error": "…"}` |
| The list's `POST` | The field is skipped in `filterModel`, `sortModel` and grouping |
| `filter_values` | Its own `400` — see [Errors](#errors) |
| The fields endpoint | `value_list: false` |

## Security switches

Value lists answer as the list does under Lex App's
[[reference/Environment Variables#Security|security switches]]. Three of them change
what these endpoints say:

| Switch | On, the default | Off |
|---|---|---|
| `LEX_SEC_FILTERS` | A column the caller may not filter by — see [Which fields may be filtered](#which-fields-may-be-filtered) — has no value list for them: `filter_values` refuses it with the `400` above, the fields endpoint gives it `value_list: false`, and a GET lookup on it answers `400` `FILTER_NOT_ALLOWED`. A `filterModel` entry on such a field is ignored, as the list ignores it | The column has its value list, and filters on it apply |
| `LEX_SEC_FAIL_CLOSED` | A row whose permission check raises is not counted | Such a row is counted wherever the list shows it |
| `LEX_SEC_NOT_FOUND` | When the model's `permission_list` refuses the caller, `filter_values` answers `404`, as the list does | `filter_values` lists the values, as the list lists the rows |

`LEX_LEGACY_SECURITY=true` turns all three off, and a `LEX_SEC_<FLAG>` setting wins over
it.

## See also

- [[reference/REST API|REST API]] — the rest of the list endpoint, and every other route
- [[using-the-app/the-grid/filtering and sorting|Filtering & Sorting]] — value lists, as users see them
- [[model-your-data/serializers#Value lists|Serializers]] — which columns get a value list
- [[access-and-dashboards/streamlit/embedding app pages#Filtering the embedded table|Embedding App Pages]] — an embedded table's filters, set from Python
- [[reference/Environment Variables#Security|Environment Variables]] — the security switches value lists follow
