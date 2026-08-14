"""
Per-test descriptions for the showcase report.

The catalogue is intentionally split out of ``showcase_clusters.py`` so
that file stays a clean dataclass + structural definition while the
written prose lives here. Each map is keyed by the unittest method
name only (the ``test_X_Y_…`` part) — the same key the runner extracts
from Django's verbose output, and the same key the report builder
looks up in ``cluster.test_descriptions``.

Style guide
-----------
* One sentence each. Two if a contrast genuinely matters.
* Lead with the customer-visible behaviour, not the implementation.
  ✓  "Returns 400 with the field name when a required field is missing."
  ✗  "Calls _validate_required on the serializer's Meta.required."
* Avoid framework jargon (``LIFO stack``, ``ContextResolver``, ``pgcode``)
  — those belong in engineering docs, not the platform health email.
* Skip implementation details that are noise to a stakeholder; keep
  the answer to "what does this prove the platform can still do?".

Coverage status (5 May 2026)
----------------------------
* celery_async, validation_hooks — covered in ``showcase_clusters.py``
  (kept inline; populated first as the gate-critical clusters).
* permissions, signals_ws, exports, serializers, queries, crud_api —
  covered here.
* init — the CLI-contract scenarios (sub-cluster 1m) are covered here;
  the rest of the cluster still falls back.
* Other clusters (calculations, history, audit_logging, api_layer,
  stress) fall back to a generic "engineering: add a description in
  this file" line in the report.
"""

from __future__ import annotations


