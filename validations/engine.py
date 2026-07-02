"""
Validation Engine — Core logic for running data quality checks.
Compares source vs target using the mapped columns and operations.
"""
import concurrent.futures
import logging
import time
import pandas as pd
from django.utils import timezone
from sqlalchemy import text
from connections.connector import ConnectorEngine
from .models import ValidationRun, ValidationResult

logger = logging.getLogger('validations')


class ValidationEngine:
    """Execute data quality validations between source and target."""

    def __init__(self, validation_run):
        self.run = validation_run
        self.mapping = validation_run.mapping
        self.source_engine = ConnectorEngine(self.mapping.source_connection)
        self.target_engine = ConnectorEngine(self.mapping.target_connection)

    def execute(self):
        """Run all validation checks for this mapping."""
        self.run.status = 'running'
        self.run.started_at = timezone.now()
        self.run.save()

        try:
            # Prefetch rules to avoid N+1 ORM queries inside loops
            column_mappings = self.mapping.column_mappings.prefetch_related('rules').all()
            if self.run.selected_columns:
                selected = [c.strip().lower() for c in self.run.selected_columns.split(',') if c.strip()]
                column_mappings = [cm for cm in column_mappings if cm.source_column.lower() in selected]

            # In-memory evaluation of active rule count to avoid database calls inside loop
            total_checks = sum(sum(1 for r in cm.rules.all() if r.is_active) for cm in column_mappings)
            self.run.total_checks = total_checks
            self.run.save()

            # Initialize prefetched results registry
            self._prefetched_results = {}

            logger.info("STEP 1 - Before aggregate prefetch")
            start_prefetch_agg = time.time()
            try:
                self._prefetch_all_aggregates(column_mappings)
            except Exception as prefetch_err:
                logger.error(f"Error prefetching aggregates: {prefetch_err}", exc_info=True)
            logger.info("STEP 2 - After aggregate prefetch")
            logger.info(f"Aggregate prefetch: {time.time() - start_prefetch_agg:.2f}s")

            start_prefetch_row = time.time()
            logger.info("STEP 2A - Before row prefetch")
            try:
                self._prefetch_all_row_by_row(column_mappings)
            except Exception as prefetch_row_err:
                logger.error(f"Error prefetching row-by-row: {prefetch_row_err}", exc_info=True)
            logger.info("STEP 3 - After row prefetch")
            logger.info(f"Row-by-row prefetch: {time.time() - start_prefetch_row:.2f}s")

            start_row_val = time.time()
            completed = 0
            passed = 0
            failed = 0
            results_to_create = []

            for col_mapping in column_mappings:
                # In-memory filtering of rules
                rules = [r for r in col_mapping.rules.all() if r.is_active]

                for rule in rules:
                    try:
                        result = self._run_check(col_mapping, rule.operation)
                        results_to_create.append(result)
                        if result.is_match:
                            passed += 1
                        else:
                            failed += 1
                    except Exception as e:
                        # Create unsaved failed ValidationResult
                        results_to_create.append(ValidationResult(
                            run=self.run,
                            column_mapping=col_mapping,
                            operation=rule.operation,
                            source_value='ERROR',
                            target_value='ERROR',
                            is_match=False,
                            difference=str(e),
                            details={'error': str(e)},
                        ))
                        failed += 1
                        logger.error(f"Check failed for {col_mapping}.{rule.operation}: {e}")

                    completed += 1
                    current_progress = int((completed / total_checks) * 100)

                    if current_progress != self.run.progress:
                        self.run.progress = current_progress
                        self.run.passed_checks = passed
                        self.run.failed_checks = failed
                        self.run.save(update_fields=[
                            "progress",
                            "passed_checks",
                            "failed_checks"
                        ])
            logger.info(f"Row validation: {time.time() - start_row_val:.2f}s")

            start_result_save = time.time()
            # Bulk insert results to database (Requirement 14: bulk inserts/commits)
            if results_to_create:
                ValidationResult.objects.bulk_create(results_to_create)
            logger.info(f"Result save: {time.time() - start_result_save:.2f}s")

            self.run.status = 'completed'
            self.run.completed_at = timezone.now()
            self.run.progress = 100
            self.run.passed_checks = passed
            self.run.failed_checks = failed
            self.run.save()

            logger.info(f"Validation run {self.run.id} completed: {passed} passed, {failed} failed out of {total_checks}")

            # Send automated email notification
            try:
                from notifications.email_service import send_validation_email
                send_validation_email(self.run)
            except Exception as email_err:
                logger.error(f"Failed to trigger email notification: {email_err}")

        except Exception as e:
            self.run.status = 'failed'
            self.run.error_message = str(e)
            self.run.completed_at = timezone.now()
            self.run.save()
            logger.error(f"Validation run {self.run.id} failed: {e}")

            # Send automated email notification on failure
            try:
                from notifications.email_service import send_validation_email
                send_validation_email(self.run)
            except Exception as email_err:
                logger.error(f"Failed to trigger email notification on run failure: {email_err}")

            raise

    def _run_check(self, col_mapping, operation):
        """Run a single validation check and save the result."""
        cache_key = (col_mapping.id, operation)
        if hasattr(self, '_prefetched_results') and cache_key in self._prefetched_results:
            prefetched = self._prefetched_results[cache_key]
            if len(prefetched) == 4:
                source_value, target_value, is_match, difference = prefetched
            else:
                source_value, target_value = prefetched
                
                # Datatype formatting tweaks for length_sum_check, null_check, sum_length
                int_ops = ('null_check', 'length_sum_check', 'sum_length', 'count', 'row_count', 'distinct_count', 'duplicate_check', 'unique_check')
                if operation in int_ops:
                    if source_value is not None:
                        try:
                            source_value = int(float(source_value))
                        except (ValueError, TypeError):
                            pass
                    if target_value is not None:
                        try:
                            target_value = int(float(target_value))
                        except (ValueError, TypeError):
                            pass

                # Trim check formatting: Found / Not Found
                if operation == 'trim_check':
                    try:
                        src_count = int(float(source_value)) if source_value is not None else 0
                        source_value = 'Found' if src_count > 0 else 'Not Found'
                    except (ValueError, TypeError):
                        source_value = 'Not Found'
                    try:
                        tgt_count = int(float(target_value)) if target_value is not None else 0
                        target_value = 'Found' if tgt_count > 0 else 'Not Found'
                    except (ValueError, TypeError):
                        target_value = 'Not Found'

                # Compare values
                is_match = self._compare(source_value, target_value, operation)
                difference = '0'
                if is_match:
                    difference = '0'
                elif source_value is None or target_value is None:
                    difference = 'N/A'
                else:
                    try:
                        diff = float(source_value) - float(target_value)
                        if diff.is_integer():
                            difference = str(int(diff))
                        else:
                            difference = f"{diff:.2f}"
                    except (ValueError, TypeError):
                        if operation == 'data_type_check':
                            difference = '0' if is_match else '1'
                        else:
                            difference = '0' if str(source_value).strip() == str(target_value).strip() else '1'
        else:
            row_by_row_ops = ('case_insensitive_check', 'contains_check', 'pattern_match')
            
            if operation in row_by_row_ops:
                # Resolve source date filters
                src_date_column = self.mapping.source_date_column if self.mapping.source_date_filter_type != 'none' else None
                src_date_start = str(self.run.source_date_filter_start) if self.run.source_date_filter_start else None
                src_date_end = str(self.run.source_date_filter_end) if self.run.source_date_filter_end else None
                src_date_operator = getattr(self.mapping, 'source_date_operator', '=') if self.mapping.source_date_filter_type == 'specific' else None
                # Fallbacks
                if not src_date_column and self.mapping.date_filter_type != 'none':
                    src_date_column = self.mapping.date_filter_column
                    src_date_start = str(self.run.date_filter_start) if self.run.date_filter_start else None
                    src_date_end = str(self.run.date_filter_end) if self.run.date_filter_end else None
                    src_date_operator = getattr(self.mapping, 'date_operator', '=') if self.mapping.date_filter_type == 'specific' else None

                # Skip date filter if column is set but dates are empty
                if src_date_column:
                    is_specific = (src_date_operator is not None)
                    if is_specific and not src_date_start:
                        src_date_column = None
                    elif not src_date_start and not src_date_end:
                        src_date_column = None

                # Resolve target date filters
                tgt_date_column = self.mapping.target_date_column if self.mapping.target_date_filter_type != 'none' else None
                tgt_date_start = str(self.run.target_date_filter_start) if self.run.target_date_filter_start else None
                tgt_date_end = str(self.run.target_date_filter_end) if self.run.target_date_filter_end else None
                tgt_date_operator = getattr(self.mapping, 'target_date_operator', '=') if self.mapping.target_date_filter_type == 'specific' else None
                # Fallbacks
                if not tgt_date_column and self.mapping.date_filter_type != 'none':
                    tgt_date_column = self.mapping.date_filter_column
                    tgt_date_start = str(self.run.date_filter_start) if self.run.date_filter_start else None
                    tgt_date_end = str(self.run.date_filter_end) if self.run.date_filter_end else None
                    tgt_date_operator = getattr(self.mapping, 'date_operator', '=') if self.mapping.date_filter_type == 'specific' else None

                # Skip date filter if column is set but dates are empty
                if tgt_date_column:
                    is_specific = (tgt_date_operator is not None)
                    if is_specific and not tgt_date_start:
                        tgt_date_column = None
                    elif not tgt_date_start and not tgt_date_end:
                        tgt_date_column = None

                source_vals = self.source_engine.get_column_values(
                    self.mapping.source_schema,
                    self.mapping.source_table,
                    col_mapping.source_column,
                    catalog=self.mapping.source_catalog if self.mapping.source_connection.connection_type == 'databricks' else None,
                    date_column=src_date_column,
                    date_start=src_date_start,
                    date_end=src_date_end,
                    date_operator=src_date_operator,
                )
                target_vals = self.target_engine.get_column_values(
                    self.mapping.target_schema,
                    self.mapping.target_table,
                    col_mapping.target_column,
                    catalog=self.mapping.target_catalog if self.mapping.target_connection.connection_type == 'databricks' else None,
                    date_column=tgt_date_column,
                    date_start=tgt_date_start,
                    date_end=tgt_date_end,
                    date_operator=tgt_date_operator,
                )

                if operation == 'case_insensitive_check':
                    if len(source_vals) != len(target_vals):
                        is_match = False
                        difference = f"Row count mismatch: source={len(source_vals)}, target={len(target_vals)}"
                    else:
                        is_match = True
                        difference = '0'
                        for i in range(len(source_vals)):
                            if str(source_vals[i]).lower() != str(target_vals[i]).lower():
                                is_match = False
                                difference = f"Mismatch at row {i+1}: source='{source_vals[i]}' target='{target_vals[i]}'"
                                break
                    source_value = 'true' if is_match else 'false'
                    target_value = 'true' if is_match else 'false'

                elif operation == 'pattern_match':
                    param = self.run.parameters.get(f"{col_mapping.source_column}:{operation}")
                    if param is None:
                        param = self.run.parameters.get(f"__all__:{operation}")
                    
                    import re
                    pat = param or r'^[a-zA-Z0-9_\-\.\s@]+$'
                    
                    # Check length match first
                    if len(source_vals) != len(target_vals):
                        is_match = False
                        difference = f"Row count mismatch: source={len(source_vals)}, target={len(target_vals)}"
                        source_value = 'false'
                        target_value = 'false'
                    else:
                        is_match = True
                        difference = '0'
                        for i in range(len(source_vals)):
                            src_val_str = str(source_vals[i])
                            tgt_val_str = str(target_vals[i])
                            
                            src_ok = bool(re.match(pat, src_val_str))
                            tgt_ok = bool(re.match(pat, tgt_val_str))
                            
                            if not src_ok or not tgt_ok:
                                is_match = False
                                if not src_ok and not tgt_ok:
                                    difference = f"Pattern mismatch at row {i+1}: both source and target failed regex check"
                                elif not src_ok:
                                    difference = f"Pattern mismatch at row {i+1}: source failed regex check (value='{src_val_str}')"
                                else:
                                    difference = f"Pattern mismatch at row {i+1}: target failed regex check (value='{tgt_val_str}')"
                                break
                        source_value = 'true' if is_match else 'false'
                        target_value = 'true' if is_match else 'false'

                elif operation == 'contains_check':
                    param = self.run.parameters.get(f"{col_mapping.source_column}:{operation}")
                    if param is None:
                        param = self.run.parameters.get(f"__all__:{operation}")
                    if param is None:
                        param = ' '
                    
                    source_ok = True
                    source_error = None
                    for i, val in enumerate(source_vals):
                        if param not in str(val):
                            source_ok = False
                            source_error = f"Row {i+1} failed check (value='{val}')"
                            break
                    
                    target_ok = True
                    target_error = None
                    for i, val in enumerate(target_vals):
                        if param not in str(val):
                            target_ok = False
                            target_error = f"Row {i+1} failed check (value='{val}')"
                            break
                    
                    is_match = source_ok and target_ok
                    source_value = 'true' if source_ok else 'false'
                    target_value = 'true' if target_ok else 'false'
                    
                    if is_match:
                        difference = '0'
                    else:
                        errs = []
                        if not source_ok:
                            errs.append(f"Source: {source_error}")
                        if not target_ok:
                            errs.append(f"Target: {target_error}")
                        difference = "; ".join(errs)

            else:
                # Traditional aggregated check logic
                source_value = self._get_value(
                    self.source_engine,
                    self.mapping.source_schema,
                    self.mapping.source_table,
                    col_mapping.source_column,
                    operation,
                    is_source=True,
                )

                target_value = self._get_value(
                    self.target_engine,
                    self.mapping.target_schema,
                    self.mapping.target_table,
                    col_mapping.target_column,
                    operation,
                    is_source=False,
                )

                # Datatype formatting tweaks for length_sum_check, null_check, sum_length
                int_ops = ('null_check', 'length_sum_check', 'sum_length', 'count', 'row_count', 'distinct_count', 'duplicate_check', 'unique_check')
                if operation in int_ops:
                    if source_value is not None:
                        try:
                            source_value = int(float(source_value))
                        except (ValueError, TypeError):
                            pass
                    if target_value is not None:
                        try:
                            target_value = int(float(target_value))
                        except (ValueError, TypeError):
                            pass

                # Trim check formatting: Found / Not Found
                if operation == 'trim_check':
                    # Original trim check returns the count of untrimmed rows
                    try:
                        src_count = int(float(source_value)) if source_value is not None else 0
                        source_value = 'Found' if src_count > 0 else 'Not Found'
                    except (ValueError, TypeError):
                        source_value = 'Not Found'
                    try:
                        tgt_count = int(float(target_value)) if target_value is not None else 0
                        target_value = 'Found' if tgt_count > 0 else 'Not Found'
                    except (ValueError, TypeError):
                        target_value = 'Not Found'

                # Compare values
                is_match = self._compare(source_value, target_value, operation)
                difference = '0'
                if is_match:
                    difference = '0'
                elif source_value is None or target_value is None:
                    difference = 'N/A'
                else:
                    try:
                        diff = float(source_value) - float(target_value)
                        if diff.is_integer():
                            difference = str(int(diff))
                        else:
                            difference = f"{diff:.2f}"
                    except (ValueError, TypeError):
                        if operation == 'data_type_check':
                            difference = '0' if is_match else '1'
                        else:
                            difference = '0' if str(source_value).strip() == str(target_value).strip() else '1'

        result = ValidationResult(
            run=self.run,
            column_mapping=col_mapping,
            operation=operation,
            source_value=str(source_value) if source_value is not None else None,
            target_value=str(target_value) if target_value is not None else None,
            is_match=is_match,
            difference=difference,
            details={
                'source_column': col_mapping.source_column,
                'target_column': col_mapping.target_column,
            },
        )
        if not hasattr(self, '_prefetched_results'):
            result.save()
        return result

    def _get_value(self, engine, schema, table, column, operation, is_source=True):
        """Get an aggregated value from a data source."""
        date_operator = None
        date_operator_start = '>='
        date_operator_end = '<='
        if is_source:
            date_column = self.mapping.source_date_column if self.mapping.source_date_filter_type != 'none' else None
            date_start = str(self.run.source_date_filter_start) if self.run.source_date_filter_start else None
            date_end = str(self.run.source_date_filter_end) if self.run.source_date_filter_end else None
            if self.mapping.source_date_filter_type == 'specific':
                date_operator = getattr(self.mapping, 'source_date_operator', '=')
            elif self.mapping.source_date_filter_type == 'range':
                date_operator_start = getattr(self.mapping, 'source_date_range_operator_start', '>=')
                date_operator_end = getattr(self.mapping, 'source_date_range_operator_end', '<=')
            # Fallback
            if not date_column and self.mapping.date_filter_type != 'none':
                date_column = self.mapping.date_filter_column
                date_start = str(self.run.date_filter_start) if self.run.date_filter_start else None
                date_end = str(self.run.date_filter_end) if self.run.date_filter_end else None
                if self.mapping.date_filter_type == 'specific':
                    date_operator = getattr(self.mapping, 'date_operator', '=')

            # Skip date filter if column is set but dates are empty
            if date_column:
                if date_operator and not date_start:
                    date_column = None
                elif not date_operator and not date_start and not date_end:
                    date_column = None
        else:
            date_column = self.mapping.target_date_column if self.mapping.target_date_filter_type != 'none' else None
            date_start = str(self.run.target_date_filter_start) if self.run.target_date_filter_start else None
            date_end = str(self.run.target_date_filter_end) if self.run.target_date_filter_end else None
            if self.mapping.target_date_filter_type == 'specific':
                date_operator = getattr(self.mapping, 'target_date_operator', '=')
            elif self.mapping.target_date_filter_type == 'range':
                date_operator_start = getattr(self.mapping, 'target_date_range_operator_start', '>=')
                date_operator_end = getattr(self.mapping, 'target_date_range_operator_end', '<=')
            # Fallback
            if not date_column and self.mapping.date_filter_type != 'none':
                date_column = self.mapping.date_filter_column
                date_start = str(self.run.date_filter_start) if self.run.date_filter_start else None
                date_end = str(self.run.date_filter_end) if self.run.date_filter_end else None
                if self.mapping.date_filter_type == 'specific':
                    date_operator = getattr(self.mapping, 'date_operator', '=')

            # Skip date filter if column is set but dates are empty
            if date_column:
                if date_operator and not date_start:
                    date_column = None
                elif not date_operator and not date_start and not date_end:
                    date_column = None

        catalog = (self.mapping.source_catalog if is_source else self.mapping.target_catalog) if engine.connection.connection_type == 'databricks' else None

        if operation == 'duplicate_check':
            return engine.check_duplicates(
                schema, table, column,
                catalog=catalog,
                date_column=date_column,
                date_start=date_start,
                date_end=date_end,
                date_operator=date_operator,
                date_operator_start=date_operator_start,
                date_operator_end=date_operator_end
            )

        return engine.get_aggregation(
            schema=schema,
            table=table,
            column=column,
            operation=operation,
            catalog=catalog,
            date_column=date_column,
            date_start=date_start,
            date_end=date_end,
            date_operator=date_operator,
            date_operator_start=date_operator_start,
            date_operator_end=date_operator_end,
        )

    def _compare(self, source_value, target_value, operation):
        """Compare source and target values."""
        if source_value is None or target_value is None:
            return source_value == target_value

        if operation == 'data_type_check':
            import re
            def parse_type(t_str):
                if not t_str:
                    return {'base': '', 'length': None, 'precision': None, 'scale': None}
                t_str = str(t_str).strip().lower()
                # Parse e.g. character varying(100) or decimal(10,2) or integer
                match = re.search(r'([a-z0-9\s]+)(?:\(([^)]+)\))?', t_str)
                if not match:
                    return {'base': t_str, 'length': None, 'precision': None, 'scale': None}
                base_type = match.group(1).strip()
                args_str = match.group(2)
                
                length = None
                precision = None
                scale = None
                
                if args_str:
                    args = [a.strip() for a in args_str.split(',')]
                    if len(args) == 1:
                        length = args[0]
                        # If base type is decimal/numeric, the single arg is precision
                        if base_type in ('numeric', 'decimal', 'number'):
                            precision = args[0]
                    elif len(args) == 2:
                        precision = args[0]
                        scale = args[1]
                
                synonyms = {
                    'integer': 'int',
                    'int4': 'int',
                    'int8': 'bigint',
                    'character varying': 'varchar',
                    'varying character': 'varchar',
                    'double precision': 'double',
                    'float64': 'float',
                    'float4': 'float',
                    'float8': 'double',
                    'object': 'string',
                    'text': 'string',
                    'numeric': 'decimal',
                    'char': 'varchar',
                }
                for k, v in synonyms.items():
                    if base_type == k:
                        base_type = v
                return {'base': base_type, 'length': length, 'precision': precision, 'scale': scale}

            s_parsed = parse_type(source_value)
            t_parsed = parse_type(target_value)
            
            return (s_parsed['base'] == t_parsed['base'] and
                    s_parsed['length'] == t_parsed['length'] and
                    s_parsed['precision'] == t_parsed['precision'] and
                    s_parsed['scale'] == t_parsed['scale'])

        try:
            s = float(source_value)
            t = float(target_value)
            if s == t:
                return True
            # Require exact match for counts or integer values
            if operation in ('count', 'row_count', 'distinct_count', 'duplicate_check', 'null_check', 'unique_check'):
                return False
            if s.is_integer() and t.is_integer():
                return False
            # Allow a tiny relative tolerance for true floating-point values
            tolerance = max(abs(s), abs(t)) * 1e-9
            return abs(s - t) <= tolerance
        except (ValueError, TypeError):
            # String comparison
            return str(source_value).strip() == str(target_value).strip()

    def _prefetch_all_aggregates(self, column_mappings):
        """Pre-fetch all aggregate validation check values in batch from source and target engines in parallel."""
    
        logger.info("=== ENTERED _prefetch_all_aggregates ===")
    
        source_rules = []
        target_rules = []
    
        row_by_row_ops = (
            'case_insensitive_check',
            'contains_check',
            'pattern_match'
        )
    
        for col_mapping in column_mappings:
            rules = [r for r in col_mapping.rules.all() if r.is_active]
            for rule in rules:
                if rule.operation != 'data_type_check':
                    source_rules.append((col_mapping, rule.operation))
                    target_rules.append((col_mapping, rule.operation))
    
        logger.info(
            f"Aggregate rules collected: "
            f"source_rules={len(source_rules)}, "
            f"target_rules={len(target_rules)}"
        )
    
        if not source_rules and not target_rules:
            logger.info("No aggregate rules found. Skipping aggregate prefetch.")
            if not hasattr(self, '_prefetched_results'):
                self._prefetched_results = {}
            return
            
        if not hasattr(self, '_prefetched_results'):
            self._prefetched_results = {}
            
        logger.info("Starting aggregate thread pool")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_to_engine = {
                executor.submit(
                    self._fetch_engine_aggregates_batch,
                    self.source_engine,
                    source_rules,
                    True
                ): 'source',
                executor.submit(
                    self._fetch_engine_aggregates_batch,
                    self.target_engine,
                    target_rules,
                    False
                ): 'target',
            }
            
            source_results = {}
            target_results = {}
            
            logger.info("Waiting for aggregate futures to complete...")
            for future in concurrent.futures.as_completed(future_to_engine):
                engine_type = future_to_engine[future]
                logger.info(
                    f"Aggregate future completed callback received: "
                    f"{engine_type}"
                )
                try:
                    res = future.result()
                    logger.info(
                        f"{engine_type} aggregate future returned "
                        f"{len(res)} results"
                    )
                    if engine_type == 'source':
                        source_results = res
                    else:
                        target_results = res
                except Exception as exc:
                    logger.error(
                        f"{engine_type} prefetch query generated an exception: {exc}",
                        exc_info=True
                    )
                    
            logger.info(
                f"Aggregate futures finished. "
                f"Source results={len(source_results)}, "
                f"Target results={len(target_results)}"
            )
            for col_mapping in column_mappings:
                rules = [r for r in col_mapping.rules.all() if r.is_active]
                for rule in rules:
                    if rule.operation != 'data_type_check':
                        key = (col_mapping.id, rule.operation)
                        src_val = source_results.get(key)
                        tgt_val = target_results.get(key)
                        self._prefetched_results[key] = (src_val, tgt_val)
                        
        logger.info(
            f"=== EXITING _prefetch_all_aggregates === "
            f"Prefetched={len(self._prefetched_results)}"
        )

    def _fetch_engine_aggregates_batch(self, engine, rules, is_source=True):
        """Fetch multiple aggregate checks in a single database/file query."""
        start_time = time.time()
        if is_source:
            schema = self.mapping.source_schema
            table = self.mapping.source_table
            date_column = self.mapping.source_date_column if self.mapping.source_date_filter_type != 'none' else None
            date_start = str(self.run.source_date_filter_start) if self.run.source_date_filter_start else None
            date_end = str(self.run.source_date_filter_end) if self.run.source_date_filter_end else None
            
            date_operator = None
            date_operator_start = '>='
            date_operator_end = '<='
            
            if self.mapping.source_date_filter_type == 'specific':
                date_operator = getattr(self.mapping, 'source_date_operator', '=')
            elif self.mapping.source_date_filter_type == 'range':
                date_operator_start = getattr(self.mapping, 'source_date_range_operator_start', '>=')
                date_operator_end = getattr(self.mapping, 'source_date_range_operator_end', '<=')
            
            # Fallback
            if not date_column and self.mapping.date_filter_type != 'none':
                date_column = self.mapping.date_filter_column
                date_start = str(self.run.date_filter_start) if self.run.date_filter_start else None
                date_end = str(self.run.date_filter_end) if self.run.date_filter_end else None
                if self.mapping.date_filter_type == 'specific':
                    date_operator = getattr(self.mapping, 'date_operator', '=')
                elif self.mapping.date_filter_type == 'range':
                    date_operator_start = getattr(self.mapping, 'date_range_operator_start', '>=')
                    date_operator_end = getattr(self.mapping, 'date_range_operator_end', '<=')
            
            if date_column:
                if date_operator and not date_start:
                    date_column = None
                elif not date_operator and not date_start and not date_end:
                    date_column = None
        else:
            schema = self.mapping.target_schema
            table = self.mapping.target_table
            date_column = self.mapping.target_date_column if self.mapping.target_date_filter_type != 'none' else None
            date_start = str(self.run.target_date_filter_start) if self.run.target_date_filter_start else None
            date_end = str(self.run.target_date_filter_end) if self.run.target_date_filter_end else None
            
            date_operator = None
            date_operator_start = '>='
            date_operator_end = '<='
            
            if self.mapping.target_date_filter_type == 'specific':
                date_operator = getattr(self.mapping, 'target_date_operator', '=')
            elif self.mapping.target_date_filter_type == 'range':
                date_operator_start = getattr(self.mapping, 'target_date_range_operator_start', '>=')
                date_operator_end = getattr(self.mapping, 'target_date_range_operator_end', '<=')
            
            # Fallback
            if not date_column and self.mapping.date_filter_type != 'none':
                date_column = self.mapping.date_filter_column
                date_start = str(self.run.date_filter_start) if self.run.date_filter_start else None
                date_end = str(self.run.date_filter_end) if self.run.date_filter_end else None
                if self.mapping.date_filter_type == 'specific':
                    date_operator = getattr(self.mapping, 'date_operator', '=')
                elif self.mapping.date_filter_type == 'range':
                    date_operator_start = getattr(self.mapping, 'date_range_operator_start', '>=')
                    date_operator_end = getattr(self.mapping, 'date_range_operator_end', '<=')

            if date_column:
                if date_operator and not date_start:
                    date_column = None
                elif not date_operator and not date_start and not date_end:
                    date_column = None

        catalog = (self.mapping.source_catalog if is_source else self.mapping.target_catalog) if engine.connection.connection_type == 'databricks' else None
        results = {}

        if engine.is_mocked():
            for col_mapping, operation in rules:
                col = col_mapping.source_column if is_source else col_mapping.target_column
                results[(col_mapping.id, operation)] = engine._mock_aggregation(col, operation)
            return results

        if engine.connection.is_file:
            df = engine.read_file(table=table)
            if df is None:
                for col_mapping, operation in rules:
                    results[(col_mapping.id, operation)] = None
                return results

            if date_column and date_column in df.columns:
                if date_operator:
                    df_date = pd.to_datetime(df[date_column])
                    ref_date = pd.to_datetime(date_start)
                    if date_operator == '=':
                        df = df[df_date == ref_date]
                    elif date_operator == '>':
                        df = df[df_date > ref_date]
                    elif date_operator == '<':
                        df = df[df_date < ref_date]
                    elif date_operator == '>=':
                        df = df[df_date >= ref_date]
                    elif date_operator == '<=':
                        df = df[df_date <= ref_date]
                else:
                    df_date = pd.to_datetime(df[date_column])
                    if date_start:
                        ref_start = pd.to_datetime(date_start)
                        if date_operator_start == '>':
                            df = df[df_date > ref_start]
                        elif date_operator_start == '<':
                            df = df[df_date < ref_start]
                        elif date_operator_start == '>=':
                            df = df[df_date >= ref_start]
                        elif date_operator_start == '<=':
                            df = df[df_date <= ref_start]
                    if date_end:
                        ref_end = pd.to_datetime(date_end)
                        if date_operator_end == '>':
                            df = df[df_date > ref_end]
                        elif date_operator_end == '<':
                            df = df[df_date < ref_end]
                        elif date_operator_end == '>=':
                            df = df[df_date >= ref_end]
                        elif date_operator_end == '<=':
                            df = df[df_date <= ref_end]

            for col_mapping, operation in rules:
                col = col_mapping.source_column if is_source else col_mapping.target_column
                if col not in df.columns:
                    results[(col_mapping.id, operation)] = None
                    continue
                results[(col_mapping.id, operation)] = self._pandas_aggregation(df, col, operation)
            return results

        # Database batch query construction
        params = {}
        select_exprs = []
        alias_to_rule = {}
        full_table = engine._build_full_table_name(table, schema=schema if schema and schema != 'file' else None, catalog=catalog)

        for idx, (col_mapping, operation) in enumerate(rules):
            col = col_mapping.source_column if is_source else col_mapping.target_column
            q_col = engine._quote_identifier(col)

            if operation == 'duplicate_check':
                expr = f"COUNT(*) - COUNT(DISTINCT {q_col})"
            elif operation == 'contains_check':
                p = self.run.parameters.get(f"{col_mapping.source_column}:{operation}")
                if p is None:
                    p = self.run.parameters.get(f"__all__:{operation}")
                if p is None:
                    p = ' '
                param_alias = f"param_{idx}"
                params[param_alias] = f"%{p}%"
                expr = f"SUM(CASE WHEN {q_col} IS NOT NULL AND {q_col} LIKE :{param_alias} THEN 0 ELSE 1 END)"
            elif operation == 'pattern_match':
                p = self.run.parameters.get(f"{col_mapping.source_column}:{operation}")
                if p is None:
                    p = self.run.parameters.get(f"__all__:{operation}")
                pat = p or r'^[a-zA-Z0-9_\-\.\s@]+$'
                t = str(engine.connection.connection_type).lower()
                param_alias = f"param_{idx}"
                params[param_alias] = pat
                
                if t == 'postgresql':
                    expr = f"SUM(CASE WHEN {q_col} IS NOT NULL AND {q_col} ~ :{param_alias} THEN 0 ELSE 1 END)"
                elif t == 'mysql':
                    expr = f"SUM(CASE WHEN {q_col} IS NOT NULL AND {q_col} REGEXP :{param_alias} THEN 0 ELSE 1 END)"
                elif t == 'sqlite':
                    expr = f"SUM(CASE WHEN {q_col} IS NOT NULL AND {q_col} REGEXP :{param_alias} THEN 0 ELSE 1 END)"
                elif t == 'databricks':
                    expr = f"SUM(CASE WHEN {q_col} IS NOT NULL AND {q_col} RLIKE :{param_alias} THEN 0 ELSE 1 END)"
                elif t == 'oracle':
                    expr = f"SUM(CASE WHEN {q_col} IS NOT NULL AND REGEXP_LIKE({q_col}, :{param_alias}) THEN 0 ELSE 1 END)"
                elif t in ('mssql', 'sqlserver'):
                    expr = f"SUM(CASE WHEN {q_col} IS NOT NULL AND {q_col} LIKE :{param_alias} THEN 0 ELSE 1 END)"
                else:
                    expr = f"SUM(CASE WHEN {q_col} IS NOT NULL AND {q_col} LIKE :{param_alias} THEN 0 ELSE 1 END)"
            elif operation == 'case_insensitive_check':
                t = str(engine.connection.connection_type).lower()
                if t == 'postgresql':
                    expr = f"MD5(CAST(SUM(hashtext(LOWER(CAST({q_col} AS TEXT)))) AS TEXT))"
                elif t == 'mysql':
                    expr = f"MD5(CAST(SUM(CRC32(LOWER(CAST({q_col} AS CHAR)))) AS CHAR))"
                elif t == 'sqlite':
                    expr = f"CAST(SUM(LENGTH(COALESCE(LOWER(CAST({q_col} AS TEXT)), ''))) AS TEXT)"
                elif t == 'databricks':
                    expr = f"CAST(SUM(hash(LOWER(CAST({q_col} AS STRING)))) AS STRING)"
                elif t in ('mssql', 'sqlserver'):
                    expr = f"CAST(SUM(CAST(BINARY_CHECKSUM(LOWER({q_col})) AS BIGINT)) AS VARCHAR)"
                elif t == 'oracle':
                    expr = f"CAST(SUM(ORA_HASH(LOWER({q_col}))) AS VARCHAR(100))"
                else:
                    expr = f"CAST(SUM(LENGTH(COALESCE(LOWER(CAST({q_col} AS VARCHAR)), ''))) AS VARCHAR)"
            else:
                op_map = {
                    'count': f'COUNT({q_col})',
                    'min': f'MIN({q_col})',
                    'max': f'MAX({q_col})',
                    'sum': f'SUM({q_col})',
                    'avg': f'AVG({q_col})',
                    'distinct_count': f'COUNT(DISTINCT {q_col})',
                    'null_check': f'SUM(CASE WHEN {q_col} IS NULL THEN 1 ELSE 0 END)',
                    'row_count': 'COUNT(*)',
                    'min_date': f'MIN({q_col})',
                    'max_date': f'MAX({q_col})',
                    'length_sum_check': f'SUM(LENGTH({q_col}))',
                    'sum_length': f'SUM(LENGTH({q_col}))',
                    'regex_check': f'SUM(CASE WHEN {q_col} IS NOT NULL AND {q_col} != \'\' THEN 1 ELSE 0 END)',
                    'unique_check': f'COUNT(DISTINCT {q_col})',
                    'range_check': f'SUM(CASE WHEN {q_col} >= 0 THEN 1 ELSE 0 END)',
                    'equals': f'SUM({q_col})',
                    'equals_check': f'MIN({q_col})',
                    'trim_check': f'SUM(CASE WHEN {q_col} != TRIM({q_col}) THEN 1 ELSE 0 END)',
                    'starts_with_check': f'SUM(CASE WHEN {q_col} IS NOT NULL AND SUBSTR({q_col}, 1, 1) BETWEEN \'A\' AND \'z\' THEN 1 ELSE 0 END)',
                    'ends_with_check': f'SUM(CASE WHEN {q_col} IS NOT NULL AND SUBSTR({q_col}, LENGTH({q_col}), 1) BETWEEN \'A\' AND \'z\' THEN 1 ELSE 0 END)',
                    'pattern_match': engine._pattern_match_sql(q_col),
                    'hash_validation': engine._hash_validation_sql(q_col),
                }
                expr = op_map.get(operation, f'COUNT({q_col})')

            alias = f"col_alias_{idx}"
            select_exprs.append(f"{expr} AS {alias}")
            alias_to_rule[alias] = (col_mapping.id, operation)

        if not select_exprs:
            return {}

        query = f"SELECT {', '.join(select_exprs)} FROM {full_table}"

        conditions = []
        if date_column:
            q_date_col = engine._quote_identifier(date_column)
            if date_operator:
                if date_start:
                    conditions.append(f'{q_date_col} {date_operator} :date_start')
            else:
                if date_start:
                    conditions.append(f'{q_date_col} {date_operator_start} :date_start')
                if date_end:
                    conditions.append(f'{q_date_col} {date_operator_end} :date_end')

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        if date_start:
            params['date_start'] = date_start
        if not date_operator and date_end:
            params['date_end'] = date_end

        try:
            df = engine.execute_query(query, params)
            if df is not None and not df.empty:
                row = df.iloc[0]
                for alias, val in row.items():
                    key = alias_to_rule.get(alias)
                    if key:
                        if hasattr(val, 'item'):
                            val = val.item()
                        results[key] = val
            else:
                for key in alias_to_rule.values():
                    results[key] = None
        except Exception as e:
            logger.error(f"Error in batch query execution on {full_table}: {e}")
            for key in alias_to_rule.values():
                results[key] = None
        logger.info(
            f"LEAVING _fetch_engine_aggregates_batch"
            f"({'source' if is_source else 'target'})"
            f"rows={len(results)}"
        )
        logger.info(f"Fetch engine aggregates batch ({'source' if is_source else 'target'}): {time.time()-start_time:.2f}s")
        return results

    def _pandas_aggregation(self, df, column, operation):
        """Perform aggregation on pandas column for batch processing of file connections."""
        try:
            col = self.source_engine._get_cleaned_column(df[column])
            if operation == 'count':
                res = col.count()
            elif operation in ('min', 'equals_check'):
                res = col.min()
            elif operation == 'max':
                res = col.max()
            elif operation in ('sum', 'equals'):
                res = col.sum()
            elif operation == 'avg':
                res = col.mean()
            elif operation in ('distinct_count', 'unique_check'):
                res = col.nunique()
            elif operation == 'null_check':
                res = col.isnull().sum()
            elif operation == 'row_count':
                res = len(df)
            elif operation == 'duplicate_check':
                res = col.duplicated().sum()
            elif operation == 'min_date':
                res = str(col.min()) if not col.empty else None
            elif operation == 'max_date':
                res = str(col.max()) if not col.empty else None
            elif operation in ('length_sum_check', 'sum_length'):
                res = col.astype(str).str.len().sum()
            elif operation == 'regex_check':
                res = col.astype(str).str.match(r'^[a-zA-Z0-9_\-\.\s@]+$').sum()
            elif operation == 'range_check':
                res = (col >= 0).sum() if pd.api.types.is_numeric_dtype(col) else len(col)
            elif operation == 'trim_check':
                res = col.astype(str).apply(lambda x: x != x.strip()).sum()
            elif operation == 'starts_with_check':
                res = col.astype(str).str.slice(0, 1).str.isalpha().sum()
            elif operation == 'ends_with_check':
                res = col.astype(str).str.slice(-1).str.isalpha().sum()
            elif operation == 'pattern_match':
                res = col.astype(str).str.match(r'^[a-zA-Z0-9_\-\.\s@]+$').sum()
            elif operation == 'hash_validation':
                res = self.source_engine._file_hash_calculation(col)
            else:
                res = col.count()
            
            if hasattr(res, 'item'):
                return res.item()
            return res
        except Exception as e:
            logger.error(f"Error in pandas batch aggregation: {e}")
            return None

    def _prefetch_all_row_by_row(self, column_mappings):
        """Pre-fetch all row-by-row validation checks (e.g. case_insensitive_check, pattern_match, contains_check)
        in parallel batch queries and perform streaming validations chunk-by-chunk to prevent memory crashes (OOM)."""
        row_by_row_rules = []
        row_by_row_ops = ('case_insensitive_check', 'contains_check', 'pattern_match')
        
        for col_mapping in column_mappings:
            rules = [r for r in col_mapping.rules.all() if r.is_active]
            for rule in rules:
                if rule.operation in row_by_row_ops:
                    row_by_row_rules.append((col_mapping, rule.operation))
                    
        if not row_by_row_rules:
            return

        if not hasattr(self, '_prefetched_results'):
            self._prefetched_results = {}

        # SQL Pushdown check fallback logic: filter out rules that already passed database-level batch check
        filtered_row_by_row_rules = []
        for col_mapping, operation in row_by_row_rules:
            key = (col_mapping.id, operation)
            if key in self._prefetched_results:
                prefetched = self._prefetched_results[key]
                if len(prefetched) == 4:
                    continue
                
                src_val, tgt_val = prefetched
                passed = False
                
                if operation in ('contains_check', 'pattern_match'):
                    try:
                        src_fail_cnt = int(float(src_val)) if src_val is not None else -1
                        tgt_fail_cnt = int(float(tgt_val)) if tgt_val is not None else -1
                    except (ValueError, TypeError):
                        src_fail_cnt = -1
                        tgt_fail_cnt = -1
                    
                    if src_fail_cnt == 0 and tgt_fail_cnt == 0:
                        passed = True
                
                elif operation == 'case_insensitive_check':
                    if src_val is not None and tgt_val is not None and str(src_val) == str(tgt_val):
                        passed = True
                
                if passed:
                    self._prefetched_results[key] = ('true', 'true', True, '0')
                    continue
            
            filtered_row_by_row_rules.append((col_mapping, operation))
            
        row_by_row_rules = filtered_row_by_row_rules
        
        if not row_by_row_rules:
            return

        if self.source_engine.is_mocked() or self.target_engine.is_mocked():
            return

        # Gather unique columns to select
        src_cols = set()
        tgt_cols = set()
        for col_mapping, operation in row_by_row_rules:
            src_cols.add(col_mapping.source_column)
            tgt_cols.add(col_mapping.target_column)

        # Resolve source date filters
        src_date_column = self.mapping.source_date_column if self.mapping.source_date_filter_type != 'none' else None
        src_date_start = str(self.run.source_date_filter_start) if self.run.source_date_filter_start else None
        src_date_end = str(self.run.source_date_filter_end) if self.run.source_date_filter_end else None
        src_date_operator = getattr(self.mapping, 'source_date_operator', '=') if self.mapping.source_date_filter_type == 'specific' else None
        src_date_operator_start = getattr(self.mapping, 'source_date_range_operator_start', '>=') if self.mapping.source_date_filter_type == 'range' else '>='
        src_date_operator_end = getattr(self.mapping, 'source_date_range_operator_end', '<=') if self.mapping.source_date_filter_type == 'range' else '<='
        
        # Fallbacks
        if not src_date_column and self.mapping.date_filter_type != 'none':
            src_date_column = self.mapping.date_filter_column
            src_date_start = str(self.run.date_filter_start) if self.run.date_filter_start else None
            src_date_end = str(self.run.date_filter_end) if self.run.date_filter_end else None
            src_date_operator = getattr(self.mapping, 'date_operator', '=') if self.mapping.date_filter_type == 'specific' else None
            src_date_operator_start = getattr(self.mapping, 'date_range_operator_start', '>=') if self.mapping.date_filter_type == 'range' else '>='
            src_date_operator_end = getattr(self.mapping, 'date_range_operator_end', '<=') if self.mapping.date_filter_type == 'range' else '<='
            
        if src_date_column:
            if src_date_operator and not src_date_start:
                src_date_column = None
            elif not src_date_operator and not src_date_start and not src_date_end:
                src_date_column = None

        # Resolve target date filters
        tgt_date_column = self.mapping.target_date_column if self.mapping.target_date_filter_type != 'none' else None
        tgt_date_start = str(self.run.target_date_filter_start) if self.run.target_date_filter_start else None
        tgt_date_end = str(self.run.target_date_filter_end) if self.run.target_date_filter_end else None
        tgt_date_operator = getattr(self.mapping, 'target_date_operator', '=') if self.mapping.target_date_filter_type == 'specific' else None
        tgt_date_operator_start = getattr(self.mapping, 'target_date_range_operator_start', '>=') if self.mapping.target_date_filter_type == 'range' else '>='
        tgt_date_operator_end = getattr(self.mapping, 'target_date_range_operator_end', '<=') if self.mapping.target_date_filter_type == 'range' else '<='
        
        # Fallbacks
        if not tgt_date_column and self.mapping.date_filter_type != 'none':
            tgt_date_column = self.mapping.date_filter_column
            tgt_date_start = str(self.run.date_filter_start) if self.run.date_filter_start else None
            tgt_date_end = str(self.run.date_filter_end) if self.run.date_filter_end else None
            tgt_date_operator = getattr(self.mapping, 'date_operator', '=') if self.mapping.date_filter_type == 'specific' else None
            tgt_date_operator_start = getattr(self.mapping, 'date_range_operator_start', '>=') if self.mapping.date_filter_type == 'range' else '>='
            tgt_date_operator_end = getattr(self.mapping, 'date_range_operator_end', '<=') if self.mapping.date_filter_type == 'range' else '<='
            
        if tgt_date_column:
            if tgt_date_operator and not tgt_date_start:
                tgt_date_column = None
            elif not tgt_date_operator and not tgt_date_start and not tgt_date_end:
                tgt_date_column = None

        # Source query/data loading
        src_df = None
        if self.source_engine.connection.is_file:
            src_df = self.source_engine.read_file(table=self.mapping.source_table)
            if src_df is not None and src_date_column and src_date_column in src_df.columns:
                src_df = self._apply_pandas_date_filter(src_df, src_date_column, src_date_start, src_date_end, src_date_operator, src_date_operator_start, src_date_operator_end)
        else:
            quoted_src_cols = [self.source_engine._quote_identifier(c) for c in src_cols]
            if src_date_column and src_date_column not in src_cols:
                quoted_src_cols.append(self.source_engine._quote_identifier(src_date_column))
                
            src_table_full = self.source_engine._build_full_table_name(
                self.mapping.source_table, 
                schema=self.mapping.source_schema if self.mapping.source_schema and self.mapping.source_schema != 'file' else None,
                catalog=self.mapping.source_catalog if self.mapping.source_connection.connection_type == 'databricks' else None
            )
            src_query = f"SELECT {', '.join(quoted_src_cols)} FROM {src_table_full}"
            src_conditions = []
            if src_date_column:
                q_date_col = self.source_engine._quote_identifier(src_date_column)
                if src_date_operator:
                    src_conditions.append(f'{q_date_col} {src_date_operator} :date_start')
                else:
                    if src_date_start:
                        src_conditions.append(f'{q_date_col} {src_date_operator_start} :date_start')
                    if src_date_end:
                        src_conditions.append(f'{q_date_col} {src_date_operator_end} :date_end')
            if src_conditions:
                src_query += " WHERE " + " AND ".join(src_conditions)
                
            src_params = {}
            if src_date_start:
                src_params['date_start'] = src_date_start
            if not src_date_operator and src_date_end:
                src_params['date_end'] = src_date_end

        # Target query/data loading
        tgt_df = None
        if self.target_engine.connection.is_file:
            tgt_df = self.target_engine.read_file(table=self.mapping.target_table)
            if tgt_df is not None and tgt_date_column and tgt_date_column in tgt_df.columns:
                tgt_df = self._apply_pandas_date_filter(tgt_df, tgt_date_column, tgt_date_start, tgt_date_end, tgt_date_operator, tgt_date_operator_start, tgt_date_operator_end)
        else:
            quoted_tgt_cols = [self.target_engine._quote_identifier(c) for c in tgt_cols]
            if tgt_date_column and tgt_date_column not in tgt_cols:
                quoted_tgt_cols.append(self.target_engine._quote_identifier(tgt_date_column))
                
            tgt_table_full = self.target_engine._build_full_table_name(
                self.mapping.target_table, 
                schema=self.mapping.target_schema if self.mapping.target_schema and self.mapping.target_schema != 'file' else None,
                catalog=self.mapping.target_catalog if self.mapping.target_connection.connection_type == 'databricks' else None
            )
            tgt_query = f"SELECT {', '.join(quoted_tgt_cols)} FROM {tgt_table_full}"
            tgt_conditions = []
            if tgt_date_column:
                q_date_col = self.target_engine._quote_identifier(tgt_date_column)
                if tgt_date_operator:
                    tgt_conditions.append(f'{q_date_col} {tgt_date_operator} :date_start')
                else:
                    if tgt_date_start:
                        tgt_conditions.append(f'{q_date_col} {tgt_date_operator_start} :date_start')
                    if tgt_date_end:
                        tgt_conditions.append(f'{q_date_col} {tgt_date_operator_end} :date_end')
            if tgt_conditions:
                tgt_query += " WHERE " + " AND ".join(tgt_conditions)
                
            tgt_params = {}
            if tgt_date_start:
                tgt_params['date_start'] = tgt_date_start
            if not tgt_date_operator and tgt_date_end:
                tgt_params['date_end'] = tgt_date_end

        # Setup source chunk generators
        if self.source_engine.connection.is_file:
            if src_df is None or src_df.empty:
                src_chunks = iter([])
            else:
                src_chunks = (src_df.iloc[i:i+100000] for i in range(0, len(src_df), 100000))
        else:
            src_engine = self.source_engine.get_engine()
            src_chunks = self._stream_db_chunks(src_engine, src_query, src_params, chunksize=100000)

        # Setup target chunk generators
        if self.target_engine.connection.is_file:
            if tgt_df is None or tgt_df.empty:
                tgt_chunks = iter([])
            else:
                tgt_chunks = (tgt_df.iloc[i:i+100000] for i in range(0, len(tgt_df), 100000))
        else:
            tgt_engine = self.target_engine.get_engine()
            tgt_chunks = self._stream_db_chunks(tgt_engine, tgt_query, tgt_params, chunksize=100000)

        src_iter = iter(src_chunks)
        tgt_iter = iter(tgt_chunks)
        
        global_row_idx = 0
        rules_state = {}
        for col_mapping, operation in row_by_row_rules:
            rules_state[(col_mapping.id, operation)] = {
                'is_match': True,
                'difference': '0',
                'source_value': 'true',
                'target_value': 'true',
                'finished': False
            }

        import re
        rule_params = {}
        for col_mapping, operation in row_by_row_rules:
            if operation == 'contains_check':
                p = self.run.parameters.get(f"{col_mapping.source_column}:{operation}")
                if p is None:
                    p = self.run.parameters.get(f"__all__:{operation}")
                if p is None:
                    p = ' '
                rule_params[(col_mapping.id, operation)] = p
            elif operation == 'pattern_match':
                p = self.run.parameters.get(f"{col_mapping.source_column}:{operation}")
                if p is None:
                    p = self.run.parameters.get(f"__all__:{operation}")
                pat = p or r'^[a-zA-Z0-9_\-\.\s@]+$'
                rule_params[(col_mapping.id, operation)] = re.compile(pat)

        while True:
            # Check if all rules are finished (i.e. failed). If so, we break early!
            all_finished = True
            for col_mapping, operation in row_by_row_rules:
                if not rules_state[(col_mapping.id, operation)]['finished']:
                    all_finished = False
                    break
            if all_finished:
                logger.info("All row-by-row checks failed early. Stopping stream.")
                break

            try:
                src_chunk = next(src_iter)
            except StopIteration:
                src_chunk = None
                
            try:
                tgt_chunk = next(tgt_iter)
            except StopIteration:
                tgt_chunk = None
                
            if src_chunk is None and tgt_chunk is None:
                break
                
            src_len = len(src_chunk) if src_chunk is not None else 0
            tgt_len = len(tgt_chunk) if tgt_chunk is not None else 0
            
            if src_len != tgt_len:
                for col_mapping, operation in row_by_row_rules:
                    state = rules_state[(col_mapping.id, operation)]
                    if not state['finished']:
                        state['is_match'] = False
                        state['source_value'] = 'false'
                        state['target_value'] = 'false'
                        state['difference'] = f"Row count mismatch: source={global_row_idx + src_len}, target={global_row_idx + tgt_len}"
                        state['finished'] = True
                break
                
            chunk_len = src_len
            
            for col_mapping, operation in row_by_row_rules:
                state = rules_state[(col_mapping.id, operation)]
                if state['finished']:
                    continue
                    
                src_col = col_mapping.source_column
                tgt_col = col_mapping.target_column
                
                src_series = src_chunk[src_col] if src_col in src_chunk.columns else pd.Series([None] * chunk_len)
                tgt_series = tgt_chunk[tgt_col] if tgt_col in tgt_chunk.columns else pd.Series([None] * chunk_len)
                
                if operation == 'case_insensitive_check':
                    src_s = src_series.fillna('').astype(str).str.lower()
                    tgt_s = tgt_series.fillna('').astype(str).str.lower()
                    
                    src_null = src_series.isna()
                    tgt_null = tgt_series.isna()
                    
                    mismatches = (src_s != tgt_s) | (src_null != tgt_null)
                    if mismatches.any():
                        relative_idx = mismatches.values.argmax()
                        sv = src_series.iloc[relative_idx] if not src_null.iloc[relative_idx] else None
                        tv = tgt_series.iloc[relative_idx] if not tgt_null.iloc[relative_idx] else None
                        
                        state['is_match'] = False
                        state['source_value'] = 'false'
                        state['target_value'] = 'false'
                        state['difference'] = f"Mismatch at row {global_row_idx + relative_idx + 1}: source='{sv}' target='{tv}'"
                        state['finished'] = True
                            
                elif operation == 'pattern_match':
                    pat_re = rule_params[(col_mapping.id, operation)]
                    src_s = src_series.fillna('').astype(str)
                    tgt_s = tgt_series.fillna('').astype(str)
                    
                    src_ok = src_s.str.match(pat_re.pattern).fillna(False)
                    tgt_ok = tgt_s.str.match(pat_re.pattern).fillna(False)
                    
                    failures = (~src_ok) | (~tgt_ok)
                    if failures.any():
                        relative_idx = failures.values.argmax()
                        sv_str = src_series.fillna('').astype(str).iloc[relative_idx]
                        tv_str = tgt_series.fillna('').astype(str).iloc[relative_idx]
                        s_ok = src_ok.iloc[relative_idx]
                        t_ok = tgt_ok.iloc[relative_idx]
                        
                        state['is_match'] = False
                        state['source_value'] = 'false'
                        state['target_value'] = 'false'
                        state['finished'] = True
                        if not s_ok and not t_ok:
                            state['difference'] = f"Pattern mismatch at row {global_row_idx + relative_idx + 1}: both source and target failed regex check"
                        elif not s_ok:
                            state['difference'] = f"Pattern mismatch at row {global_row_idx + relative_idx + 1}: source failed regex check (value='{sv_str}')"
                        else:
                            state['difference'] = f"Pattern mismatch at row {global_row_idx + relative_idx + 1}: target failed regex check (value='{tv_str}')"
                            
                elif operation == 'contains_check':
                    param = rule_params[(col_mapping.id, operation)]
                    src_s = src_series.fillna('').astype(str)
                    tgt_s = tgt_series.fillna('').astype(str)
                    
                    src_ok = src_s.str.contains(param, regex=False)
                    tgt_ok = tgt_s.str.contains(param, regex=False)
                    
                    failures = (~src_ok) | (~tgt_ok)
                    if failures.any():
                        relative_idx = failures.values.argmax()
                        sv = src_series.iloc[relative_idx]
                        tv = tgt_series.iloc[relative_idx]
                        s_ok = src_ok.iloc[relative_idx]
                        t_ok = tgt_ok.iloc[relative_idx]
                        
                        state['is_match'] = False
                        state['source_value'] = 'true' if s_ok else 'false'
                        state['target_value'] = 'true' if t_ok else 'false'
                        state['finished'] = True
                        
                        errs = []
                        if not s_ok:
                            errs.append(f"Source: Row {global_row_idx + relative_idx + 1} failed check (value='{sv}')")
                        if not t_ok:
                            errs.append(f"Target: Row {global_row_idx + relative_idx + 1} failed check (value='{tv}')")
                        state['difference'] = "; ".join(errs)
                            
            global_row_idx += chunk_len

        # Save all states back into _prefetched_results
        for col_mapping, operation in row_by_row_rules:
            state = rules_state[(col_mapping.id, operation)]
            self._prefetched_results[(col_mapping.id, operation)] = (
                state['source_value'],
                state['target_value'],
                state['is_match'],
                state['difference']
            )

    def _stream_db_chunks(self, engine, query, params, chunksize=100000):
        """Yield DataFrame chunks from a database using server-side query streaming."""
        start_time = time.time()
        chunk_idx = 0
        try:
            with engine.connect() as conn:
                # Use yield_per to force server-side cursor chunking in SQLAlchemy
                conn = conn.execution_options(yield_per=chunksize)
                for chunk in pd.read_sql(text(query), conn, params=params, chunksize=chunksize):
                    chunk_idx += 1
                    logger.info(f"Stream chunk {chunk_idx} fetched: {len(chunk)} rows in {time.time() - start_time:.2f}s")
                    yield chunk
                    start_time = time.time()
        except Exception as e:
            logger.error(f"Error streaming db chunks: {e}")
            yield pd.DataFrame()

    def _apply_pandas_date_filter(self, df, date_column, date_start, date_end, date_operator, date_operator_start, date_operator_end):
        """Apply pandas date filter matching database WHERE logic."""
        try:
            df_date = pd.to_datetime(df[date_column])
            if date_start:
                ref_start = pd.to_datetime(date_start)
            if date_end:
                ref_end = pd.to_datetime(date_end)
                
            if date_operator:
                if date_operator == '=':
                    return df[df_date == ref_start]
                elif date_operator == '>':
                    return df[df_date > ref_start]
                elif date_operator == '<':
                    return df[df_date < ref_start]
                elif date_operator == '>=':
                    return df[df_date >= ref_start]
                elif date_operator == '<=':
                    return df[df_date <= ref_start]
            else:
                if date_start:
                    if date_operator_start == '>':
                        df = df[df_date > ref_start]
                    elif date_operator_start == '<':
                        df = df[df_date < ref_start]
                    elif date_operator_start == '>=':
                        df = df[df_date >= ref_start]
                    elif date_operator_start == '<=':
                        df = df[df_date <= ref_start]
                if date_end:
                    if date_operator_end == '>':
                        df = df[df_date > ref_end]
                    elif date_operator_end == '<':
                        df = df[df_date < ref_end]
                    elif date_operator_end == '>=':
                        df = df[df_date >= ref_end]
                    elif date_operator_end == '<=':
                        df = df[df_date <= ref_end]
            return df
        except Exception as e:
            logger.error(f"Error filtering pandas date: {e}")
            return df
