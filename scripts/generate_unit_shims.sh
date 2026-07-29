#!/usr/bin/env bash
# generate_unit_shims.sh — replace old test files with re-export shims
# Usage: cd /home/syscall/Documents/lex && bash scripts/generate_unit_shims.sh
set -euo pipefail

cd /home/syscall/Documents/lex

write_shim() {
  local old_path="$1"
  local new_module="$2"

  if [[ ! -f "$old_path" ]]; then
    echo "WARN  missing: $old_path"
    return
  fi

  if head -1 "$old_path" | grep -q "Re-export shim"; then
    echo "SKIP  already a shim: $old_path"
    return
  fi

  printf '"""Re-export shim — canonical source moved to %s."""\nfrom %s import *  # noqa: F401,F403\n' "$new_module" "$new_module" > "$old_path"
  echo "WROTE $old_path -> $new_module"
}

# ── audit_logging/tests → unit/audit ──
write_shim lex/audit_logging/tests/test_audit_log_mixin.py lex.tests.unit.audit.test_audit_log_mixin
write_shim lex/audit_logging/tests/test_audit_log_mixin_update.py lex.tests.unit.audit.test_audit_log_mixin_update
write_shim lex/audit_logging/tests/test_bulk_audit_log_mixin.py lex.tests.unit.audit.test_bulk_audit_log_mixin
write_shim lex/audit_logging/tests/test_cache_manager.py lex.tests.unit.audit.test_cache_manager
write_shim lex/audit_logging/tests/test_calculation_audit.py lex.tests.unit.audit.test_calculation_audit
write_shim lex/audit_logging/tests/test_content_types.py lex.tests.unit.audit.test_content_types
write_shim lex/audit_logging/tests/test_context_resolution.py lex.tests.unit.audit.test_context_resolution
write_shim lex/audit_logging/tests/test_context_resolver_errors.py lex.tests.unit.audit.test_context_resolver_errors
write_shim lex/audit_logging/tests/test_initial_data_audit_logger.py lex.tests.unit.audit.test_initial_data_audit_logger
write_shim lex/audit_logging/tests/test_lex_logger.py lex.tests.unit.audit.test_lex_logger
write_shim lex/audit_logging/tests/test_model_logging_context.py lex.tests.unit.audit.test_model_logging_context
write_shim lex/audit_logging/tests/test_websocket_notifier.py lex.tests.unit.audit.test_websocket_notifier

# ── core/tests → unit/calculation ──
write_shim lex/core/tests/test_active_calculation_state_store.py lex.tests.unit.calculation.test_active_calculation_state_store
write_shim lex/core/tests/test_calculate_hook.py lex.tests.unit.calculation.test_calculate_hook
write_shim lex/core/tests/test_calculated_model_mixin.py lex.tests.unit.calculation.test_calculated_model_mixin
write_shim lex/core/tests/test_calculation_history_transitions.py lex.tests.unit.calculation.test_calculation_history_transitions
write_shim lex/core/tests/test_calculation_model_state_machine.py lex.tests.unit.calculation.test_calculation_model_state_machine
write_shim lex/core/tests/test_calculation_signals.py lex.tests.unit.calculation.test_calculation_signals
write_shim lex/core/tests/test_calculation_wait_contexts.py lex.tests.unit.calculation.test_calculation_wait_contexts
write_shim lex/core/tests/test_celery_task_dispatcher.py lex.tests.unit.calculation.test_celery_task_dispatcher
write_shim lex/core/tests/test_dispatch_calculation_task.py lex.tests.unit.calculation.test_dispatch_calculation_task

# ── core/tests → unit/audit ──
write_shim lex/core/tests/test_audit_actor_tracking.py lex.tests.unit.audit.test_audit_actor_tracking

# ── core/tests → unit/grid ──
write_shim lex/core/tests/test_ag_grid_server_side.py lex.tests.unit.grid.test_ag_grid_server_side
write_shim lex/core/tests/test_model_export_ag_grid.py lex.tests.unit.grid.test_model_export_ag_grid
write_shim lex/core/tests/test_user_read_filter_backend.py lex.tests.unit.grid.test_user_read_filter_backend

# ── core/tests → unit/auth ──
write_shim lex/core/tests/test_permission_enforcement.py lex.tests.unit.auth.test_permission_enforcement
write_shim lex/core/tests/test_permission_result.py lex.tests.unit.auth.test_permission_result
write_shim lex/core/tests/test_user_context.py lex.tests.unit.auth.test_user_context

# ── core/tests → unit/temporal ──
write_shim lex/core/tests/test_bitemporal_suppression.py lex.tests.unit.temporal.test_bitemporal_suppression
write_shim lex/core/tests/test_history_deletion.py lex.tests.unit.temporal.test_history_deletion
write_shim lex/core/tests/test_temporal_progression.py lex.tests.unit.temporal.test_temporal_progression