# ── crud_api ─────────────────────────────────────────────────────────
# This cluster is about the REST API contract for records: POST /create,
# GET /list, GET /detail, PATCH/PUT, DELETE, plus bulk delete and the
# pagination/ordering/filtering surface used directly via the API.
#
# Distinguish from ``queries`` (below) — that cluster is about the AG
# Grid filter/sort/group/pivot translation, i.e. the query DSL used by
# the frontend grid, not the basic REST CRUD shape.
CRUD_API_TESTS: dict[str, str] = {
    "test_2_1_post_creates_record":
        "POSTing valid JSON to a model endpoint creates a record and returns it. "
        "If broken, no customer can create new records via the API.",
    "test_2_3_post_missing_required_field_returns_400":
        "Missing required fields produce a 400 with the field name. If broken, "
        "customers see opaque server errors instead of actionable validation feedback.",
    "test_2_4_post_invalid_field_type_returns_400":
        "Wrong-type field values (e.g. text in a number column) produce a 400, not a 500. "
        "If broken, the API leaks internal errors instead of rejecting cleanly.",
    "test_2_5_post_extra_unknown_fields_are_ignored":
        "Extra unknown fields in POST payloads are silently ignored, so older "
        "clients can keep sending fields the new schema dropped.",
    "test_2_6_unauthenticated_post_is_rejected":
        "Anonymous callers cannot create records. If broken, anyone on the network "
        "could write into the customer's database.",
    "test_2_7_api_key_post_stamps_technical_user":
        "Records created via API key are stamped with the technical-user identity. "
        "If broken, the audit trail loses the link between automated jobs and their actor.",
    "test_2_8_get_detail_returns_record":
        "GET /<id> returns a single record with all the fields the caller is permitted to see. "
        "Foundation of every detail view in the UI.",
    "test_2_9_get_detail_nonexistent_returns_404":
        "Unknown ids return 404, not 500. If broken, the UI's 'not found' messaging breaks.",
    "test_2_10_get_list_returns_all_records":
        "GET on a collection returns every readable record. If broken, list views are empty or partial.",
    "test_2_11_list_respects_pagination":
        "Pagination parameters limit the page size and provide stable navigation. "
        "If broken, large datasets lock up the browser.",
    "test_2_11b_per_page_controls_page_size":
        "The per_page parameter controls how many records come back. If broken, "
        "the grid's 'rows per page' control silently does nothing.",
    "test_2_11c_per_page_negative_one_returns_all":
        "per_page=-1 returns the full unpaginated set — used by exports and small lookup grids.",
    "test_2_12_unauthenticated_get_is_rejected":
        "Reads require authentication. If broken, every record in the system is publicly readable.",
    "test_2_13_patch_updates_only_specified_fields":
        "PATCH updates only the fields named in the payload and leaves others intact. "
        "If broken, partial edits silently overwrite or null out data.",
    "test_2_15_patch_invalid_value_leaves_record_unchanged":
        "A rejected PATCH does not partially apply — the record remains unchanged. "
        "Critical for keeping data consistent when validation fails.",
    "test_2_16_put_replaces_the_record":
        "PUT replaces the full record with the payload, in contrast to PATCH's merge semantics.",
    "test_2_17_patch_nonexistent_returns_404":
        "Editing an unknown id returns 404 instead of creating a new record.",
    "test_2_18_unauthenticated_patch_is_rejected":
        "Anonymous callers cannot edit records. If broken, anyone could alter the dataset.",
    "test_2_19_delete_removes_record":
        "DELETE removes the record. If broken, customers cannot retire stale data through the UI.",
    "test_2_20_delete_nonexistent_returns_404":
        "Deleting an unknown id is a 404, not a silent success — prevents masking client bugs.",
    "test_2_21_unauthenticated_delete_is_rejected":
        "Anonymous callers cannot delete records. If broken, the dataset is destructible by anyone.",
    "test_2_22_delete_then_get_returns_404":
        "After deletion, the record is gone for subsequent reads — no stale cache/tombstone leak.",
    "test_2_23_bulk_delete_removes_selected_records":
        "Bulk delete removes every selected id in a single request. Powers the grid's "
        "'select rows -> delete' toolbar action.",
    "test_2_24_bulk_delete_leaves_unselected_records":
        "Bulk delete is precisely scoped — unselected records are untouched. "
        "If broken, the customer can lose far more data than they intended.",
    "test_2_25_bulk_delete_unknown_ids_are_noop":
        "Unknown ids in a bulk-delete batch are skipped, not errored — the request still "
        "deletes the records that do exist.",
    "test_2_26_ordering_ascending_by_value":
        "?ordering=field returns rows sorted ascending by that field.",
    "test_2_27_ordering_descending_by_value":
        "?ordering=-field returns rows sorted descending by that field.",
    "test_2_28_ordering_multi_field":
        "Multiple fields in ?ordering apply in declaration order, so secondary sort works.",
    "test_2_29_ordering_unknown_field_is_ignored":
        "An unknown ordering field is silently dropped instead of crashing the request.",
    "test_2_30_filter_exact_match":
        "?field=value filters the list to records with that exact value.",
    "test_2_31_filter_lookup_gt":
        "?field__gt=value filters to records strictly greater than the threshold.",
    "test_2_32_filter_lookup_icontains":
        "?field__icontains=value matches a case-insensitive substring — what the search bar uses.",
    "test_2_33_filter_combined":
        "Multiple filter parameters AND together so callers can narrow down on several conditions.",
    "test_2_34_filter_empty_result":
        "A filter that matches no rows returns an empty list with HTTP 200, not a 404.",
    "test_2_35_filter_negation":
        "Negation operators (e.g. __ne) exclude rows matching the condition.",
    "test_2_36_filter_in_lookup":
        "__in=a,b,c matches any of the listed values — used by multi-select filters.",
    "test_2_37_unknown_filter_field_is_ignored":
        "An unknown filter field is silently dropped instead of crashing the request.",
}


