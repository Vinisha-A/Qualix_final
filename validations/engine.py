"""
Validation Engine — Core logic for running data quality checks.
Compares source vs target using the mapped columns and operations.
"""
import logging
from django.utils import timezone
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
            column_mappings = self.mapping.column_mappings.prefetch_related('rules').all()
            if self.run.selected_columns:
                selected = [c.strip().lower() for c in self.run.selected_columns.split(',') if c.strip()]
                column_mappings = [cm for cm in column_mappings if cm.source_column.lower() in selected]

            total_checks = sum(cm.rules.filter(is_active=True).count() for cm in column_mappings)
            self.run.total_checks = total_checks
            self.run.save()

            completed = 0
            passed = 0
            failed = 0

            for col_mapping in column_mappings:
                rules = col_mapping.rules.filter(is_active=True)

                for rule in rules:
                    try:
                        result = self._run_check(col_mapping, rule.operation)
                        if result.is_match:
                            passed += 1
                        else:
                            failed += 1
                    except Exception as e:
                        # Create a failed result
                        ValidationResult.objects.create(
                            run=self.run,
                            column_mapping=col_mapping,
                            operation=rule.operation,
                            source_value='ERROR',
                            target_value='ERROR',
                            is_match=False,
                            difference=str(e),
                            details={'error': str(e)},
                        )
                        failed += 1
                        logger.error(f"Check failed for {col_mapping}.{rule.operation}: {e}")

                    completed += 1
                    self.run.progress = int((completed / max(total_checks, 1)) * 100)
                    self.run.passed_checks = passed
                    self.run.failed_checks = failed
                    self.run.save()

            self.run.status = 'completed'
            self.run.completed_at = timezone.now()
            self.run.progress = 100
            self.run.passed_checks = passed
            self.run.failed_checks = failed
            self.run.save()

            logger.info(f"Validation run {self.run.id} completed: {passed} passed, {failed} failed out of {total_checks}")

        except Exception as e:
            self.run.status = 'failed'
            self.run.error_message = str(e)
            self.run.completed_at = timezone.now()
            self.run.save()
            logger.error(f"Validation run {self.run.id} failed: {e}")
            raise

    def _run_check(self, col_mapping, operation):
        """Run a single validation check and save the result."""
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

            source_vals = self.source_engine.get_column_values(
                self.mapping.source_schema,
                self.mapping.source_table,
                col_mapping.source_column,
                src_date_column,
                src_date_start,
                src_date_end,
                src_date_operator,
            )
            target_vals = self.target_engine.get_column_values(
                self.mapping.target_schema,
                self.mapping.target_table,
                col_mapping.target_column,
                tgt_date_column,
                tgt_date_start,
                tgt_date_end,
                tgt_date_operator,
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

        result = ValidationResult.objects.create(
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

        if operation == 'duplicate_check':
            return engine.check_duplicates(schema, table, column, date_column, date_start, date_end, date_operator, date_operator_start, date_operator_end)

        return engine.get_aggregation(
            schema=schema,
            table=table,
            column=column,
            operation=operation,
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