# ── core/tests → unit/core ──
write_shim lex/core/tests/test_combination_and_cluster.py lex.tests.unit.core.test_combination_and_cluster
write_shim lex/core/tests/test_combination_engine.py lex.tests.unit.core.test_combination_engine
write_shim lex/core/tests/test_create_flow_and_duplicates.py lex.tests.unit.core.test_create_flow_and_duplicates
write_shim lex/core/tests/test_exceptions.py lex.tests.unit.core.test_exceptions
write_shim lex/core/tests/test_future_activation_scheduler_routing.py lex.tests.unit.core.test_future_activation_scheduler_routing
write_shim lex/core/tests/test_lex_model_core.py lex.tests.unit.core.test_lex_model_core
write_shim lex/core/tests/test_lexmodel_atomic_save.py lex.tests.unit.core.test_lexmodel_atomic_save
write_shim lex/core/tests/test_lifecycle_hooks.py lex.tests.unit.core.test_lifecycle_hooks
write_shim lex/core/tests/test_local_scheduler.py lex.tests.unit.core.test_local_scheduler
write_shim lex/core/tests/test_model_validation.py lex.tests.unit.core.test_model_validation
write_shim lex/core/tests/test_programmatic_creation.py lex.tests.unit.core.test_programmatic_creation
write_shim lex/core/tests/test_reconcile_command.py lex.tests.unit.core.test_reconcile_command

# ── process_admin/tests → unit/grid ──
write_shim lex/process_admin/tests/test_ag_grid_list_utilities.py lex.tests.unit.grid.test_ag_grid_list_utilities
write_shim lex/process_admin/tests/test_model_export_utilities.py lex.tests.unit.grid.test_model_export_utilities
write_shim lex/process_admin/tests/test_pk_list_filter_backend.py lex.tests.unit.grid.test_pk_list_filter_backend
write_shim lex/process_admin/tests/test_user_read_restriction_filter.py lex.tests.unit.grid.test_user_read_restriction_filter

# ── process_admin/tests → unit/auth ──
write_shim lex/process_admin/tests/test_api_key_user_context.py lex.tests.unit.auth.test_api_key_user_context
write_shim lex/process_admin/tests/test_keycloak_permissions_middleware.py lex.tests.unit.auth.test_keycloak_permissions_middleware
write_shim lex/process_admin/tests/test_streamlit_token_views.py lex.tests.unit.auth.test_streamlit_token_views

# ── process_admin/tests → unit/temporal ──
write_shim lex/process_admin/tests/test_bitemporal_synchronizer.py lex.tests.unit.temporal.test_bitemporal_synchronizer
write_shim lex/process_admin/tests/test_temporal_parse_as_of.py lex.tests.unit.temporal.test_temporal_parse_as_of
write_shim lex/process_admin/tests/test_temporal_reconciler.py lex.tests.unit.temporal.test_temporal_reconciler

# ── process_admin/tests → unit/api ──
write_shim lex/process_admin/tests/test_base_serializer_helpers.py lex.tests.unit.api.test_base_serializer_helpers
write_shim lex/process_admin/tests/test_constants.py lex.tests.unit.api.test_constants
write_shim lex/process_admin/tests/test_destroy_one_with_payload.py lex.tests.unit.api.test_destroy_one_with_payload
write_shim lex/process_admin/tests/test_history_endpoint.py lex.tests.unit.api.test_history_endpoint
write_shim lex/process_admin/tests/test_many_model_entries.py lex.tests.unit.api.test_many_model_entries
write_shim lex/process_admin/tests/test_model_collection_structure.py lex.tests.unit.api.test_model_collection_structure
write_shim lex/process_admin/tests/test_model_container.py lex.tests.unit.api.test_model_container
write_shim lex/process_admin/tests/test_model_entry_provider_mixin.py lex.tests.unit.api.test_model_entry_provider_mixin
write_shim lex/process_admin/tests/test_model_permissions_view.py lex.tests.unit.api.test_model_permissions_view
write_shim lex/process_admin/tests/test_model_registration.py lex.tests.unit.api.test_model_registration
write_shim lex/process_admin/tests/test_model_structure_builder_merge.py lex.tests.unit.api.test_model_structure_builder_merge
write_shim lex/process_admin/tests/test_model_structure_permissions.py lex.tests.unit.api.test_model_structure_permissions
write_shim lex/process_admin/tests/test_model_structure_types.py lex.tests.unit.api.test_model_structure_types
write_shim lex/process_admin/tests/test_model_structure_yaml.py lex.tests.unit.api.test_model_structure_yaml
write_shim lex/process_admin/tests/test_model_utils.py lex.tests.unit.api.test_model_utils
write_shim lex/process_admin/tests/test_one_model_entry.py lex.tests.unit.api.test_one_model_entry
write_shim lex/process_admin/tests/test_serializer_map_behavior.py lex.tests.unit.api.test_serializer_map_behavior
write_shim lex/process_admin/tests/test_xlsx_field.py lex.tests.unit.api.test_xlsx_field

# ── lex_app/tests → unit/infra ──
write_shim lex/lex_app/tests/test_celery_callbacks.py lex.tests.unit.infra.test_celery_callbacks
write_shim lex/lex_app/tests/test_celery_context.py lex.tests.unit.infra.test_celery_context
write_shim lex/lex_app/tests/test_fast_health.py lex.tests.unit.infra.test_fast_health
write_shim lex/lex_app/tests/test_init_retry.py lex.tests.unit.infra.test_init_retry
write_shim lex/lex_app/tests/test_keycloak_manager_timeout.py lex.tests.unit.infra.test_keycloak_manager_timeout
write_shim lex/lex_app/tests/test_runtime_config.py lex.tests.unit.infra.test_runtime_config
write_shim lex/lex_app/tests/test_user_model_registration.py lex.tests.unit.infra.test_user_model_registration

echo ""
echo "Done generating shims."