# ── queries ──────────────────────────────────────────────────────────
# This cluster is about the AG Grid query layer: how the frontend grid's
# filter/sort/group/pivot UI translates into ORM queries. It sits ON TOP
# of the REST CRUD (``crud_api``) but adds the legacy + AG Grid-specific
# filter shapes, group/pivot hierarchies, and the LexModel-managed
# pagination edge cases the grid uses.
QUERIES_TESTS: dict[str, str] = {
    "test_14_1_icontains_substring_match":
        "Grid 'contains' text filter translates into a case-insensitive substring match. "
        "Foundation of the grid search bar.",
    "test_14_2_decimal_range_filter":
        "Grid number-range filter (Decimal between A and B) returns the records inside the range.",
    "test_14_3_in_lookup_comma_split":
        "A multi-value filter passed as a comma list correctly splits into an IN lookup.",
    "test_14_4_negated_filter":
        "Grid 'not equal' filters translate into a NOT lookup — used by the column 'exclude' UI.",
    "test_14_5_ordering_descending":
        "Grid sort descending on a column produces a descending ORDER BY in the query.",
    "test_14_6_per_page_minus_one_returns_all":
        "Grid's 'show all' option (per_page=-1) bypasses pagination so exports and small "
        "lookups see every row.",
    "test_14_7_pk_only_returns_id_shortcut":
        "The pk-only shortcut returns just primary keys — used by the grid's bulk-action "
        "selection without re-fetching every row.",
    "test_14_8_flat_leaf_pagination_slice":
        "Flat (non-grouped) leaf rows paginate correctly — start/end row indices line up "
        "with what the grid asked for.",
    "test_14_9_filter_model_text_contains":
        "AG Grid 'filterModel' text-contains payload (the new filter format) maps to "
        "icontains on the right column.",
    "test_14_10_filter_number_inrange_plus_sort_desc":
        "AG number 'inRange' filter combined with a descending sort works as a single query.",
    "test_14_11_filter_date_greater_than_datefield":
        "AG date 'greaterThan' filter on a DateField correctly handles date-only "
        "(not datetime) comparison.",
    "test_14_12_filter_date_equals_datetime_with_time":
        "AG date 'equals' filter on a DateTime column matches the day, not just an "
        "exact-second timestamp.",
    "test_14_13_filter_set_in_membership":
        "AG 'set' filter (multi-checkbox dropdown) maps to an IN lookup with the chosen values.",
    "test_14_14_filter_compound_operator_or":
        "Two filter conditions joined by OR in the AG filter builder produce a single OR query.",
    "test_14_15_row_group_cols_level_0_returns_group_rows":
        "Row-grouping at level 0 returns one row per group, with counts. Powers the "
        "grid's collapsible group view.",
    "test_14_16_group_level_value_cols_sum_amount":
        "Aggregation columns (sum on Amount) compute correctly inside a grouped view.",
    "test_14_17_drill_into_group_returns_leaf_rows":
        "Expanding a group returns the leaf rows under it, scoped to the group key.",
    "test_14_18_pivot_mode_status_x_amount_sum":
        "Pivot mode (status × Amount sum) produces a correct cross-tab — used for "
        "ad-hoc analytics inside the grid.",
    "test_14_19_invalid_filter_field_is_ignored":
        "An unknown filter column in the AG payload is silently ignored instead of "
        "crashing the grid request.",
    "test_14_20_invalid_sort_field_falls_back_to_pk":
        "An unknown sort column falls back to the primary key — keeps deterministic "
        "ordering even if the frontend asks for a missing column.",
    "test_14_21_text_filter_operation_type_variants":
        "Every text filter operation the AG UI exposes (equals, contains, startsWith, "
        "endsWith, blank, notBlank) translates into the correct lookup.",
    "test_14_22_number_filter_operation_type_variants":
        "Every number filter operation (=, !=, <, <=, >, >=, inRange, blank) "
        "translates into the correct lookup.",
    "test_14_23_legacy_condition_model_and_or":
        "The legacy v1 filter format (the older 'condition model') still works on AND/OR — "
        "ensures saved filter views from before the AG migration keep functioning.",
    "test_14_24_ordering_multi_field_csv_silently_drops_unknown":
        "Multi-field ordering as a CSV string drops any unknown column instead of "
        "rejecting the whole sort.",
    "test_14_25_blank_and_not_blank_ops_do_not_work_bug016":
        "Documents the known BUG-016: AG 'blank' / 'notBlank' operators are NOT honoured "
        "today. Keeps the test as a tombstone so the regression is visible until fixed.",
}


