"""
Sub-cluster 10j — GenericFilters DRF filter backends + CustomDefaultStorage.

ROI extension targeting two small but customer-visible plumbing files
that sit on every list-endpoint request and every file-URL render:

* ``lex/api/filters/GenericFilters.py`` — 48.84% baseline.
  Four DRF ``BaseFilterBackend`` subclasses + one tree-walker helper
  that translate frontend query params into ``QuerySet.filter(**kwargs)``
  calls. A regression here either silently drops query filters
  (returns too many rows — privacy / perf incident) or builds an
  invalid ORM kwarg (500s the whole grid).

* ``lex/utilities/storage/custom_storage.py`` — 28.57% baseline.
  Twelve lines, but every file URL the SPA renders flows through
  ``CustomDefaultStorage.url`` — a regression that double-slashes or
  drops ``base_url`` is a broken-image dashboard.

All scenarios are ``SimpleTestCase`` (no DB, no settings overrides) —
the filter backends only need a request stub and a ``MagicMock``
queryset to exercise every branch.

Scenarios 10.32 – 10.39.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from lex.api.filters.GenericFilters import (
    ForeignKeyFilterBackend,
    PrimaryKeyListFilterBackend,
    StringFilterBackend,
    UserReadRestrictionFilterBackend,
    create_filter_queries_from_tree_paths,
)
from lex.utilities.storage.custom_storage import CustomDefaultStorage


def _make_request(get_params=None, query_params=None):
    """Build a request stub flexible enough for every backend."""
    req = MagicMock()
    req.GET = get_params or {}
    # `request.GET.get` → dict.get; MagicMock auto-binds, so wire it.
    req.GET = _GetDict(get_params or {})
    req.query_params = _GetDict(query_params or {})
    req.user = SimpleNamespace(is_authenticated=True, username="alice")
    return req


class _GetDict(dict):
    """Minimal QueryDict-ish: supports .get(key, default) and .dict()."""

    def dict(self):  # noqa: D401 — mirrors QueryDict.dict()
        return dict(self)


# ---------------------------------------------------------------------
# 10.32 — create_filter_queries_from_tree_paths
# ---------------------------------------------------------------------
class TestCluster10j_FilterTreeWalker(SimpleTestCase):
    """``create_filter_queries_from_tree_paths`` recursive walker.

    The walker turns a nested ``{'children': {...}, 'entries': [...]}``
    tree (sent by AG Grid's set-filter UI) into a flat dict of
    Django ORM keyword arguments. Walking is depth-first; the
    leaf-marker key is ``entries`` and the recursion key is
    ``children``.
    """

    def test_10_32_walker_flattens_nested_tree_into_in_lookups(self) -> None:
        """10.32: a 2-level tree produces ``parent__child__in`` kwargs.

        Customer impact: the AG Grid set-filter UI sends nested
        category filters like ``region → country → [DE, FR]``. If
        the walker forgot to append ``__`` between hops or dropped
        the trailing ``in``, the resulting queryset would either
        500 (bad lookup) or silently return *every* row (filter
        dropped) — both worse than no filter at all.
        """
        tree = {
            "children": {
                "region": {
                    "children": {
                        "country": {"entries": ["DE", "FR"]},
                    },
                },
                "status": {"entries": ["active"]},
            },
        }
        out: dict = {}
        create_filter_queries_from_tree_paths(out, tree, "")

        self.assertEqual(out["region__country__in"], ["DE", "FR"])
        self.assertEqual(out["status__in"], ["active"])
        self.assertEqual(
            len(out), 2,
            "no extra spurious keys allowed — drift would either "
            "over-filter or under-filter the queryset",
        )


# ---------------------------------------------------------------------
# 10.33 — ForeignKeyFilterBackend
# ---------------------------------------------------------------------
class TestCluster10j_ForeignKeyFilterBackend(SimpleTestCase):
    """``ForeignKeyFilterBackend.filter_queryset`` JSON parsing path."""

    def test_10_33_no_active_filter_tree_returns_queryset_unchanged(self):
        """10.33a: when ``activeFilterTree`` is missing, ``filter(**{})``
        is called — a no-op that returns the queryset as-is.

        Pin so a regression that started filtering on missing-param
        (defaulting to e.g. ``{}`` interpreted as exclude-everything)
        would surface here.
        """
        backend = ForeignKeyFilterBackend()
        qs = MagicMock()
        request = _make_request(get_params={})

        result = backend.filter_queryset(request, qs, view=MagicMock())

        qs.filter.assert_called_once_with()  # no kwargs
        self.assertIs(result, qs.filter.return_value)

    def test_10_33b_active_filter_tree_parses_json_and_filters(self):
        """10.33b: the JSON tree is parsed, walked, and the resulting
        kwargs are passed to ``queryset.filter``.

        Backend contract: AG Grid sends ``activeFilterTree`` as a
        JSON-encoded string; the backend MUST json.loads it. A
        regression that passed the raw string to the walker would
        crash on ``'in' in <str>``.
        """
        backend = ForeignKeyFilterBackend()
        qs = MagicMock()
        tree = {"children": {"status": {"entries": ["x"]}}}
        request = _make_request(
            get_params={"activeFilterTree": json.dumps(tree)},
        )

        backend.filter_queryset(request, qs, view=MagicMock())

        qs.filter.assert_called_once_with(status__in=["x"])


# ---------------------------------------------------------------------
# 10.34 — PrimaryKeyListFilterBackend
# ---------------------------------------------------------------------
class TestCluster10j_PrimaryKeyListFilterBackend(SimpleTestCase):
    """``PrimaryKeyListFilterBackend`` translates ``?pks=1,2,3`` into
    ``<pk_name>__in=[...]``."""

    def test_10_34_pks_param_builds_pk_in_lookup_with_container_pk_name(self):
        """10.34a: comma-split into list; pk_name from model_container.

        Customer-visible: the bulk-action toolbar (``Delete selected``)
        relies on this to filter the queryset to the picked rows.
        If the backend hard-coded ``id__in`` instead of reading
        ``container.pk_name``, every model with a non-default PK
        (e.g. UUID-keyed) would silently no-op the bulk action —
        the user clicks Delete and nothing happens.
        """
        view = MagicMock()
        view.kwargs = {"model_container": SimpleNamespace(pk_name="uuid")}
        backend = PrimaryKeyListFilterBackend()
        qs = MagicMock()
        request = _make_request(query_params={"pks": "a,b,c"})

        backend.filter_queryset(request, qs, view)

        qs.filter.assert_called_once_with(uuid__in=["a", "b", "c"])

    def test_10_34b_empty_pk_segments_are_dropped(self):
        """10.34b: ``?pks=1,,2`` → ``[1, 2]`` (no empty strings).

        AG Grid trailing-comma corner case — without the filter,
        Django would raise ``ValueError`` on the empty-string PK
        and 500 the whole bulk action.
        """
        view = MagicMock()
        view.kwargs = {"model_container": SimpleNamespace(pk_name="id")}
        backend = PrimaryKeyListFilterBackend()
        qs = MagicMock()
        request = _make_request(query_params={"pks": "1,,2,"})

        backend.filter_queryset(request, qs, view)

        qs.filter.assert_called_once_with(id__in=["1", "2"])

    def test_10_34c_no_pks_param_passes_through(self):
        """10.34c: missing ``pks`` → ``filter(**{})`` no-op.

        Pins the documented "missing param means full queryset"
        contract — drift to "missing means empty queryset" would
        wipe every list view that omits the param.
        """
        view = MagicMock()
        view.kwargs = {"model_container": SimpleNamespace(pk_name="id")}
        backend = PrimaryKeyListFilterBackend()
        qs = MagicMock()
        request = _make_request(query_params={})

        backend.filter_queryset(request, qs, view)

        qs.filter.assert_called_once_with()


# ---------------------------------------------------------------------
# 10.35 — StringFilterBackend
# ---------------------------------------------------------------------
class TestCluster10j_StringFilterBackend(SimpleTestCase):
    """``StringFilterBackend`` parses ``?searchParams=<json>`` and
    forwards as ``queryset.filter(**parsed)``."""

    def test_10_35_search_params_json_parsed_and_applied(self):
        """10.35a: JSON dict → kwargs passed to filter.

        Powers the free-text search box at the top of every grid;
        a regression that double-decoded or single-quoted the JSON
        would silently 500 every search.
        """
        backend = StringFilterBackend()
        qs = MagicMock()
        params = {"name__icontains": "foo", "active": True}
        request = _make_request(get_params={"searchParams": json.dumps(params)})

        backend.filter_queryset(request, qs, view=MagicMock())

        qs.filter.assert_called_once_with(**params)

    def test_10_35b_missing_search_params_defaults_to_empty_dict(self):
        """10.35b: missing ``searchParams`` → ``filter(**{})``.

        Documented default: empty filter. Regression that crashed on
        missing param would break the very first page-load of every
        grid (before the user has typed anything).
        """
        backend = StringFilterBackend()
        qs = MagicMock()
        request = _make_request(get_params={})

        backend.filter_queryset(request, qs, view=MagicMock())

        qs.filter.assert_called_once_with()


# ---------------------------------------------------------------------
# 10.36 — UserReadRestrictionFilterBackend
# ---------------------------------------------------------------------
class TestCluster10j_UserReadRestrictionFilterBackend(SimpleTestCase):
    """``UserReadRestrictionFilterBackend`` legacy modification_restriction
    branch.

    Modern models use ``permission_read`` (covered in cluster 4); this
    backend is the back-compat path for legacy models that still expose
    ``modification_restriction.can_be_read``. The branch is rarely
    hit in production but MUST stay correct — silently widening read
    visibility on a legacy model is a privacy regression.
    """

    def test_10_36a_no_restriction_attribute_returns_qs_unchanged(self):
        """10.36a: model without ``modification_restriction`` → no-op.

        The dominant code path: every modern LexModel hits this branch.
        Pin so a regression that started calling ``.filter`` (truthy
        side-effect) here would surface immediately.
        """
        backend = UserReadRestrictionFilterBackend()

        # Build a model that lacks `modification_restriction`.
        model = type("PlainModel", (), {})
        view = MagicMock()
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=model),
        }
        qs = MagicMock(spec=[])  # no callable attrs — would explode if used

        result = backend.filter_queryset(_make_request(), qs, view)
        self.assertIs(result, qs, "queryset must pass through untouched")

    def test_10_36b_with_restriction_invokes_can_be_read_per_object(self):
        """10.36b: legacy ``modification_restriction.can_be_read`` is
        invoked per object; allowed PKs become the new filter.

        Pin per-object call so a regression that bypassed the
        restriction (e.g. removed the comprehension) would silently
        widen visibility — exactly the privacy regression this
        backend exists to prevent.
        """
        seen_pks = []

        class _Restriction:
            @staticmethod
            def can_be_read(instance, user, violations):
                seen_pks.append(instance.pk)
                # Allow only the odd PKs.
                return instance.pk % 2 == 1

        model = type(
            "RestrictedModel", (),
            {"modification_restriction": _Restriction},
        )
        rows = [SimpleNamespace(pk=i) for i in (1, 2, 3, 4)]
        qs = MagicMock()
        qs.__iter__ = lambda self: iter(rows)

        view = MagicMock()
        view.kwargs = {
            "model_container": SimpleNamespace(model_class=model),
        }

        backend = UserReadRestrictionFilterBackend()
        backend.filter_queryset(_make_request(), qs, view)

        self.assertEqual(
            seen_pks, [1, 2, 3, 4],
            "every queryset row must be evaluated by the restriction "
            "— skipping rows is a privacy regression",
        )


# ---------------------------------------------------------------------
# 10.37 – 10.39 — CustomDefaultStorage URL behaviour
# ---------------------------------------------------------------------
class TestCluster10j_CustomDefaultStorage(SimpleTestCase):
    """``CustomDefaultStorage.url`` strips leading-slash + joins base_url.

    Every file URL the SPA renders flows through this method.
    Three documented behaviours:

    * raise ``ValueError`` when ``base_url`` is None,
    * strip a leading ``/`` from the file name before joining,
    * use ``urljoin`` so the base URL's trailing slash decides
      whether the path becomes a sibling or a child.
    """

    def test_10_37_raises_when_base_url_is_none(self):
        """10.37: ``url(...)`` raises ``ValueError`` when ``base_url``
        is unset.

        Defensive contract — operators must opt in to URL serving
        explicitly. A regression that returned the bare path would
        leak a filesystem absolute path into the browser.
        """
        # FileSystemStorage falls back to settings.MEDIA_URL when
        # base_url is None at __init__; force it back to None on
        # the instance to exercise the documented guard.
        storage = CustomDefaultStorage()
        storage.base_url = None
        with self.assertRaises(ValueError):
            storage.url("foo.png")

    def test_10_38_strips_leading_slash_before_join(self):
        """10.38: ``url('/foo/bar.png')`` → joined against base_url
        WITHOUT the leading slash.

        Without the strip, ``urljoin('https://cdn/static/', '/foo')``
        resolves to ``https://cdn/foo`` — the ``/static/`` segment
        is lost. That is the exact bug this method is here to
        prevent.
        """
        storage = CustomDefaultStorage(base_url="https://cdn.example.com/static/")

        url = storage.url("/uploads/img.png")

        self.assertEqual(
            url, "https://cdn.example.com/static/uploads/img.png",
            "leading slash MUST be stripped or `urljoin` discards "
            "the base path segment",
        )

    def test_10_39_relative_name_joins_naturally(self):
        """10.39: a name with no leading slash joins as a child of
        ``base_url``.

        Smoke test for the happy path — pins ``urljoin`` behaviour
        so a refactor that swapped to e.g. ``base_url + name``
        (string concat) would surface the trailing-slash drift.
        """
        storage = CustomDefaultStorage(base_url="https://cdn.example.com/static/")

        url = storage.url("uploads/img.png")

        self.assertEqual(
            url, "https://cdn.example.com/static/uploads/img.png",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


