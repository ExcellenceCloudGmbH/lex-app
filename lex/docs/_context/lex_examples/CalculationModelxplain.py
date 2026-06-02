import logging
import os
import traceback
from copy import deepcopy

from django.db import models
from django.db import transaction
from django_lifecycle import (
    hook,
    AFTER_UPDATE,
    AFTER_CREATE,
    BEFORE_SAVE,
)
from django_lifecycle.conditions import WhenFieldValueIs
from lex.api.utils import operation_context, OperationContext
from lex.audit_logging.utils.CacheManager import CacheManager
from lex.audit_logging.utils.ContextResolver import ContextResolver
from lex.core.models.LexModel import LexModel
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)

class CalculationModelException(APIException):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.calc_obj = kwargs.get("calc_obj", None)
            self.exception_details = kwargs.get("exception_details", None)
            self.stack_trace = kwargs.get("stack_trace", None)


class CalculationModel(LexModel):

    IN_PROGRESS = "IN_PROGRESS"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"
    NOT_CALCULATED = "NOT_CALCULATED"
    ABORTED = "ABORTED"
    CANCELLED = "CANCELLED"
    STATUSES = [
        (IN_PROGRESS, "IN_PROGRESS"),
        (ERROR, "ERROR"),
        (SUCCESS, "SUCCESS"),
        (NOT_CALCULATED, "NOT_CALCULATED"),
        (ABORTED, "ABORTED"),
        (CANCELLED, "CANCELLED"),
    ]

    is_calculated = models.CharField(
        max_length=50, choices=STATUSES, default=NOT_CALCULATED, editable=False
    )

    class Meta:
        abstract = True


    def update(self):
        """
        Placeholder for update logic. Subclasses should override this method
        if they provide 'update' functionality.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must override the 'update' or 'calculate' method."
        )

    def calculate(self):
        """
        Placeholder for calculation logic. Subclasses should override this method
        if they provide 'calculate' functionality.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must override the 'update' or 'calculate' method."
        )




    @hook(BEFORE_SAVE)
    def before_save(self):
        pass

        # Check if it's a new instance
        if self._state.adding:
            self.is_creation = True
        else:
            self.is_creation = False

    def lex_func(self):
        """
        Dynamically selects the overridden calculation method ('calculate' or 'update').
        It compares the function object of the instance's method with the function
        object on the base class to detect an override.
        """
        # CORRECT: Compare the bound method's function with the base class's function
        if self.calculate.__func__ is not CalculationModel.calculate:
            return self.calculate
        # CORRECT: Do the same for the 'update' method
        elif self.update.__func__ is not CalculationModel.update:
            return self.update

        # Fallback will raise NotImplementedError when called
        return self.calculate




    def should_use_celery(self) -> bool:
        """
        Determine if calculation should use Celery based on configuration and availability.

        Returns:
            bool: True if Celery should be used, False for synchronous execution
        """

        # Check if Celery is enabled in setting
        if not os.getenv("CELERY_ACTIVE", None) == 'true' or not hasattr(self.lex_func(), 'delay'):
            return False

        # Check if Celery is available by trying to import and test connection
        try:
            from celery import current_app
            # Test if we can access Celery (this will fail if broker is down)
            current_app.control.inspect()
            return True
        except Exception:
            # Celery not available, fall back to synchronous execution
            return False

    def dispatch_calculation_task(self):
        """
        Dispatch calculation to Celery worker using the calc_and_save task.

        Returns:
            AsyncResult: Celery task result object
        """

        # Extract only the calculation_id from context to avoid pickling issues
        context = operation_context.get()
        request_obj = context['request_obj'] or {}
        request_obj_extracted = OperationContext.extract_info_request(request_obj)
        new_context = {**context, "request_obj": request_obj_extracted}

        # For backward compatibility
        func = self.lex_func()

        # Dispatch single model calculation to Celery with calculation_id
        from lex.audit_logging.utils.ModelContext import model_logging_context
        model_context = deepcopy(model_logging_context.get()['model_context'])

        # Dispatch the task
        task_result = func.delay(context=new_context, model_context=model_context)

        # Register with RunInCelery context if one exists
        from lex.lex_app.celery_tasks import register_task_with_context
        return register_task_with_context(task_result)

    def execute_calculation_sync(self):
        """
        Execute calculation synchronously in the current thread.
        """
        from lex.core.signals.CalculationSignals import update_calculation_status

        func = self.lex_func()
        exception_details = None
        stack_trace = None
        try:
            if hasattr(self, "is_atomic") and not self.is_atomic:
                func()
                self.is_calculated = self.SUCCESS
            else:
                with transaction.atomic():
                    func()
                    self.is_calculated = self.SUCCESS

        except Exception as e:
            # Store error details
            error_details = f"{str(e)}\n\n{traceback.format_exc()}"
            exception_details = str(e)
            stack_trace = traceback.format_exc()
            self.is_calculated = self.ERROR

            if hasattr(self, 'calculation_error_message'):
                self.calculation_error_message = error_details
            elif hasattr(self, 'error_message'):
                self.error_message = error_details

            raise
        finally:
            # Clean up cache if context is available
            try:
                context = ContextResolver.resolve()

                # Only perform cleanup if this is the ROOT process
                # If we are a child process, we leave our logs in cache so the parent/frontend
                # can still access them until the entire operation completes.
                is_root = False
                if context.root_record and context.current_record:
                    if context.root_record == context.current_record:
                        is_root = True
                elif context.current_record and not context.parent_record:
                    # If no explicit root but also no parent, we are effectively root
                    is_root = True

                if is_root:
                    calc_id = context.calculation_id
                    # If we are root, we can clean up everything for this calculation ID
                    # or just our specific key. Cleaning everything ensures no orphaned child keys.
                    cleanup_result = CacheManager.cleanup_calculation(calculation_id=calc_id)

                    if cleanup_result.success:
                        logger.info(f"Root process cleanup successful for calculation {calc_id}")
                    else:
                        logger.warning(
                            f"Root process cleanup had errors for calculation {calc_id}: {cleanup_result.errors}")
                else:
                    logger.debug(f"Skipping cache cleanup for child process {context.current_record}")

            except Exception as cleanup_error:
                logger.error(f"Cache cleanup failed after calculation hook: {str(cleanup_error)}")

            self.save(skip_hooks=True)
            update_calculation_status(
                self,
                exception_details=exception_details,
                stack_trace=stack_trace,
            )

    @hook(AFTER_UPDATE, condition=WhenFieldValueIs("is_calculated", IN_PROGRESS))
    @hook(AFTER_CREATE, condition=WhenFieldValueIs("is_calculated", IN_PROGRESS))
    def calculate_hook(self):
        """                                                                                                                      
        Enhanced calculation hook with Celery integration.                                                                       

        Dispatches calculations to Celery workers when celery_active=True and Celery                                             
        is available, otherwise falls back to synchronous execution. Proper status                                               
        management ensures IN_PROGRESS -> SUCCESS/ERROR transitions.                                                             
        """
        from lex.core.signals.CalculationSignals import update_calculation_status
        import logging

        logger = logging.getLogger(__name__)

        try:
            if self.should_use_celery():
                # Dispatch to Celery worker                                                                                      
                logger.info(f"Dispatching calculation for {self} to Celery worker")

                task_result = None
                from lex.lex_app.celery_tasks import RunInCelery
                with RunInCelery():
                    task_result = self.dispatch_calculation_task()

                    # Note: Status will be updated by CallbackTask.on_success/on_failure                                             
                # Model remains in IN_PROGRESS state until task completes                                                        
                logger.info(f"Calculation task {task_result.id} dispatched for {self}")

            else:
                # Execute synchronously as fallback                                                                              
                logger.info(f"Executing calculation for {self} synchronously (Celery not available)")
                self.execute_calculation_sync()

        except Exception as e:
            # Handle any errors in task dispatch or synchronous execution                                                        
            logger.error(f"Calculation failed for {self}: {e}", exc_info=True)
            status_was_error = self.is_calculated == self.ERROR
            self.is_calculated = self.ERROR

            # Store error message if the model has an error_message field                                                        
            stack_trace = f"{traceback.format_exc()}"
            exception_details = str(e)
                # Clean up cache and save error state
            try:
                context = ContextResolver.resolve()

                # Only perform cleanup if this is the ROOT process                                                               
                is_root = False
                if context.root_record and context.current_record:
                    if context.root_record == context.current_record:
                        is_root = True
                elif context.current_record and not context.parent_record:
                    is_root = True

                if is_root:
                    calc_id = context.calculation_id
                    # Clean up all keys associated with this calculation ID                                                      
                    cleanup_result = CacheManager.cleanup_calculation(calculation_id=calc_id)

                    if cleanup_result.success:
                        logger.info(f"Root process cleanup successful after calculation hook for calculation {calc_id}")
                    else:
                        logger.warning(f"Root process cleanup had errors after calculation hook for calculation {calc_id}: {cleanup_result.errors} ")
                else:
                    logger.debug(f"Skipping cache cleanup for child process {context.current_record}")

            except Exception as cleanup_error:
                logger.error(f"Cache cleanup failed after calculation hook: {str(cleanup_error)}")

            # Dispatch failures do not pass through execute_calculation_sync(), so persist
            # ERROR state and notify websocket clients from here.
            if not status_was_error:
                try:
                    self.save(skip_hooks=True)
                    update_calculation_status(
                        self,
                        exception_details=exception_details,
                        stack_trace=stack_trace,
                    )
                except Exception as status_update_error:
                    logger.error(
                        f"Failed to persist/notify ERROR state for {self}: {status_update_error}",
                        exc_info=True,
                    )

            raise CalculationModelException(calc_obj=self, exception_details=exception_details, stack_trace=stack_trace)