# ── permissions ──────────────────────────────────────────────────────
PERMISSIONS_TESTS: dict[str, str] = {
    "test_4_1_superuser_reads_all_fields":
        "Superusers see every field on every record — the no-restriction baseline.",
    "test_4_1b_superuser_api_response_contains_every_field":
        "The actual JSON returned to a superuser contains every field, not just internally permits read.",
    "test_4_2_hr_user_sees_allowed_fields_only":
        "Role-restricted users (HR-style) only see the fields their role permits — others are masked.",
    "test_4_2b_regular_user_sees_only_public_fields":
        "Regular users without elevated scopes only see public fields. Foundation of "
        "field-level data hiding.",
    "test_4_3_allow_all_except_hides_excluded_fields":
        "An 'allow all except X' permission profile correctly hides the excluded fields.",
    "test_4_4_permission_edit_restricts_editable_fields":
        "Edit permissions correctly limit which fields a user can change in PATCH/PUT.",
    "test_4_5_permission_delete_denies_non_admin":
        "Non-admin users cannot delete records — DELETE returns 403.",
    "test_4_5b_admin_may_delete":
        "Admin users CAN delete records — confirms the deny is scoped, not blanket.",
    "test_4_6_permission_create_denies_non_admin":
        "Non-admin users cannot create records — POST returns 403.",
    "test_4_6b_admin_may_create":
        "Admin users CAN create records — confirms the deny is scoped.",
    "test_4_7_keycloak_read_scope_allows_read":
        "A Keycloak-issued read scope allows GET requests. Confirms SSO scope translation works.",
    "test_4_8_keycloak_no_scopes_denies":
        "An authenticated user with NO scopes is denied — fail-closed semantics.",
    "test_4_8b_keycloak_no_scopes_denies_edit_and_export":
        "No scopes → edit and export both denied, not just read.",
    "test_4_8c_edit_scope_allows_edit_not_read":
        "An edit-only scope grants edit but not read — confirms scopes don't leak into "
        "each other.",
    "test_4_9_legacy_can_read_matches_permission_read":
        "Legacy can_read() helper behaves identically to the new permission_read scope — "
        "guards against a regression during the v1→v2 migration.",
    "test_4_10_from_request_base_populates_user_and_email":
        "The request-derived UserContext correctly populates the user identity and email.",
    "test_4_10b_anonymous_request_yields_unauthenticated_context":
        "An anonymous request yields a clearly-marked unauthenticated context (no impersonation).",
    "test_4_11_api_key_context_includes_api_key_role":
        "API-key requests get a UserContext stamped with the API key role for downstream checks.",
    "test_4_12_with_instance_resolves_instance_scopes":
        "Instance-bound permission resolution attaches per-row scopes to the user.",
    "test_4_13_per_row_visibility_filters_denied_rows":
        "Row-level permissions hide rows the user is not entitled to read — not just fields.",
    "test_4_14_auditlog_resource_filter_db_path":
        "Audit-log filtering by resource correctly hits the optimised DB-side filter path.",
    "test_4_15_auditlog_deferred_permission_residual_path":
        "When DB-side permission filtering can't fully evaluate a constraint, the residual "
        "Python check still drops the right rows.",
    "test_4_16_pk_only_fast_path_respects_permissions":
        "Even the pk-only fast path respects row-level permissions — no bulk-action loophole.",
    "test_4_17_allow_all_profile_returns_every_row":
        "An 'allow all' profile returns every row, confirming the deny path is the limiter.",
    "test_4_18_deny_all_short_circuits_to_empty":
        "A 'deny all' profile short-circuits to an empty list with no DB hit.",
    "test_4_19_camel_to_snake_contract":
        "Permission keys received as camelCase from the frontend convert to snake_case "
        "consistently — guards a v1→v2 contract trap.",
    "test_4_20_non_editable_fields_includes_pk_and_editable_false":
        "The non-editable list always includes the primary key and any field marked editable=False.",
    "test_4_21_decorator_injects_mixin_and_preserves_identity":
        "The permission decorator injects the mixin without breaking the model's identity "
        "(class name, mro, isinstance).",
    "test_4_22_metaclass_injects_for_lexmodel_only":
        "Permission metaclass only injects into LexModel subclasses — third-party Django "
        "models stay untouched.",
    "test_4_23_change_detection_skips_permission_check_on_unchanged_values":
        "Saving a record without changing a permitted field doesn't run a permission check "
        "on it — perf optimisation that must not weaken security.",
    "test_4_24_changed_denied_field_raises_permission_denied":
        "Trying to write a denied field raises PermissionDenied even if the rest of the payload is allowed.",
    "test_4_25_reserved_field_names_bypass_permission_check":
        "Framework-reserved fields (created_at, etc.) bypass the user-level permission check "
        "since the framework manages them.",
    "test_4_26_create_path_permission_create_denies_non_admin":
        "On the create path, the create-specific permission is enforced — separate from "
        "edit permission so they can be granted independently.",
    "test_4_27_translates_scopes_into_ra_rbac_entries":
        "Keycloak scopes translate into the React-Admin RBAC entries the frontend uses to "
        "show/hide UI elements.",
    "test_4_28_missing_rsname_entries_are_skipped":
        "Keycloak permissions without an rsname are skipped instead of crashing the request.",
    "test_4_29_resource_set_id_becomes_record_field":
        "When Keycloak emits a resource_set with an id, that id becomes the record's "
        "id field for row-level resolution.",
    "test_4_30_falls_back_to_profile_uma_permissions":
        "When a request has no token-side permissions, the profile's UMA permissions are "
        "used as a fallback.",
    "test_4_31_defaults_installed_when_no_access_token":
        "With no access token at all, the default (locked-down) permissions install — fail-closed.",
    "test_4_32_populates_identity_from_keycloak_when_token_present":
        "When the token IS present, identity is populated from it (sub, email, name).",
    "test_4_32b_uma_fetch_failure_keeps_defaults":
        "A failed UMA fetch leaves the defaults in place — does not partially populate "
        "and create a half-trusted context.",
    "test_4_33_extract_client_roles_shapes":
        "Client-role extraction handles every shape Keycloak emits (resource_access, "
        "realm_access, etc.).",
    "test_4_34_cleanup_invalid_tokens_scrubs_session":
        "Invalid tokens are scrubbed from the session so a follow-up request doesn't "
        "carry stale credentials.",
    "test_4_35_global_read_scope":
        "A global read scope grants read on every model, not just the explicitly-named ones.",
    "test_4_36_row_scoped_read_permissions":
        "Row-scoped permissions only return matching rows — confirms the scope→queryset translation.",
    "test_4_36b_scope_cache_memoizes_per_request":
        "Within a single request, repeated permission lookups hit the cache instead of "
        "re-querying Keycloak — perf safety net.",
    "test_4_37_can_read_with_default_scope_branches":
        "can_read() handles every default scope branch (public, restricted, denied) correctly.",
    "test_4_38_build_shadow_instance_coerces_scalars":
        "Shadow-instance construction (used for permission previews on unsaved data) "
        "coerces scalar fields to their typed values.",
    "test_4_38b_build_shadow_instance_empty_payload_returns_none":
        "An empty payload to the shadow-instance builder returns None instead of an "
        "object full of defaults.",
    "test_4_38c_parse_value_fk_dict_extracts_id":
        "FK dict payloads (e.g. {id: 5, name: 'foo'}) extract just the id when parsing values.",
    "test_4_39_can_read_from_payload_unresolvable_model_allows":
        "When the target model can't be resolved (legacy or removed), can_read defaults to "
        "allow rather than block — prevents a deploy from silently denying old payloads.",
    "test_4_39b_can_read_from_payload_honours_scope_cache":
        "Payload-derived read checks reuse the scope cache so they don't re-query Keycloak.",
    "test_4_39c_can_read_from_payload_scoped_denies_non_matching":
        "Payload-derived read checks deny rows that don't match the user's scope — "
        "no scope-bypass via payload trick.",
    "test_4_40_export_full_deny_blanks_every_domain_field":
        "A fully-denied export still produces a file, but every domain field is blanked — "
        "no leak of unauthorised data via the export path.",
    "test_4_41_read_deny_detail_response_leaks_no_domain_fields":
        "A denied detail response contains no domain fields — only framework metadata.",
}


