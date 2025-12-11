from copy import deepcopy
from typing import List, Optional, Any, Dict
import logging

from lex.core.exceptions import CeleryDispatchError
from lex.core.mixins.calculated import calc_and_save_sync
from lex.api.utils import operation_context, OperationContext
from lex.audit_logging.utils.model_context import model_logging_context

logger = logging.getLogger(__name__)


class CeleryTaskDispatcher:
    """
    Handles Celery task dispatch and failure management for calculated models.

    This class provides centralized management of Celery task creation, dispatch,
    and failure handling for groups of calculated models. It includes context
    resolution for calculation_id tracking and comprehensive error handling
    with synchronous fallback capabilities.
    """

    @staticmethod
    def dispatch_calculation_groups(
            groups: List[List['CalculatedModelMixin']],
            *args,
            context = None
    ) -> None:
        """
        Dispatch model groups to Celery workers with failure handling.

        This method handles the complete Celery dispatch workflow:
        1. Resolves calculation_id from context if not provided
        2. Dispatches each group as a separate Celery task
        3. Monitors task completion and handles failures
        4. Falls back to synchronous processing for failed tasks

        Args:
            groups: List of model groups to process, where each group is a list of models
            *args: Arguments to pass to calculation methods
            context: Optional context for tracking

        Raises:
            CeleryDispatchError: If Celery dispatch fails completely or all processing attempts fail
        """
        if not groups:
            logger.info("No groups to process, skipping Celery dispatch")
            return

        # Validate groups structure
        if not isinstance(groups, (list, tuple)):
            raise CeleryDispatchError(
                f"Groups must be a list or tuple, got {type(groups).__name__}",
                groups_type=type(groups).__name__
            )

        # Filter out empty groups and log statistics
        non_empty_groups = [group for group in groups if group]
        empty_groups_count = len(groups) - len(non_empty_groups)

        if empty_groups_count > 0:
            logger.warning(f"Filtered out {empty_groups_count} empty groups from {len(groups)} total groups")

        if not non_empty_groups:
            logger.warning("All groups are empty, nothing to process")
            return

        total_models = sum(len(group) for group in non_empty_groups)
        logger.info(
            f"Starting Celery dispatch for {len(non_empty_groups)} groups containing {total_models} total models"
        )

        try:
            # Import the Celery task here to avoid circular imports
            try:
                from lex.lex_app.celery_tasks import calc_and_save
            except ImportError as import_error:
                raise CeleryDispatchError(
                    f"Failed to import Celery task 'calc_and_save': {str(import_error)}",
                    total_groups=len(non_empty_groups),
                    total_models=total_models
                ) from import_error

            # Dispatch each group as a separate Celery task
            task_results = []
            group_mapping = {}  # Map task results to their corresponding groups
            failed_dispatch_count = 0

            from lex.lex_app.celery_tasks import RunInCelery
            with RunInCelery():
                for i, group in enumerate(non_empty_groups):
                    try:

                        task_result = CeleryTaskDispatcher._dispatch_single_group(
                            group, i, *args, context=context
                        )

                        if task_result:
                            task_results.append(task_result)
                            group_mapping[task_result.id] = group
                        else:
                            failed_dispatch_count += 1
                            logger.warning(f"Failed to dispatch group {i + 1}, processed synchronously as fallback")

                    except Exception as dispatch_error:
                        failed_dispatch_count += 1
                        logger.error(f"Error dispatching group {i + 1}: {str(dispatch_error)}")
                        # The _dispatch_single_group method handles synchronous fallback

            # Log dispatch statistics
            successful_dispatches = len(task_results)
            logger.info(
                f"Dispatch summary: {successful_dispatches}/{len(non_empty_groups)} groups dispatched to Celery, "
                f"{failed_dispatch_count} groups processed synchronously"
            )

            # Handle task results and process any failures
            if task_results:
                logger.debug(f"Monitoring {len(task_results)} Celery tasks for completion")
                CeleryTaskDispatcher._handle_task_results(
                    task_results, group_mapping, *args
                )
            else:
                if failed_dispatch_count == 0:
                    logger.warning("No tasks were successfully dispatched and no synchronous fallback occurred")
                else:
                    logger.info(
                        f"All {failed_dispatch_count} groups were processed synchronously due to dispatch failures")

        except CeleryDispatchError:
            # Re-raise CeleryDispatchError as-is
            raise
        except Exception as celery_setup_error:
            logger.error(f"Celery setup failed: {celery_setup_error}")
            logger.warning(f"Falling back to synchronous processing for all {len(non_empty_groups)} groups")

            try:
                # Flatten all groups and process synchronously
                all_models = []
                for group in non_empty_groups:
                    all_models.extend(group)

                logger.info(f"Processing {len(all_models)} models synchronously as complete fallback")
                calc_and_save_sync(all_models, *args)
                logger.info("Synchronous fallback processing completed successfully")

            except Exception as sync_fallback_error:
                raise CeleryDispatchError(
                    f"Both Celery dispatch and synchronous fallback failed. "
                    f"Celery error: {str(celery_setup_error)}. Sync error: {str(sync_fallback_error)}",
                    total_groups=len(non_empty_groups),
                    total_models=total_models,
                    celery_error=str(celery_setup_error),
                    sync_error=str(sync_fallback_error)
                ) from sync_fallback_error


    @staticmethod
    def _dispatch_single_group(
            group: List['CalculatedModelMixin'],
            group_index: int,
            *args,
            context=None
    ):
        """
        Dispatch a single group to Celery with error handling.

        Attempts to create and dispatch a Celery task for the given group.
        If dispatch fails, the group is processed synchronously as a fallback.

        Args:
            group: List of models to process as a single task
            group_index: Index of the group for logging purposes
            *args: Arguments to pass to the calculation method
            context: Optional context for tracking

        Returns:
            Celery task result object if successful, None if fallback was used

        Raises:
            CeleryDispatchError: If both Celery dispatch and synchronous fallback fail
        """
        if not group:
            logger.warning(f"Group {group_index + 1} is empty, skipping dispatch")
            return None

        if not isinstance(group, (list, tuple)):
            raise CeleryDispatchError(
                f"Group must be a list or tuple, got {type(group).__name__}",
                group_index=group_index,
                group_type=type(group).__name__
            )

        group_size = len(group)
        logger.debug(f"Attempting to dispatch group {group_index + 1} with {group_size} models")

        try:
            from lex.lex_app.celery_tasks import calc_and_save
        except ImportError as import_error:
            raise CeleryDispatchError(
                f"Failed to import calc_and_save for group dispatch: {str(import_error)}",
                group_index=group_index,
                group_size=group_size
            ) from import_error


        try:
            request_obj = context['request_obj'] or {}
            request_obj_extracted = OperationContext.extract_info_request(request_obj)
            new_context = {**context, "request_obj": request_obj_extracted}
            model_context = model_logging_context.get()['model_context']
            task_result = calc_and_save.delay(group, group_index, *args, context=new_context, model_context=model_context)
            from lex.lex_app.celery_tasks import register_task_with_context
            register_task_with_context(task_result)

        except CeleryDispatchError as dispatch_error:
            logger.error(f"Celery dispatch failed for group {group_index + 1}: {str(dispatch_error)}")

            # Attempt synchronous processing as fallback
            try:
                logger.warning(
                    f"Falling back to synchronous processing for group {group_index + 1} "
                    f"with {group_size} models due to Celery dispatch failure"
                )

                calc_and_save_sync(group, *args)

                logger.info(
                    f"Successfully processed group {group_index + 1} synchronously as fallback "
                    f"({group_size} models)"
                )
                return None  # Indicate synchronous processing was used

            except Exception as sync_error:
                raise CeleryDispatchError(
                    f"Both Celery dispatch and synchronous fallback failed for group {group_index + 1}. "
                    f"Celery error: {str(dispatch_error)}. Sync error: {str(sync_error)}",
                    group_index=group_index,
                    group_size=group_size,
                    celery_error=str(dispatch_error),
                    sync_error=str(sync_error)
                ) from sync_error

        except Exception as unexpected_error:
            raise CeleryDispatchError(
                f"Unexpected error during group dispatch: {str(unexpected_error)}",
                group_index=group_index,
                group_size=group_size,
            ) from unexpected_error

        return task_result




    @staticmethod
    def _handle_task_results(
            task_results: List[Any],
            group_mapping: Dict[str, List['CalculatedModelMixin']],
            *args
    ) -> None:
        """
        Handle Celery task results and process failures synchronously.

        Monitors all dispatched tasks for completion, identifies failures,
        and processes failed groups synchronously. Provides comprehensive
        logging of task success and failure rates.

        Args:
            task_results: List of Celery task result objects
            group_mapping: Mapping from task IDs to their corresponding model groups
            *args: Arguments to pass to synchronous processing for failed tasks

        Raises:
            CeleryDispatchError: If task result handling fails completely
        """
        if not task_results:
            logger.warning("No task results to handle")
            return

        if not isinstance(task_results, (list, tuple)):
            raise CeleryDispatchError(
                f"Task results must be a list or tuple, got {type(task_results).__name__}",
                task_results_type=type(task_results).__name__
            )

        if not isinstance(group_mapping, dict):
            raise CeleryDispatchError(
                f"Group mapping must be a dict, got {type(group_mapping).__name__}",
                group_mapping_type=type(group_mapping).__name__
            )

        total_tasks = len(task_results)
        total_models = sum(len(group) for group in group_mapping.values())

        logger.info(f"Handling results for {total_tasks} Celery tasks covering {total_models} models")

        try:
            # Import here to avoid circular imports
            try:
                from celery.result import ResultSet
            except ImportError as import_error:
                raise CeleryDispatchError(
                    f"Failed to import Celery ResultSet: {str(import_error)}",
                    total_tasks=total_tasks,
                    total_models=total_models
                ) from import_error

            failed_groups = []
            successful_tasks = 0
            status_check_errors = 0

            try:
                # Create ResultSet from the task results and wait for completion
                rs = ResultSet(task_results)

                logger.debug(f"Waiting for {total_tasks} Celery tasks to complete...")

                # Wait for all tasks to complete, but handle individual failures

                rs.join(propagate=False)
                # Check each task result for failures
                for task_index, task_result in enumerate(task_results):
                    task_id = getattr(task_result, 'id', f'unknown_{task_index}')
                    try:
                        if task_result.failed():
                            error_info = getattr(task_result, 'result', 'Unknown error')
                            logger.error(f"Task {task_id} failed with error: {error_info}")

                            # Add the corresponding group to failed_groups for retry
                            if task_id in group_mapping:
                                failed_group = group_mapping[task_id]
                                failed_groups.append(failed_group)
                                logger.warning(
                                    f"Added failed task {task_id} group ({len(failed_group)} models) "
                                    f"to synchronous retry queue"
                                )
                            else:
                                logger.error(f"Failed task {task_id} not found in group mapping")
                        else:
                            successful_tasks += 1
                            logger.debug(f"Task {task_id} completed successfully")

                    except Exception as check_error:
                        status_check_errors += 1
                        logger.error(f"Error checking status of task {task_id}: {str(check_error)}")

                        # Assume failure and add to retry list if mapping exists
                        if task_id in group_mapping:
                            failed_group = group_mapping[task_id]
                            failed_groups.append(failed_group)
                            logger.warning(
                                f"Added task {task_id} with status check error to retry queue "
                                f"({len(failed_group)} models)"
                            )


                # Process any failed groups synchronously
                if failed_groups:
                    failed_models_count = sum(len(group) for group in failed_groups)
                    logger.warning(
                        f"Processing {len(failed_groups)} failed task groups "
                        f"({failed_models_count} models) synchronously"
                    )

                    sync_success_count = 0
                    sync_failure_count = 0

                    for group_index, group in enumerate(failed_groups):
                        try:
                            logger.debug(
                                f"Processing failed group {group_index + 1}/{len(failed_groups)} synchronously")
                            calc_and_save_sync(group, *args)
                            sync_success_count += 1
                            logger.info(
                                f"Successfully processed failed group {group_index + 1} "
                                f"of {len(group)} models synchronously"
                            )
                        except Exception as sync_error:
                            sync_failure_count += 1
                            logger.error(
                                f"Synchronous fallback failed for group {group_index + 1} "
                                f"({len(group)} models): {str(sync_error)}"
                            )
                            # Continue processing other groups, but raise error at the end

                    if sync_failure_count > 0:
                        raise CeleryDispatchError(
                            f"Synchronous fallback failed for {sync_failure_count}/{len(failed_groups)} groups",
                            total_tasks=total_tasks,
                            failed_groups=len(failed_groups),
                            sync_failures=sync_failure_count,
                            sync_successes=sync_success_count
                        )

                    logger.info(
                        f"All {len(failed_groups)} failed groups processed successfully via synchronous fallback")

                # Log final statistics
                failed_tasks = len(failed_groups)
                logger.info(
                    f"Task processing completed: {successful_tasks}/{total_tasks} tasks successful, "
                    f"{failed_tasks} failed and recovered via synchronous processing"
                )

                if status_check_errors > 0:
                    logger.warning(f"{status_check_errors} tasks had status check errors but were handled")

            except CeleryDispatchError:
                # Re-raise CeleryDispatchError as-is
                raise
            except Exception as celery_error:
                logger.error(f"Celery ResultSet processing failed: {str(celery_error)}")

                # Fall back to synchronous processing for all groups
                logger.warning(f"Falling back to synchronous processing for all {len(group_mapping)} groups")

                try:
                    all_models = []
                    for group in group_mapping.values():
                        all_models.extend(group)

                    logger.info(f"Processing {len(all_models)} models synchronously as complete fallback")
                    calc_and_save_sync(all_models, *args)
                    logger.info("Complete synchronous fallback processing completed successfully")

                except Exception as complete_fallback_error:
                    raise CeleryDispatchError(
                        f"Both Celery result handling and complete synchronous fallback failed. "
                        f"Celery error: {str(celery_error)}. Fallback error: {str(complete_fallback_error)}",
                        total_tasks=total_tasks,
                        total_models=total_models,
                        celery_error=str(celery_error),
                        fallback_error=str(complete_fallback_error)
                    ) from complete_fallback_error

        except CeleryDispatchError:
            # Re-raise CeleryDispatchError as-is
            raise
        except Exception as result_handling_error:
            raise CeleryDispatchError(
                f"Unexpected error during task result handling: {str(result_handling_error)}",
                total_tasks=total_tasks,
                total_models=total_models
            ) from result_handling_error

    @staticmethod
    def _get_calculation_context() -> Optional[str]:
        """
        Extract calculation_id from context for Celery dispatch.

        Attempts to retrieve the calculation_id from the current context
        variable. This allows tasks to maintain context across the
        synchronous-to-asynchronous boundary.

        Returns:
            The calculation_id string if available, None otherwise
        """
        calculation_id = None
        try:
            context = operation_context.get()
            if context and "calculation_id" in context:
                calculation_id = context["calculation_id"]
                logger.debug(f"Retrieved calculation_id from context: {calculation_id}")
        except Exception as e:
            logger.warning(f"Could not get calculation_id from context: {e}")

        return calculation_id