# Example subclass implementation
class TargetTrackRecord(CalculationModel):
    
    id = models.AutoField(primary_key=True)
    quarter = models.ForeignKey(to=Quarter, on_delete=models.CASCADE)
    is_fund_raising = models.BooleanField(default=False)
    as_of_date = models.DateTimeField()
    report = XLSXField(upload_to='target_track_record', null=True, blank=True)
    

    internal_update = False

    
    def update(self):
        if not self.internal_update:
            self.internal_update = True

            print(f"Create Target Track Record")

            # if One.user_name is not None and pd.isna(self.created_by):
            #     self.created_by = One.user_name
            #     self.internal_update = True
            #     self.save()

            valuation = ValuationVehicle.objects \
                .exclude(vehicle__category='Feeder') \
                .exclude(vehicle__category='Carry') \
                .filter(quarter=self.quarter) \
                .exclude(valid_to__lt=self.as_of_date) \
                .filter(valid_from__lte=self.as_of_date) \
                .exclude(target__investment_name='AIP MEP')

            carry_provision_investor = CarryProvisionInvestor.objects \
                .exclude(vehicle__category='Feeder') \
                .exclude(vehicle__category='Carry') \
                .filter(quarter=self.quarter) \
                .exclude(valid_to__lt=self.as_of_date) \
                .filter(valid_from__lte=self.as_of_date)

            target_cashflows = TargetCashflow.objects \
                .filter(transaction_date__lte=self.quarter.report_date) \
                .exclude(valid_to__lt=self.as_of_date) \
                .filter(valid_from__lte=self.as_of_date) \
                .exclude(vehicle__category='Feeder') \
                .exclude(transaction_type_detail='Equalization Interest') \
                .exclude(target__investment_name='AIP MEP')

            targets = Target.objects.exclude(investment_name='AIP MEP').values('investment_name', 'entry_date', 'exit_date')

            vehicles = Vehicle.objects.all()
            investors = Investor.objects.all()

            # Calculate: Invest Cost + Deal Proceeds + Carry Payments
            total_track_record = TargetTrackRecord.calculate_clashflows(target_cashflows)

            # Calculate Carry Payments
            total_track_record = TargetTrackRecord.calculate_carry_payments(total_track_record, target_cashflows, vehicles,
                                                               investors)

            # Fund Fair Market Value and Carry Provision from Valuation
            total_track_record = TargetTrackRecord.calculate_valuation(self.quarter, self.is_fund_raising, total_track_record, valuation, carry_provision_investor)

            # Status
            total_track_record = TargetTrackRecord.add_status(total_track_record, targets)

            accumulated_track_record = TargetTrackRecord.create_accumulated_track_record(total_track_record)

            # Gross and Net Total Value + MoM
            total_track_record, accumulated_track_record = TargetTrackRecord.calculate_total_value(total_track_record,
                                                                                      accumulated_track_record)

            # Gross and Net IRR Total
            total_track_record = TargetTrackRecord.calc_irr_category_target(self.quarter, self.is_fund_raising, total_track_record, target_cashflows,
                                                               valuation, vehicles, investors)
            total_track_record = TargetTrackRecord.calc_irr_fund_target(self.quarter, self.is_fund_raising, total_track_record, target_cashflows, valuation)

            # Accumulated IRR
            accumulated_track_record = TargetTrackRecord.calculate_irr_accumulated(
                self.quarter, self.is_fund_raising, accumulated_track_record, target_cashflows, valuation, vehicles, investors)

            # Create Excel Sheet

            total_track_record, accumulated_track_record = TargetTrackRecord.format_dataframes(total_track_record, accumulated_track_record)

            current_time_stamp = "{:%Y_%m_%d_%H_%M_%S}".format(datetime.now())

            self.create_tabular_target_track_record(total_track_record)
            self.create_tabular_accumulated_target_track_record(accumulated_track_record)

            report_date_string = self.quarter.report_date.strftime("%Y_%m_%d")

            if self.is_fund_raising:
                path = f"{report_date_string}_TargetTrackRecord_FundRaising_{current_time_stamp}.xlsx"
            else:
                path = f"{report_date_string}_TargetTrackRecord_{current_time_stamp}.xlsx"

            XLSXField.create_excel_file_from_dfs(
                self.report,
                path=path,
                sheet_names=['TargetTrackRecord', 'AccumulatedTargetTrackRecord'],
                data_frames=[total_track_record, accumulated_track_record])

            self.locked = True

    @staticmethod
    def calculate_clashflows(target_cashflows):
        # Data Preparation
        target_cfs_df = pd.DataFrame.from_records(
            target_cashflows.values('vehicle__fund', 'vehicle__category', 'target__investment_name',
                                    'transaction_type', 'amount_eur'))

        track_record_by_category = target_cfs_df.groupby(
            ['vehicle__fund', 'vehicle__category', 'target__investment_name', 'transaction_type'], as_index=False).agg(
            amount=pd.NamedAgg(column='amount_eur', aggfunc=sum))
        track_record_by_fund = target_cfs_df.groupby(
            ['vehicle__fund', 'target__investment_name', 'transaction_type'], as_index=False).agg(
            amount=pd.NamedAgg(column='amount_eur', aggfunc=sum))

        investment_by_category = track_record_by_category[track_record_by_category['transaction_type'] == 'Invest Cost']
        deal_proceed_by_category = track_record_by_category[
            track_record_by_category['transaction_type'] == 'Deal Proceeds']

        investment_by_fund = track_record_by_fund[track_record_by_fund['transaction_type'] == 'Invest Cost']
        deal_proceed_by_fund = track_record_by_fund[track_record_by_fund['transaction_type'] == 'Deal Proceeds']

        ### merge by category

        track_record_by_category = pd.merge(investment_by_category, deal_proceed_by_category,
                                            left_on=['vehicle__fund', 'vehicle__category', 'target__investment_name'],
                                            right_on=['vehicle__fund', 'vehicle__category', 'target__investment_name'],
                                            how='outer').drop(
            ['transaction_type_y', 'transaction_type_x'], axis=1)

        ### merge by fund

        track_record_by_fund = pd.merge(investment_by_fund, deal_proceed_by_fund,
                                        left_on=['vehicle__fund', 'target__investment_name'],
                                        right_on=['vehicle__fund', 'target__investment_name'], how='outer').drop(
            ['transaction_type_y', 'transaction_type_x'], axis=1)

        # Join Fund with Category
        total_track_record = pd.merge(track_record_by_fund, track_record_by_category,
                                      left_on=['vehicle__fund', 'target__investment_name'],
                                      right_on=['vehicle__fund', 'target__investment_name'],
                                      how='outer')

        # Rename columns
        total_track_record = total_track_record.rename(
            columns={'vehicle__fund': 'fund', 'vehicle__category': 'category', 'target__investment_name': 'target',
                     'amount_x_x': 'fund_invest_cost', 'amount_y_x': 'fund_deal_proceeds',
                     'amount_x': 'fund_carry_payments',
                     'amount_x_y': 'category_invest_cost', 'amount_y_y': 'category_deal_proceeds',
                     'amount_y': 'category_carry_payments', })
        total_track_record['fund_invest_cost'] = -total_track_record['fund_invest_cost']
        total_track_record['category_invest_cost'] = -total_track_record['category_invest_cost']

        return total_track_record

    @staticmethod
    def calculate_carry_payments(total_track_records, target_cashflows, vehicles, investors):
        target_cfs_df = pd.DataFrame.from_records(
            target_cashflows.filter(transaction_type='Carry Payment')
            .exclude(transaction_type_detail=None)
            .values('vehicle__fund', 'vehicle__category', 'target__investment_name',
                    'transaction_type', 'transaction_type_detail', 'amount_eur'))

        if len(target_cfs_df) == 0:
            total_track_records['category_carry_payments'] = 0
            total_track_records['fund_carry_payments'] = 0
            return total_track_records
        else:
            carry_payments = []

            for row_index, row in target_cfs_df.iterrows():
                if row['vehicle__fund'] == 'Armira I':
                    vehicles_df = pd.DataFrame.from_records(
                        vehicles.filter(vehicle_name=row['transaction_type_detail'])
                        .values('vehicle_name', 'category'))
                    if len(vehicles_df) == 1:
                        cp = (
                            row['vehicle__fund'], vehicles_df['category'][0], row['target__investment_name'],
                            row['amount_eur'])
                        carry_payments.append(cp)
                        continue

                    investor_df = pd.DataFrame.from_records(
                        investors.filter(investor_name=row['transaction_type_detail'])
                        .values('investor_name'))

                    if len(investor_df) == 1:
                        cp = (row['vehicle__fund'], 'CoInv', row['target__investment_name'], row['amount_eur'])
                        carry_payments.append(cp)
                        continue

                elif row['vehicle'] == 'Armira II':
                    cp = (row['vehicle__fund'], row['vehicle__category'], row['target__investment_name'],
                          row['amount_eur'])
                    carry_payments.append(cp)

            carry_payments_category = pd.DataFrame(carry_payments,
                                                   columns=['fund', 'category', 'target', 'category_carry_payments'])

            carry_payments_category = carry_payments_category.groupby(
                ['fund', 'category', 'target'], as_index=False).sum(numeric_only=True)

            carry_payments_fund = carry_payments_category.groupby(
                ['fund', 'target'], as_index=False).sum(numeric_only=True)

            carry_payments_fund = carry_payments_fund.rename(columns={'category_carry_payments': 'fund_carry_payments'})

            total_track_records = pd.merge(total_track_records, carry_payments_category,
                                           left_on=['fund', 'category', 'target'],
                                           right_on=['fund', 'category', 'target'],
                                           how='outer')

            total_track_records = pd.merge(total_track_records, carry_payments_fund,
                                           left_on=['fund', 'target'],
                                           right_on=['fund', 'target'], how='outer')
        return total_track_records

    @staticmethod
    def calculate_valuation(quarter, is_fund_raising, total_track_record, valuation, carry_provision_investor):

        valuation_fund, valuation_category = TargetTrackRecord.get_fmv_carry_payment(quarter, is_fund_raising, valuation)

        agg_category = valuation_category.groupby(
            ['vehicle__fund', 'vehicle__category', 'target__investment_name', 'transaction_type'], as_index=False).agg(
            amount=pd.NamedAgg(column='amount', aggfunc=sum))

        agg_fund = valuation_fund.groupby(
            ['vehicle__fund', 'target__investment_name', 'transaction_type'], as_index=False).agg(
            amount=pd.NamedAgg(column='amount', aggfunc=sum))

        agg_fmv_category = agg_category[agg_category['transaction_type'] == 'Fair Market Value']
        agg_carry_provision_category = agg_category[agg_category['transaction_type'] == 'Carry Provision']
        agg_fmv_fund = agg_fund[agg_fund['transaction_type'] == 'Fair Market Value']
        agg_carry_provision_fund = agg_fund[agg_fund['transaction_type'] == 'Carry Provision']

        agg_category_total = pd.merge(agg_fmv_category, agg_carry_provision_category,
                                      left_on=['vehicle__fund', 'vehicle__category', 'target__investment_name'],
                                      right_on=['vehicle__fund', 'vehicle__category', 'target__investment_name'],
                                      how='outer')

        agg_fund_total = pd.merge(agg_fmv_fund, agg_carry_provision_fund,
                                  left_on=['vehicle__fund', 'target__investment_name'],
                                  right_on=['vehicle__fund', 'target__investment_name'],
                                  how='outer')
        if len(carry_provision_investor) > 0:

            carry_provision_investor_df = pd.DataFrame.from_records(
                carry_provision_investor.values('vehicle__vehicle_name', 'vehicle__category', 'vehicle__fund',
                                                'transaction_type', 'amount')
            )

            vehicle_list = carry_provision_investor_df['vehicle__vehicle_name'].unique()

            vehicle_to_target = VehicleToTarget.objects.filter(quarter=quarter).filter(
                vehicle__vehicle_name__in=vehicle_list).values('vehicle__vehicle_name', 'target__investment_name')
            vehicle_to_target_df = pd.DataFrame.from_records(vehicle_to_target)
            carry_provision_investor_df = pd.merge(carry_provision_investor_df, vehicle_to_target_df,
                                                   left_on=['vehicle__vehicle_name'],
                                                   right_on=['vehicle__vehicle_name'], how='left')

            category_carry_provision_investor = carry_provision_investor_df.groupby(
                ['vehicle__fund', 'target__investment_name', 'vehicle__category', 'transaction_type'],
                as_index=False).agg(
                amount=pd.NamedAgg(column='amount', aggfunc=sum))

            fund_carry_provision_investor = carry_provision_investor_df.groupby(
                ['vehicle__fund', 'target__investment_name', 'transaction_type'], as_index=False).agg(
                amount=pd.NamedAgg(column='amount', aggfunc=sum))

            category_carry_provision_investor = category_carry_provision_investor[
                category_carry_provision_investor['transaction_type'] == 'Carry Provision']
            fund_carry_provision_investor = fund_carry_provision_investor[
                fund_carry_provision_investor['transaction_type'] == 'Carry Provision']

            category_carry_provision_investor.rename(
                columns={'amount': 'category_carry_provision_investor_exld_calculations'}, inplace=True)
            fund_carry_provision_investor.rename(columns={'amount': 'fund_carry_provision_investor_exld_calculations'},
                                                 inplace=True)

            agg_category_total = pd.merge(agg_category_total, category_carry_provision_investor,
                                          left_on=['vehicle__fund', 'vehicle__category', 'target__investment_name'],
                                          right_on=['vehicle__fund', 'vehicle__category', 'target__investment_name'],
                                          how='outer')

            agg_fund_total = pd.merge(agg_fund_total, fund_carry_provision_investor,
                                      left_on=['vehicle__fund', 'target__investment_name'],
                                      right_on=['vehicle__fund', 'target__investment_name'],
                                      how='outer')
        else:
            agg_category_total['category_carry_provision_investor_exld_calculations'] = 0
            agg_fund_total['fund_carry_provision_investor_exld_calcuations'] = 0

        total_valuation_records = pd.merge(agg_fund_total, agg_category_total,
                                           left_on=['vehicle__fund', 'target__investment_name'],
                                           right_on=['vehicle__fund', 'target__investment_name'],
                                           how='outer')

        # Rename columns
        total_valuation_records = total_valuation_records.rename(
            columns={'vehicle__fund': 'fund', 'target__investment_name': 'target',
                     'vehicle__category': 'category', 'amount_x_x': 'fund_fair_market_value',
                     'amount_y_x': 'fund_carry_provision', 'amount_x_y': 'category_fair_market_value',
                     'amount_y_y': 'category_carry_provision'})

        total_track_record = pd.merge(total_track_record,
                                      total_valuation_records[
                                          ['fund', 'target', 'category', 'fund_fair_market_value',
                                           'fund_carry_provision', 'fund_carry_provision_investor_exld_calculations',
                                           'category_fair_market_value',
                                           'category_carry_provision',
                                           'category_carry_provision_investor_exld_calculations']],
                                      left_on=['fund', 'target', 'category'],
                                      right_on=['fund', 'target', 'category'],
                                      how='outer')

        # Fill empty cells
        total_track_record = total_track_record.fillna(0)
        return total_track_record

    @staticmethod
    def add_status(total_track_record, targets):
        entry_exit_dates = pd.DataFrame.from_records(
            targets.values('investment_name', 'entry_date', 'exit_date')).drop_duplicates()

        total_track_record = pd.merge(total_track_record,
                                      entry_exit_dates[['investment_name', 'entry_date', 'exit_date']],
                                      left_on=['target'],
                                      right_on=['investment_name'],
                                      how='left')

        total_track_record['status'] = np.where(pd.isna(total_track_record['exit_date']), "Active", "Realized")
        return total_track_record

    ### Gross and Net Total Value
    @staticmethod
    def create_accumulated_track_record(total_track_record):
        accumulated_by_category = total_track_record.groupby(
            ['fund', 'category'], as_index=False).sum(numeric_only=True)

        accumulated_by_fund = total_track_record.groupby(
            ['fund'], as_index=False).sum(numeric_only=True)
        accumulated_by_fund['category'] = 'Total'

        accumulated_armira1 = total_track_record[total_track_record['fund'] == 'Armira I']
        accumulated_armira1_dealbydeal = accumulated_armira1[accumulated_armira1['category'] == 'DealbyDeal']
        accumulated_armira1_holding = accumulated_armira1[accumulated_armira1['category'] == 'Holding']

        accumulated_armira1_exld_coinv = pd.concat([accumulated_armira1_dealbydeal, accumulated_armira1_holding])

        accumulated_armira1_exld_coinv = accumulated_armira1_exld_coinv.groupby(
            ['fund'], as_index=False).sum(numeric_only=True)
        accumulated_armira1_exld_coinv['category'] = 'Armira I excluding CoInv'

        accumulated_armira2_core = total_track_record[
            (total_track_record['fund'] == 'Armira II') & (total_track_record['category'] == 'Core')]

        #Realized
        accumulated_armira_total_realized = total_track_record[total_track_record['status'] == 'Realized']
        accumulated_armira_1_realized = accumulated_armira_total_realized[(total_track_record['fund'] == 'Armira I') & (total_track_record['category'] != 'CoInv')]
        accumulated_armira_2_realized = accumulated_armira_total_realized[(total_track_record['fund'] == 'Armira II') & (total_track_record['category'] != 'CoInv')]

        accumulated_armira_realized = pd.concat([accumulated_armira_1_realized, accumulated_armira_2_realized])

        accumulated_armira_realized['fund'] = 'Armira Total'
        accumulated_armira_realized['category'] = 'Realized excluding CoInv'
        accumulated_armira_realized = accumulated_armira_realized.groupby(
            ['fund', 'category'], as_index=False).sum(numeric_only=True)

        #Unrealized
        accumulated_armira_total_unrealized = total_track_record[total_track_record['status'] == 'Active']
        accumulated_armira_1_unrealized = accumulated_armira_total_unrealized[(total_track_record['fund'] == 'Armira I') & (total_track_record['category'] != 'CoInv')]
        accumulated_armira_2_unrealized = accumulated_armira_total_unrealized[(total_track_record['fund'] == 'Armira II') & (total_track_record['category'] != 'CoInv')]

        accumulated_armira_unrealized = pd.concat([accumulated_armira_1_unrealized, accumulated_armira_2_unrealized])

        accumulated_armira_unrealized['fund'] = 'Armira Total'
        accumulated_armira_unrealized['category'] = 'Unrealized excluding CoInv'
        accumulated_armira_unrealized = accumulated_armira_unrealized.groupby(
            ['fund', 'category'], as_index=False).sum(numeric_only=True)

        accumulated_armira_total = pd.concat(
            [accumulated_armira1_dealbydeal, accumulated_armira1_holding, accumulated_armira2_core])
        accumulated_armira_total['fund'] = 'Armira Total'
        accumulated_armira_total = accumulated_armira_total.groupby(
            ['fund'], as_index=False).sum(numeric_only=True)
        accumulated_armira_total['category'] = 'AII Core + AI excluding CoInv'

        accumulated_track_record = pd.concat(
            [accumulated_by_fund, accumulated_by_category, accumulated_armira1_exld_coinv, accumulated_armira_realized,
             accumulated_armira_unrealized, accumulated_armira_total])
        accumulated_track_record = accumulated_track_record[[
            'fund',
            'category',
            'category_invest_cost',
            'category_carry_provision',
            'category_carry_provision_investor_exld_calculations',
            'category_deal_proceeds',
            'category_carry_payments',
            'category_fair_market_value']]

        accumulated_track_record = accumulated_track_record.rename(columns={
            'category_invest_cost': 'invest_cost',
            'category_carry_provision': 'carry_provision',
            'category_carry_provision_investor_exld_calculations': 'carry_provision_investor_exld_calculations',
            'category_deal_proceeds': 'deal_proceeds',
            'category_carry_payments': 'carry_payments',
            'category_fair_market_value': 'fair_market_value'
        })

        return accumulated_track_record

    @staticmethod
    def calculate_total_value(total_track_record, accumulated_track_record):
        # gross invest cost + deal proceeds + carry payments + fmv
        # net invest cost + deal proceeds + fmv - carry provisions
        # invest cost raus

        ### Gross Deal Proceeds

        total_track_record["fund_gross_deal_proceeds"] = total_track_record["fund_deal_proceeds"] + \
                                                       total_track_record["fund_carry_payments"]

        total_track_record["category_gross_deal_proceeds"] = total_track_record["category_deal_proceeds"] + \
                                                           total_track_record["category_carry_payments"]

        accumulated_track_record["gross_deal_proceeds"] = accumulated_track_record["deal_proceeds"] + \
                                                        accumulated_track_record["carry_payments"]

        ### Gross Total Value

        total_track_record["fund_gross_total_value"] = total_track_record["fund_deal_proceeds"] + \
                                                       total_track_record["fund_fair_market_value"] + \
                                                       total_track_record["fund_carry_payments"]

        total_track_record["category_gross_total_value"] = total_track_record["category_deal_proceeds"] + \
                                                           total_track_record["category_fair_market_value"] + \
                                                           total_track_record["category_carry_payments"]

        accumulated_track_record["gross_total_value"] = accumulated_track_record["deal_proceeds"] + \
                                                        accumulated_track_record["fair_market_value"] + \
                                                        accumulated_track_record["carry_payments"]

        ### Net Total Value

        total_track_record["fund_net_total_value"] = total_track_record["fund_deal_proceeds"] + \
                                                     total_track_record["fund_fair_market_value"] + \
                                                     total_track_record["fund_carry_provision"] + \
                                                        total_track_record["fund_carry_provision_investor_exld_calculations"]

        total_track_record["category_net_total_value"] = total_track_record["category_deal_proceeds"] + \
                                                         total_track_record["category_fair_market_value"] + \
                                                         total_track_record["category_carry_provision"] + \
                                                         total_track_record["category_carry_provision_investor_exld_calculations"]

        accumulated_track_record["net_total_value"] = accumulated_track_record["deal_proceeds"] + \
                                                      accumulated_track_record["fair_market_value"] + \
                                                      accumulated_track_record["carry_provision"] + \
                                                      accumulated_track_record["carry_provision_investor_exld_calculations"]

        ### MoM -  Total Value / Total Invest
        total_track_record["fund_gross_mom"] = total_track_record["fund_gross_total_value"] / total_track_record[
            "fund_invest_cost"]

        total_track_record["fund_net_mom"] = total_track_record["fund_net_total_value"] / total_track_record[
            "fund_invest_cost"]

        total_track_record["category_gross_mom"] = total_track_record["category_gross_total_value"] / \
                                                   total_track_record[
                                                       "category_invest_cost"]

        total_track_record["category_net_mom"] = total_track_record["category_net_total_value"] / total_track_record[
            "category_invest_cost"]

        accumulated_track_record["gross_mom"] = accumulated_track_record["gross_total_value"] / \
                                                accumulated_track_record[
                                                    "invest_cost"]

        accumulated_track_record["net_mom"] = accumulated_track_record["net_total_value"] / accumulated_track_record[
            "invest_cost"]

        ### Replace Inf
        total_track_record = total_track_record.fillna(0)
        total_track_record.replace([np.inf, -np.inf], 0, inplace=True)
        accumulated_track_record = accumulated_track_record.fillna(0)
        accumulated_track_record.replace([np.inf, -np.inf], 0, inplace=True)

        return total_track_record, accumulated_track_record

    @staticmethod
    def get_fmv_carry_payment(quarter, is_fund_raising, valuation):

        if is_fund_raising:
            valuation_category = pd.DataFrame.from_records(
                valuation.values('vehicle__fund', 'vehicle__vehicle_name', 'vehicle__category', 'transaction_type',
                                 'target__investment_name', 'transaction_date',
                                 'target__entry_date', 'target__exit_date', 'amount', 'cash_amount'))
            valuation_fund = pd.DataFrame.from_records(
                valuation.values('vehicle__fund', 'vehicle__vehicle_name', 'target__investment_name',
                                 'transaction_type',
                                 'transaction_date', 'amount', 'cash_amount'))

            valuation_category['cash_amount'] = valuation_category['cash_amount'].fillna(0)
            valuation_category['amount'] = np.where(valuation_category['cash_amount'] != 0,
                                                    valuation_category['cash_amount'], valuation_category['amount'])

            valuation_category = valuation_category.drop(['cash_amount'], axis=1)

            valuation_fund['cash_amount'] = valuation_fund['cash_amount'].fillna(0)
            valuation_fund['amount'] = np.where(valuation_fund['cash_amount'] != 0,
                                                valuation_fund['cash_amount'], valuation_fund['amount'])

            valuation_fund = valuation_fund.drop(['cash_amount'], axis=1)

        else:
            valuation_category = pd.DataFrame.from_records(
                valuation.values('vehicle__fund', 'vehicle__vehicle_name', 'vehicle__category', 'transaction_type',
                                 'target__investment_name', 'transaction_date',
                                 'target__entry_date', 'target__exit_date', 'amount'))
            valuation_fund = pd.DataFrame.from_records(
                valuation.values('vehicle__fund', 'vehicle__vehicle_name', 'target__investment_name',
                                 'transaction_type',
                                 'transaction_date', 'amount'))

            valuation_category['transaction_date'] = quarter.report_date
            valuation_fund['transaction_date'] = quarter.report_date

        return valuation_fund, valuation_category

    @staticmethod
    def calc_irr_category_target(quarter, is_fund_raising, total_track_record, target_cashflows, valuation, vehicles, investors):
        ### IRR - Target - net: deal proceeds (cashflow) +  fmv (valuation / neueste fmv oder welche?) - carry provisions(valuation)
        # gross irr invest cost + deal proceeds + carry payments + fmv
        # nett irr invest cost + deal proceeds + fmv - carry provisions
        target_amount = pd.DataFrame.from_records(
            target_cashflows.values('vehicle__fund', 'vehicle__category', 'target__investment_name',
                                    'transaction_date',
                                    'transaction_type',
                                    'transaction_type_detail',
                                    'amount_eur'))

        valuation_fund, valuation_category = TargetTrackRecord.get_fmv_carry_payment(quarter, is_fund_raising, valuation)

        unique_targets = total_track_record[['fund', 'category', 'target']].drop_duplicates()

        all_target_irr = []

        for row_index, row in unique_targets.iterrows():
            input_invest_cost = target_amount.loc[
                (target_amount['target__investment_name'] == row['target']) &
                (target_amount['vehicle__fund'] == row['fund']) &
                (target_amount['vehicle__category'] == row['category']) &
                (target_amount['transaction_type'] == 'Invest Cost')
                ]

            input_deal_proceeds = target_amount.loc[
                (target_amount['target__investment_name'] == row['target']) &
                (target_amount['vehicle__fund'] == row['fund']) &
                (target_amount['vehicle__category'] == row['category']) &
                (target_amount['transaction_type'] == 'Deal Proceeds')
                ]

            input_carry_payment = target_amount.loc[
                (target_amount['target__investment_name'] == row['target']) &
                (target_amount['vehicle__fund'] == row['fund']) &
                (target_amount['transaction_type'] == 'Carry Payment')
                ]

            input_invest_cost = input_invest_cost.rename(
                columns={'amount_eur': 'amount'})

            input_deal_proceeds = input_deal_proceeds.rename(
                columns={'amount_eur': 'amount'})

            input_carry_payment = input_carry_payment.rename(
                columns={'amount_eur': 'amount'})

            if len(input_carry_payment) > 0:
                carry_payments = []
                for cp_index, cp in input_carry_payment.iterrows():
                    vehicles_df = pd.DataFrame.from_records(
                        vehicles.filter(vehicle_name=cp['transaction_type_detail'])
                        .values('vehicle_name', 'category'))
                    if len(vehicles_df) == 1:
                        carry_payment = (
                            cp['vehicle__fund'],
                            vehicles_df['category'][0],
                            cp['target__investment_name'],
                            cp['transaction_type'],
                            cp['transaction_date'],
                            cp['amount'])
                        carry_payments.append(carry_payment)
                        continue

                    investor_df = pd.DataFrame.from_records(
                        investors.filter(investor_name=cp['transaction_type_detail'])
                        .values('investor_name'))

                    if len(investor_df) == 1:
                        cp = (cp['vehicle__fund'],
                              'CoInv',
                              cp['target__investment_name'],
                              cp['transaction_type'],
                              cp['transaction_date'],
                              cp['amount'])
                        carry_payments.append(cp)
                        continue

                fund_carry_payment = pd.DataFrame(carry_payments,
                                                  columns=['vehicle__fund',
                                                           'vehicle__category',
                                                           'target__investment_name',
                                                           'transaction_type',
                                                           'transaction_date',
                                                           'amount'])

                input_carry_payment = fund_carry_payment[fund_carry_payment['vehicle__category'] == row['category']]

            input_fmv = valuation_category[
                ['vehicle__fund', 'vehicle__category', 'transaction_type', 'target__investment_name',
                 'transaction_date', 'amount']].loc[
                (valuation_category['target__investment_name'] == row['target']) &
                (valuation_category['vehicle__fund'] == row['fund']) &
                (valuation_category['vehicle__category'] == row['category']) &
                (valuation_category['transaction_type'] == 'Fair Market Value')
                ]
            input_carry_provision = valuation_category[
                ['vehicle__fund', 'vehicle__category', 'transaction_type', 'target__investment_name',
                 'transaction_date', 'amount']].loc[
                (valuation_category['target__investment_name'] == row['target']) &
                (valuation_category['vehicle__fund'] == row['fund']) &
                (valuation_category['vehicle__category'] == row['category']) &
                (valuation_category['transaction_type'] == 'Carry Provision')
                ]

            # nett irr (-)invest cost + deal proceeds + fmv + (-)carry provisions
            net_total = pandas.concat(
                [input_invest_cost, input_deal_proceeds,
                 input_fmv[['vehicle__fund', 'vehicle__category', 'target__investment_name', 'transaction_date',
                            'transaction_type', 'amount']],
                 input_carry_provision[
                     ['vehicle__fund', 'vehicle__category', 'target__investment_name', 'transaction_date',
                      'transaction_type', 'amount']]])

            if 'Invest Cost' not in list(net_total['transaction_type']):
                net_irr_value = float('inf')
            else:
                try:
                    net_total = net_total.sort_values(by='transaction_date', ascending=False)
                    net_irr_value = xirr(net_total['amount'], net_total['transaction_date'])
                except ValueError:
                    net_irr_value = 0

            # gross irr (-)invest cost + deal proceeds + carry payments + fmv
            # carry payment - transaction_type_detail category
            gross_total = pandas.concat(
                [input_invest_cost, input_deal_proceeds, input_carry_payment,
                 input_fmv[['vehicle__fund', 'vehicle__category', 'target__investment_name', 'transaction_date',
                            'transaction_type', 'amount']]])

            if 'Invest Cost' not in list(gross_total.transaction_type):
                gross_irr_value = float('inf')

            else:
                try:
                    gross_total = gross_total.sort_values(by='transaction_date', ascending=False)
                    gross_irr_value = xirr(gross_total['amount'], gross_total['transaction_date'])
                except ValueError:
                    gross_irr_value = 0

            target_irr = (row['fund'], row['category'], row['target'], net_irr_value, gross_irr_value)
            all_target_irr.append(target_irr)

        target_irr = pd.DataFrame(all_target_irr,
                                  columns=['fund', 'category', 'target', 'category_net_irr', 'category_gross_irr'])

        total_track_record = pd.merge(total_track_record, target_irr,
                                      left_on=['fund', 'target', 'category'],
                                      right_on=['fund', 'target', 'category'],
                                      how='outer')
        total_track_record = total_track_record.fillna(0)

        return total_track_record

    @staticmethod
    def calc_irr_fund_target(quarter, is_fund_raising, total_track_record, target_cashflows, valuation):

        target_amount = pd.DataFrame.from_records(
            target_cashflows.values('vehicle__fund', 'target__investment_name', 'transaction_date',
                                    'transaction_type',
                                    'amount_eur'))

        valuation_fund, valuation_category = TargetTrackRecord.get_fmv_carry_payment(quarter, is_fund_raising, valuation)

        unique_targets = total_track_record[['fund', 'target']].drop_duplicates()

        all_target_irr = []

        for row_index, row in unique_targets.iterrows():
            input_invest_cost = target_amount.loc[
                (target_amount['target__investment_name'] == row['target']) &
                (target_amount['vehicle__fund'] == row['fund']) &
                (target_amount['transaction_type'] == 'Invest Cost')
                ]

            input_deal_proceeds = target_amount.loc[
                (target_amount['target__investment_name'] == row['target']) &
                (target_amount['vehicle__fund'] == row['fund']) &
                (target_amount['transaction_type'] == 'Deal Proceeds')
                ]

            input_carry_payment = target_amount.loc[
                (target_amount['target__investment_name'] == row['target']) &
                (target_amount['vehicle__fund'] == row['fund']) &
                (target_amount['transaction_type'] == 'Carry Payment')
                ]

            input_invest_cost = input_invest_cost.rename(
                columns={'amount_eur': 'amount'})

            input_deal_proceeds = input_deal_proceeds.rename(
                columns={'amount_eur': 'amount'})

            input_carry_payment = input_carry_payment.rename(
                columns={'amount_eur': 'amount'})

            input_fmv = valuation_category[
                ['vehicle__fund', 'transaction_type', 'target__investment_name',
                 'transaction_date', 'amount']].loc[
                (valuation_category['target__investment_name'] == row['target']) &
                (valuation_category['vehicle__fund'] == row['fund']) &
                (valuation_category['transaction_type'] == 'Fair Market Value')
                ]
            input_carry_provision = valuation_category[
                ['vehicle__fund', 'transaction_type', 'target__investment_name',
                 'transaction_date', 'amount']].loc[
                (valuation_category['target__investment_name'] == row['target']) &
                (valuation_category['vehicle__fund'] == row['fund']) &
                (valuation_category['transaction_type'] == 'Carry Provision')
                ]

            # net irr invest cost + deal proceeds + fmv - carry provisions
            net_total = pandas.concat(
                [input_invest_cost, input_deal_proceeds,
                 input_fmv[['vehicle__fund', 'target__investment_name', 'transaction_date',
                            'transaction_type', 'amount']],
                 input_carry_provision[
                     ['vehicle__fund', 'target__investment_name', 'transaction_date',
                      'transaction_type', 'amount']]])

            if 'Invest Cost' not in list(net_total.transaction_type):
                net_irr_value = float('inf')
            else:
                try:
                    net_total = net_total.sort_values(by='transaction_date', ascending=False)
                    net_irr_value = xirr(net_total['amount'], net_total['transaction_date'])
                except ValueError:
                    net_irr_value = 0

            # gross irr invest cost + deal proceeds + carry payments + fmv
            gross_total = pandas.concat(
                [input_invest_cost, input_deal_proceeds, input_carry_payment,
                 input_fmv[['vehicle__fund', 'target__investment_name', 'transaction_date',
                            'transaction_type', 'amount']]])

            if 'Invest Cost' not in list(gross_total.transaction_type):
                gross_irr_value = float('inf')

            else:
                try:
                    gross_total = gross_total.sort_values(by='transaction_date', ascending=False)
                    gross_irr_value = xirr(gross_total['amount'], gross_total['transaction_date'])
                except ValueError:
                    gross_irr_value = 0

            target_irr = (row['fund'], row['target'], net_irr_value, gross_irr_value)
            all_target_irr.append(target_irr)

        target_irr = pd.DataFrame(all_target_irr, columns=['fund', 'target', 'fund_net_irr', 'fund_gross_irr'])

        total_track_record = pd.merge(total_track_record, target_irr,
                                      left_on=['fund', 'target'],
                                      right_on=['fund', 'target'],
                                      how='outer')
        total_track_record = total_track_record.fillna(0)

        return total_track_record

    @staticmethod
    def calculate_irr_accumulated(quarter, is_fund_raising, accumulated_track_record, target_cashflows, valuation, vehicles, investors):
        ### IRR - Target - net: deal proceeds (cashflow) +  fmv (valuation / neueste fmv oder welche?) - carry provisions(valuation)
        # gross irr invest cost + deal proceeds + carry payments + fmv
        # nett irr invest cost + deal proceeds + fmv - carry provisions
        cf_total = pd.DataFrame.from_records(
            target_cashflows.values('vehicle__fund',
                                    'vehicle__category',
                                    'target__exit_date',
                                    'transaction_date',
                                    'transaction_type',
                                    'transaction_type_detail',
                                    'amount_eur'))

        # if 'target__exit_date' is NaT then replace with 'Active' else 'Realized'
        cf_total['target__exit_date'] = cf_total['target__exit_date'].fillna('Unrealized')
        cf_total['target__exit_date'] = np.where(cf_total['target__exit_date'] == 'Unrealized', 'Unrealized',
                                                 'Realized')

        valuation_fund, valuation_category = TargetTrackRecord.get_fmv_carry_payment(quarter, is_fund_raising, valuation)

        unique_targets = accumulated_track_record[['fund', 'category']].drop_duplicates()

        valuation = valuation_category[['vehicle__fund', 'vehicle__category', 'transaction_date',
                                        'transaction_type', 'amount']]

        # Reassign Carry Payments according to the Transaction Type Detail
        carry_payment_df = cf_total.loc[(cf_total['transaction_type'] == 'Carry Payment')]

        if len(carry_payment_df) > 0:
            carry_payment_new_assign = []
            for cp_index, cp in carry_payment_df.iterrows():
                vehicles_df = pd.DataFrame.from_records(
                    vehicles.filter(vehicle_name=cp['transaction_type_detail'])
                    .values('vehicle_name', 'category'))
                investor_df = pd.DataFrame.from_records(
                    investors.filter(investor_name=cp['transaction_type_detail'])
                    .values('investor_name'))

                if len(vehicles_df) == 1:
                    carry_payment = (
                        cp['vehicle__fund'],
                        vehicles_df['category'][0],
                        cp['transaction_type'],
                        cp['transaction_date'],
                        cp['amount_eur'])
                    carry_payment_new_assign.append(carry_payment)
                    continue

                elif len(investor_df) == 1:
                    cp = (cp['vehicle__fund'],
                          'CoInv',
                          cp['transaction_type'],
                          cp['transaction_date'],
                          cp['amount_eur'])
                    carry_payment_new_assign.append(cp)
                    continue
            carry_payment_df = pd.DataFrame(carry_payment_new_assign,
                                            columns=['vehicle__fund',
                                                     'vehicle__category',
                                                     'transaction_type',
                                                     'transaction_date',
                                                     'amount'])

        all_target_irr = []

        cf_total = cf_total.rename(columns={'amount_eur': 'amount'})

        # invest cost, deal proceeds, fmv, carry provision
        net_total = pandas.concat(
            [
                cf_total.loc[(cf_total['transaction_type'] == 'Deal Proceeds')],
                cf_total.loc[(cf_total['transaction_type'] == 'Invest Cost')],
                valuation
            ])

        # invest cost, deal proceeds, fmv, carry payment
        gross_total = pandas.concat(
            [
                cf_total.loc[(cf_total['transaction_type'] == 'Deal Proceeds')],
                cf_total.loc[(cf_total['transaction_type'] == 'Invest Cost')],
                valuation.loc[(valuation['transaction_type'] == 'Fair Market Value')],
                carry_payment_df
            ])

        for row_index, row in unique_targets.iterrows():

            if row['category'] == 'Total':
                row_net_total = net_total.loc[
                    (net_total['vehicle__fund'] == row['fund'])
                ]

                row_gross_total = gross_total.loc[
                    (gross_total['vehicle__fund'] == row['fund'])
                ]

            elif row['category'] == 'Armira I excluding CoInv':
                row_net_total = net_total.loc[
                    (net_total['vehicle__fund'] == row['fund']) &
                    (net_total['vehicle__category'] != 'CoInv')
                    ]

                row_gross_total = gross_total.loc[
                    (gross_total['vehicle__fund'] == row['fund']) &
                    (gross_total['vehicle__category'] != 'CoInv')
                    ]

            elif row['category'] == 'Realized excluding CoInv' or row['category'] == 'Unrealized excluding CoInv':
                if row['category'] == 'Realized excluding CoInv':
                    is_exit = 'Realized'
                else:
                    is_exit = 'Unrealized'

                a1_row_net_total = net_total.loc[
                    (net_total['vehicle__fund'] == 'Armira I') &
                    (net_total['target__exit_date'] == is_exit) &
                    (net_total['vehicle__category'] != 'CoInv')
                    ]
                a2_row_net_total = net_total.loc[
                    (net_total['vehicle__fund'] == 'Armira II') &
                    (net_total['target__exit_date'] == is_exit) &
                    (net_total['vehicle__category'] != 'CoInv')
                    ]
                row_net_total = pd.concat([a1_row_net_total, a2_row_net_total])

                a1_row_gross_total = gross_total.loc[
                    (gross_total['vehicle__fund'] == 'Armira I') &
                    (gross_total['target__exit_date'] == is_exit) &
                    (gross_total['vehicle__category'] != 'CoInv')
                    ]
                a2_row_gross_total = gross_total.loc[
                    (gross_total['vehicle__fund'] == 'Armira II') &
                    (gross_total['target__exit_date'] == is_exit) &
                    (gross_total['vehicle__category'] != 'CoInv')
                    ]
                row_gross_total = pd.concat([a1_row_gross_total, a2_row_gross_total])

            elif row['category'] == 'AII Core + AI excluding CoInv':
                a1_row_net_total = net_total.loc[
                    (net_total['vehicle__fund'] == 'Armira I') &
                    (net_total['vehicle__category'] != 'CoInv')
                    ]
                a2_row_net_total = net_total.loc[
                    (net_total['vehicle__fund'] == 'Armira II') &
                    (net_total['vehicle__category'] == 'Core')
                    ]
                row_net_total = pd.concat([a1_row_net_total, a2_row_net_total])

                a1_row_gross_total = gross_total.loc[
                    (gross_total['vehicle__fund'] == 'Armira I') &
                    (gross_total['vehicle__category'] != 'CoInv')
                    ]
                a2_row_gross_total = gross_total.loc[
                    (gross_total['vehicle__fund'] == 'Armira II') &
                    (gross_total['vehicle__category'] == 'Core')
                    ]
                row_gross_total = pd.concat([a1_row_gross_total, a2_row_gross_total])

            else:
                row_net_total = net_total.loc[
                    (net_total['vehicle__fund'] == row['fund']) &
                    (net_total['vehicle__category'] == row['category'])
                    ]

                row_gross_total = gross_total.loc[
                    (gross_total['vehicle__fund'] == row['fund']) &
                    (gross_total['vehicle__category'] == row['category'])
                    ]

            # nett irr (-)invest cost + deal proceeds + fmv + (-)carry provisions
            if 'Invest Cost' not in list(row_net_total['transaction_type']):
                net_irr_value = float('inf')
            else:
                try:
                    row_net_total = row_net_total.sort_values(by='transaction_date', ascending=False)
                    net_irr_value = xirr(row_net_total['amount'], row_net_total['transaction_date'])
                except ValueError:
                    net_irr_value = 0

            # gross irr (-)invest cost + deal proceeds + carry payments + fmv
            # carry payment - transaction_type_detail category
            if 'Invest Cost' not in list(gross_total.transaction_type):
                gross_irr_value = float('inf')

            else:
                try:
                    if len(row_gross_total) == 0:
                        gross_irr_value = 0
                    else:
                        row_gross_total = row_gross_total.sort_values(by='transaction_date', ascending=False)
                        gross_irr_value = xirr(row_gross_total['amount'], row_gross_total['transaction_date'])
                except ValueError:
                    gross_irr_value = 0

            target_irr = (row['fund'], row['category'], net_irr_value, gross_irr_value)
            all_target_irr.append(target_irr)

        target_irr = pd.DataFrame(all_target_irr,
                                  columns=['fund', 'category', 'net_irr', 'gross_irr'])

        total_track_record = pd.merge(accumulated_track_record, target_irr,
                                      left_on=['fund', 'category'],
                                      right_on=['fund', 'category'],
                                      how='outer')
        total_track_record = total_track_record.fillna(0)

        return total_track_record

    def create_tabular_target_track_record(self, target_track_record):
        from Tabular_Reports.TargetTrackRecordTabular import \
            TargetTrackRecordTabular
        TargetTrackRecordTabular.objects.filter(quarter=self.quarter, target_track_record=self).delete()
        for row_index, row in target_track_record.iterrows():
            row.replace([np.inf, -np.inf], 0, inplace=True)
            target = Target.objects.filter(investment_name=row['target']).first()
            target_track_record_tabular = TargetTrackRecordTabular(
                quarter=self.quarter,
                report_date=self.quarter.report_date,
                target_track_record=self,
                fund=row['fund'],
                category=row['category'],
                target=target,
                entry_date=target.entry_date,
                exit_date=target.exit_date,
                status=row['status'],
                fund_invest_cost=row['fund_invest_cost'],
                fund_carry_provision=row['fund_carry_provision'],
                fund_deal_proceeds=row['fund_deal_proceeds'],
                fund_carry_payments=row['fund_carry_payments'],
                fund_fair_market_value=row['fund_fair_market_value'],
                fund_gross_total_value=row['fund_gross_total_value'],
                fund_net_total_value=row['fund_net_total_value'],
                fund_gross_mom=row['fund_gross_mom'],
                fund_net_mom=row['fund_net_mom'],
                fund_gross_irr=row['fund_gross_irr'],
                fund_net_irr=row['fund_net_irr'],
                category_invest_cost=row['category_invest_cost'],
                category_carry_provision=row['category_carry_provision'],
                category_deal_proceeds=row['category_deal_proceeds'],
                category_carry_payments=row['category_carry_payments'],
                category_fair_market_value=row['category_fair_market_value'],
                category_gross_total_value=row['category_gross_total_value'],
                category_net_total_value=row['category_net_total_value'],
                category_gross_mom=row['category_gross_mom'],
                category_net_mom=row['category_net_mom'],
                category_gross_irr=row['category_gross_irr'],
                category_net_irr=row['category_net_irr']
            )
            target_track_record_tabular.save()

    def create_tabular_accumulated_target_track_record(self, accumulated_target_track_record):
        from Tabular_Reports.AccTargetTrackRecordTabular import \
            AccTargetTrackRecordTabular
        AccTargetTrackRecordTabular.objects.filter(quarter=self.quarter, target_track_record=self).delete()
        for row_index, row in accumulated_target_track_record.iterrows():
            row.replace([np.inf, -np.inf], 0, inplace=True)
            accumulated_target_track_record = AccTargetTrackRecordTabular(
                quarter=self.quarter,
                report_date=self.quarter.report_date,
                target_track_record=self,
                fund=row['fund'],
                category=row['category'],
                invest_cost=row['invest_cost'],
                carry_provision=row['carry_provision'],
                deal_proceeds=row['deal_proceeds'],
                carry_payments=row['carry_payments'],
                fair_market_value=row['fair_market_value'],
                gross_total_value=row['gross_total_value'],
                net_total_value=row['net_total_value'],
                gross_mom=row['gross_mom'],
                net_mom=row['net_mom'],
                gross_irr=row['gross_irr'],
                net_irr=row['net_irr']
            )
            accumulated_target_track_record.save()

    @staticmethod
    def format_dataframes(total_track_record, accumulated_track_record):
        total_track_record = total_track_record[[
            "fund", "category", "target", "entry_date", "exit_date", "status",
            "fund_invest_cost", "fund_carry_provision", "fund_carry_provision_investor_exld_calculations",
            "fund_deal_proceeds", "fund_carry_payments", "fund_gross_deal_proceeds",
            "fund_fair_market_value", "fund_gross_total_value", "fund_net_total_value", "fund_gross_mom",
            "fund_net_mom", "fund_gross_irr", "fund_net_irr",
            "category_invest_cost", "category_carry_provision",
            "category_carry_provision_investor_exld_calculations", "category_deal_proceeds",
            "category_carry_payments", "category_gross_deal_proceeds",
            "category_fair_market_value", "category_gross_total_value", "category_net_total_value",
            "category_gross_mom", "category_net_mom", "category_gross_irr", "category_net_irr"]]
        accumulated_track_record = accumulated_track_record[[
            "fund", "category", "invest_cost", "carry_provision", "carry_provision_investor_exld_calculations",
            "deal_proceeds", "carry_payments", "gross_deal_proceeds", "fair_market_value",
            "gross_total_value", "net_total_value", "gross_mom", "net_mom", "gross_irr", "net_irr"]]
        return total_track_record, accumulated_track_record