# ── signals_ws (live UI updates) ─────────────────────────────────────
SIGNALS_WS_TESTS: dict[str, str] = {
    "test_9_1_mark_in_progress_registers_state_store":
        "Marking a calculation as in-progress registers it in the state store the UI watches. "
        "Foundation of the live progress spinner.",
    "test_9_2_completion_cleans_up_state_store":
        "On completion, the state store entry is removed so a finished calc isn't shown "
        "as still running.",
    "test_9_3_websocket_notification_sent_on_state_change":
        "Every state change pushes a WebSocket notification to open browser tabs — what "
        "drives 'live status' in the UI.",
    "test_9_4_root_process_cleans_up_cache":
        "The root process cleans up the in-memory cache on shutdown so a restarted worker "
        "starts from a clean slate.",
    "test_9_5_child_process_skips_cache_cleanup":
        "Child processes do NOT touch the cache on exit — only the root does, so a "
        "child crash doesn't wipe state for sibling workers.",
    "test_9_6_update_status_includes_error_details_on_failure":
        "Failure status updates carry the error details so the UI can display 'why it failed', "
        "not just 'it failed'.",
    "test_9_7_guard_lifecycle_before_inside_after":
        "Suspension guards correctly fire before/inside/after their scope — required for the "
        "live-update suppression API customers use during bulk imports.",
    "test_9_8_nested_suspension_stacks_and_unwinds_correctly":
        "Nested suspension scopes stack and unwind without leaking state between them.",
    "test_9_9_three_guards_are_independent":
        "Three concurrent guards do not interfere with each other — independence is the "
        "contract for 'I can suspend signals on my own work without touching yours'.",
    "test_9_10_suspension_does_not_leak_across_threads":
        "Suspension scopes do NOT leak across threads — a customer's parallel job in another "
        "thread keeps emitting live updates while the bulk import is suspended in the first.",
}


# ── exports ──────────────────────────────────────────────────────────
EXPORTS_TESTS: dict[str, str] = {
    "test_13_1_empty_queryset_returns_404":
        "An empty export request returns 404 instead of an empty file — clearer signal to "
        "the caller that there was nothing to export.",
    "test_13_2a_flat_export_without_fk_has_all_rows":
        "A flat export (no FK joins) returns every row in the queryset.",
    "test_13_2b_flat_export_has_rows_and_fk_display_names":
        "A flat export with FK columns shows the human-readable display name, not just the FK id.",
    "test_13_3_filtered_export_selects_specific_ids":
        "An export filtered to specific record ids returns exactly those rows — used by the "
        "'select rows -> export' grid action.",
    "test_13_4_permission_export_masks_restricted_fields":
        "Fields the caller can't read are masked (blanked) in the export, not omitted — "
        "preserves column shape so a downstream pivot doesn't break.",
    "test_13_5_ag_flat_respects_column_layout_and_headers":
        "AG-grid-driven exports respect the column ordering and header labels the user "
        "configured in the grid.",
    "test_13_6_ag_skips_unresolvable_columns":
        "Columns the export can't resolve (e.g. dropped fields) are skipped instead of "
        "crashing the whole export.",
    "test_13_7_ag_over_limit_end_row_clamps":
        "Asking for a larger end_row than there are rows clamps to the last row — no off-by-one.",
    "test_13_8_ag_grouped_export_writes_hierarchy_labels":
        "Grouped exports write the group hierarchy labels in their own columns so the file "
        "still makes sense without the grid context.",
    "test_13_9_group_key_paths_filter_integer_fk_and_null":
        "Group key paths filter correctly on integer FKs and on NULLs — historically a "
        "common 'grouped export shows wrong rows' regression site.",
    "test_13_10_ag_with_filtered_export_ids_selects_subset":
        "An AG-grid export with both a filter AND specific ids correctly intersects them.",
    "test_13_11_unauthenticated_post_is_rejected":
        "Anonymous callers cannot trigger an export — exports honour the same auth as reads.",
    "test_13_12_non_uniform_permission_export_runs_slow_mask":
        "When permissions vary row by row the export uses the slow per-row masking path "
        "(correctness over speed) rather than the fast bulk path.",
    "test_13_13_all_db_columns_values_fast_path":
        "When every column maps to a real DB column AND permissions are uniform, the fast "
        "values()-based path runs — keeps export latency reasonable on big sets.",
    "test_13_14_computed_column_hydrates_instances":
        "Computed columns (Python @property) force instance hydration so the values are "
        "actually computed, not skipped.",
    "test_13_15_short_description_resolver_falls_back_to_str":
        "A column without an explicit short_description falls back to str(value) instead "
        "of crashing or writing a Python repr.",
    "test_13_16_callable_attribute_is_invoked":
        "A callable column attribute is invoked (so the value, not <bound method ...>, lands "
        "in the file).",
    "test_13_18_empty_queryset_streaming_returns_404":
        "Streaming-export path also returns 404 on empty queryset — same contract as "
        "non-streaming.",
    "test_13_19_classify_returns_none_for_only_ag_internal_columns":
        "When the request asks for only AG-internal columns (no domain fields), classification "
        "returns None so the export bails cleanly instead of producing an empty file.",
    "test_13_20_flat_fast_export_runs_when_universal_bails":
        "If the universal export path bails out (e.g. mixed permissions), the flat fast path "
        "still runs so customers don't get a blanket failure.",
    "test_13_21_normalize_cell_value_covers_every_branch":
        "Every value-type branch (None, datetime, decimal, bool, str, callable, FK ref) "
        "normalises into the correct cell value in the output file.",
}


# ── serializers ──────────────────────────────────────────────────────
SERIALIZERS_TESTS: dict[str, str] = {
    "test_12_1_detail_contains_framework_managed_keys":
        "Detail responses always include framework-managed keys (id, created_at, etc.) so "
        "the frontend can render audit chrome without a special case.",
    "test_12_2_short_description_uses_model_str":
        "The short_description field on serialised records uses the model's __str__ — what "
        "shows up in dropdowns and breadcrumbs.",
    "test_12_3_lex_reserved_scopes_shape":
        "The reserved 'lex_*' scopes on the serialised payload have the expected shape — "
        "the contract the frontend depends on for permissions.",
    "test_12_4_permission_read_restricts_visible_fields":
        "Fields the caller can't read are absent from the response — not nulled, not masked: gone.",
    "test_12_5_permission_read_deny_all_omits_from_list":
        "A fully-denied record is omitted from list responses entirely, not present-but-empty.",
    "test_12_6_history_row_respects_main_model_permission_read":
        "History rows inherit the main model's permission_read — denied users can't read history "
        "via the history endpoint.",
    "test_12_7_meta_historical_scopes_are_immutable":
        "The historical scopes injected by Meta are frozen — a runtime mutation can't widen "
        "what a user can see in history.",
    "test_12_8_lex_reserved_scopes_edit_reflects_permission_edit":
        "The lex_reserved edit scope on serialised payloads reflects permission_edit so the "
        "frontend can disable inputs the user can't write.",
    "test_12_9_decimal_field_preserves_precision":
        "Decimal fields round-trip without precision loss — critical for money columns.",
    "test_12_10_datetime_roundtrip_keeps_timezone":
        "Datetime fields round-trip with their timezone preserved — no silent UTC stripping.",
    "test_12_11_date_field_uses_iso_format":
        "Date fields serialise as ISO 8601 (YYYY-MM-DD), the format every frontend already parses.",
    "test_12_12_uuid_field_is_string":
        "UUID fields serialise as strings, not Python UUID objects — required for JSON.",
    "test_12_13_nullable_foreign_key_unset_is_null":
        "An unset nullable FK serialises as null, not missing — keeps the schema stable.",
    "test_12_14_foreign_key_set_roundtrips":
        "A set FK round-trips through PATCH/PUT as the same FK — no accidental detach.",
    "test_12_15_patch_accepts_foreign_key_dict_payload":
        "PATCH accepts an FK as either a bare id or a {id, ...} dict — both come back the same way.",
    "test_12_16_patch_rejects_invalid_choice":
        "An invalid choice for a choices=… field is rejected with a clear error.",
    "test_12_17_text_field_preserves_unicode_and_newlines":
        "Text fields preserve unicode characters and newlines exactly as written.",
    "test_12_18_json_field_preserves_structure":
        "JSON fields preserve nested object/array structure on round trip.",
    "test_12_19_unknown_field_in_patch_ignored":
        "An unknown field in a PATCH payload is ignored, not an error — keeps old clients alive.",
    "test_12_20_filtered_list_drops_denied_rows":
        "List endpoints drop rows the caller is denied on — confirmed at the serialiser level, "
        "not just the queryset.",
    "test_12_21_list_row_shape_matches_detail":
        "A list row has the same field shape as a detail response — no surprise missing keys "
        "in list views.",
    "test_12_22_many_get_selected_rows_match_list_shape":
        "/many?ids=… returns rows in the same shape as the list endpoint — bulk fetch is "
        "schema-stable.",
    "test_12_23_fk_reference_caller_cannot_read_is_stripped":
        "An FK reference the caller can't read is stripped from the response — no leak via FK "
        "pointer.",
    "test_12_24_unreadable_fields_pruned_from_updates":
        "Fields the caller can't read are pruned from update payloads on the way IN — prevents "
        "a 'read denied but write allowed' loophole.",
    "test_12_25_target_denied_collapses_payload":
        "A fully-denied target record collapses the payload to nothing — no partial leak.",
    "test_12_26_model2serializer_always_injects_internal_fields":
        "Auto-generated serialisers always inject the internal framework fields, even for "
        "models that don't declare them.",
    "test_12_27_wrap_custom_serializer_preserves_user_fields":
        "Wrapping a custom serialiser preserves the user-defined fields — customisation isn't "
        "silently dropped.",
    "test_12_28_get_serializer_map_returns_same_class_per_model":
        "Repeated lookups for the same model return the same serialiser class — no duplicate "
        "class generation that would slow the API down.",
    "test_12_29_post_creates_m2m_with_pk_list":
        "A POST with an M2M field as a pk list correctly creates the relation.",
    "test_12_30_patch_replaces_m2m_set":
        "A PATCH on an M2M field with a new pk list replaces the set, not appends.",
    "test_12_31_fk_attach_detach_rewire":
        "Attaching, detaching and re-wiring an FK through PATCH all behave as documented.",
    "test_12_32_source_default_override_exposes_framework_alias":
        "A field that overrides source=… still exposes the framework's alias for downstream "
        "consumers.",
    "test_12_33_history_table_inherits_framework_alias_from_source":
        "History tables inherit the framework alias from their source model — keeps the "
        "history schema consistent with the live one.",
    "test_12_34_meta_history_table_walks_instance_type_chain_for_alias":
        "Meta history-table resolution walks the instance type chain to find the alias — works "
        "with model inheritance.",
    "test_12_35_wrap_custom_serializer_preserves_hide_actions_column":
        "Wrapping a custom serialiser preserves the 'hide actions column' flag — no UI "
        "regression from the wrapping process.",
}


# ── init ─────────────────────────────────────────────────────────────
# Sub-cluster 1m is the operator-facing CLI contract: the run
# configurations `lex setup` writes into PyCharm must each invoke a
# subcommand the `lex` CLI actually resolves, and `lex --help` must
# advertise those subcommands under the spelling an operator types.
INIT_TESTS: dict[str, str] = {
    "test_1_102_scaffold_produces_complete_run_file_set":
        "Setting up a project writes the full set of one-click run configurations — "
        "every engineer's tray looks the same on every machine.",
    "test_1_103_every_run_xml_uses_lex_script":
        "Every generated run configuration invokes the platform CLI rather than a stray "
        "script, so the environment is prepared the same way each time.",
    "test_1_104_every_run_subcommand_resolves":
        "Every one-click run configuration points at a command that really exists. If broken, "
        "an operator clicks a button in their IDE and gets 'no such command'.",
    "test_1_105_explicit_commands_are_all_registered":
        "The commands the platform's tooling and documentation depend on are all still "
        "registered. If broken, a rename has silently retired a command people rely on.",
    "test_1_106_skip_bootstrap_set_matches_explicit_handlers":
        "The list of commands allowed to start without a full application boot only names "
        "real commands, so no command quietly takes the slow path.",
    "test_1_107_lex_root_help_lists_explicit_commands":
        "Running the CLI's help lists every command an operator is meant to type. If broken, "
        "a working command is undiscoverable to anyone who didn't already know its name.",
    "test_1_107b_deprecated_aliases_stay_out_of_root_help":
        "Older spellings kept alive for existing documentation still run, but stay out of the "
        "help listing so each command is advertised exactly once.",
    "test_1_108_each_explicit_subcommand_help_exits_zero":
        "Every command answers '--help' cleanly. If broken, a command is mis-declared and will "
        "fail the moment someone runs it.",
    "test_1_109_every_delegated_command_is_a_real_django_command":
        "Run configurations that hand off to the underlying application framework name commands "
        "it actually knows, so operators get real work instead of a confusing error.",
}
