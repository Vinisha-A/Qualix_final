import json
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse

from .models import Mapping, ColumnMapping, ValidationRule
from connections.models import DataConnection
from accounts.decorators import contributor_or_admin_required

logger = logging.getLogger(__name__)


@login_required
def mapping_list_view(request):
    """List all mappings."""
    mappings = Mapping.objects.filter(is_active=True).select_related(
        'source_connection', 'target_connection', 'created_by'
    )
    return render(request, 'mappings/list.html', {'mappings': mappings})


def get_datatype_category(type_str, name_str=''):
    t = str(type_str or '').upper()
    n = str(name_str or '').upper()
    if any(x in t for x in ('INT', 'BIGINT', 'SMALLINT', 'TINYINT', 'NUMERIC', 'DECIMAL', 'FLOAT', 'DOUBLE', 'REAL', 'NUMBER')):
        return 'INTEGER'
    elif (any(x in t for x in ('DATE', 'TIME', 'TIMESTAMP')) or 
          any(x in n for x in ('DATE', 'TIME', 'TIMESTAMP')) or 
          n.endswith('_AT') or n.endswith('_ON') or n == 'AT' or 'DT' in n):
        return 'DATE'
    elif any(x in t for x in ('BOOL', 'BOOLEAN')):
        return 'BOOLEAN'
    else:
        return 'VARCHAR'

def get_applicable_operations(category):
    if category == 'INTEGER':
        return ['null_check', 'sum', 'avg', 'min', 'max', 'range_check', 'duplicate_check', 'count', 'row_count', 'unique_check', 'distinct_count', 'data_type_check', 'hash_validation']
    elif category == 'DATE':
        return ['min_date', 'max_date', 'null_check', 'duplicate_check', 'count', 'row_count', 'unique_check', 'distinct_count', 'hash_validation']
    elif category == 'BOOLEAN':
        return ['null_check', 'count', 'row_count', 'duplicate_check', 'unique_check', 'distinct_count', 'hash_validation']
    else: # VARCHAR
        return ['null_check', 'length_sum_check', 'sum_length', 'regex_check', 'duplicate_check', 'unique_check', 'distinct_count', 'row_count', 'count', 'case_insensitive_check', 'trim_check', 'contains_check', 'pattern_match', 'data_type_check', 'hash_validation']

@login_required
@contributor_or_admin_required
def mapping_create_view(request):
    """Create a new source-target mapping."""
    connections = DataConnection.objects.filter(is_active=True)
    operations = ValidationRule.OPERATION_CHOICES

    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Mapping name is required and cannot be empty.')
                return render(request, 'mappings/create.html', {
                    'connections': connections,
                    'operations': operations,
                })

            description = request.POST.get('description', '')
            source_conn_id = request.POST.get('source_connection')
            source_schema = request.POST.get('source_schema', '')
            source_table = request.POST.get('source_table', '')
            target_conn_id = request.POST.get('target_connection')
            target_schema = request.POST.get('target_schema', '')
            target_table = request.POST.get('target_table', '')
            is_draft = request.POST.get('is_draft', 'false') == 'true'

            # Resolving Source Date Filters
            source_date_column = request.POST.get('source_date_column', '')
            source_date_filter_type = request.POST.get('source_date_filter_type', 'none')
            if not source_date_column:
                source_date_filter_type = 'none'
            source_date_filter_start = None
            source_date_filter_end = None
            source_date_value_type = request.POST.get('source_date_value_type', 'calendar')
            source_date_relative_operator = request.POST.get('source_date_relative_operator', '+')
            try:
                source_date_relative_value = int(request.POST.get('source_date_relative_value', 0) or 0)
            except (ValueError, TypeError):
                source_date_relative_value = 0
            source_date_operator = request.POST.get('source_date_operator', '=')

            if source_date_filter_type == 'specific':
                if source_date_value_type == 'calendar':
                    source_date_single = request.POST.get('source_date_single')
                    if source_date_single:
                        source_date_filter_start = source_date_single
                        source_date_filter_end = source_date_single
            elif source_date_filter_type == 'range':
                source_date_filter_start = request.POST.get('source_date_filter_start') or None
                source_date_filter_end = request.POST.get('source_date_filter_end') or None

            # Resolving Target Date Filters
            target_date_column = request.POST.get('target_date_column', '')
            target_date_filter_type = request.POST.get('target_date_filter_type', 'none')
            if not target_date_column:
                target_date_filter_type = 'none'
            target_date_filter_start = None
            target_date_filter_end = None
            target_date_value_type = request.POST.get('target_date_value_type', 'calendar')
            target_date_relative_operator = request.POST.get('target_date_relative_operator', '+')
            try:
                target_date_relative_value = int(request.POST.get('target_date_relative_value', 0) or 0)
            except (ValueError, TypeError):
                target_date_relative_value = 0
            target_date_operator = request.POST.get('target_date_operator', '=')

            if target_date_filter_type == 'specific':
                if target_date_value_type == 'calendar':
                    target_date_single = request.POST.get('target_date_single')
                    if target_date_single:
                        target_date_filter_start = target_date_single
                        target_date_filter_end = target_date_single
            elif target_date_filter_type == 'range':
                target_date_filter_start = request.POST.get('target_date_filter_start') or None
                target_date_filter_end = request.POST.get('target_date_filter_end') or None

            # Prepare mapping creation data
            mapping_data = {
                'name': name,
                'description': description,
                'source_connection_id': source_conn_id,
                'source_schema': source_schema,
                'source_table': source_table,
                'target_connection_id': target_conn_id,
                'target_schema': target_schema,
                'target_table': target_table,
                'created_by': request.user,
                'is_draft': is_draft,
                'source_date_column': source_date_column,
                'source_date_filter_type': source_date_filter_type,
                'source_date_filter_start': source_date_filter_start,
                'source_date_filter_end': source_date_filter_end,
                'source_date_operator': source_date_operator,
                'source_date_range_operator_start': request.POST.get('source_date_range_operator_start', '>='),
                'source_date_range_operator_end': request.POST.get('source_date_range_operator_end', '<='),
                'target_date_column': target_date_column,
                'target_date_filter_type': target_date_filter_type,
                'target_date_filter_start': target_date_filter_start,
                'target_date_filter_end': target_date_filter_end,
                'target_date_operator': target_date_operator,
                'target_date_range_operator_start': request.POST.get('target_date_range_operator_start', '>='),
                'target_date_range_operator_end': request.POST.get('target_date_range_operator_end', '<='),
                # Stale fields that might be submitted from client or UI
                'source_date_value_type': source_date_value_type,
                'source_date_relative_operator': source_date_relative_operator,
                'source_date_relative_value': source_date_relative_value,
                'target_date_value_type': target_date_value_type,
                'target_date_relative_operator': target_date_relative_operator,
                'target_date_relative_value': target_date_relative_value,
            }

            # Dynamic fields verification and defensive logging
            model_fields = set()
            for f in Mapping._meta.get_fields():
                model_fields.add(f.name)
                if hasattr(f, 'attname'):
                    model_fields.add(f.attname)

            rejected_fields = {}
            for key in list(mapping_data.keys()):
                if key not in model_fields:
                    rejected_fields[key] = mapping_data[key]
                    logger.warning(
                        f"Field mismatch: Submitted field '{key}' is not a valid attribute of the Mapping model. "
                        f"Removing from parameters list. "
                        f"Model fields: {sorted(list(model_fields))}"
                    )
                    del mapping_data[key]

            try:
                mapping = Mapping.objects.create(**mapping_data)
            except Exception as e:
                logger.error(
                    f"Error creating mapping. "
                    f"Submitted data keys: {list(mapping_data.keys())}. "
                    f"Rejected data: {rejected_fields}. "
                    f"Model fields: {sorted(list(model_fields))}. "
                    f"Failure reason: {e}"
                )
                raise

            try:
                from dashboard.models import FormDraft
                FormDraft.objects.filter(user=request.user, page_key='mapping_create', status='draft').update(status='completed')
            except Exception:
                pass

            # Process column mappings
            column_data = request.POST.get('column_mappings_json', '[]')
            try:
                columns = json.loads(column_data)
            except json.JSONDecodeError:
                columns = []

            # If "__all__" is passed in columns mapping
            if columns and columns[0].get('source_column') == '__all__':
                user_selected_ops = columns[0].get('operations', [])
                from connections.connector import ConnectorEngine
                source_conn = DataConnection.objects.get(id=source_conn_id)
                target_conn = DataConnection.objects.get(id=target_conn_id)
                source_engine = ConnectorEngine(source_conn)
                target_engine = ConnectorEngine(target_conn)
                
                src_all_cols = source_engine.get_columns(source_schema if source_schema != 'file' else None, source_table)
                tgt_all_cols = target_engine.get_columns(target_schema if target_schema != 'file' else None, target_table)
                
                expanded_columns = []
                for s_col in src_all_cols:
                    matched_t = next((t_col for t_col in tgt_all_cols if t_col['name'].lower() == s_col['name'].lower()), None)
                    if matched_t:
                        s_cat = get_datatype_category(s_col['type'], s_col['name'])
                        all_ops = get_applicable_operations(s_cat)
                        if user_selected_ops:
                            ops = [op for op in all_ops if op in user_selected_ops]
                        else:
                            ops = all_ops
                        expanded_columns.append({
                            'source_column': s_col['name'],
                            'source_datatype': s_col['type'],
                            'target_column': matched_t['name'],
                            'target_datatype': matched_t['type'],
                            'operations': ops
                        })
                columns = expanded_columns
            else:
                # Resolve datatypes for manually selected columns if unknown
                from connections.connector import ConnectorEngine
                source_cols_map = {}
                target_cols_map = {}
                try:
                    source_conn = DataConnection.objects.get(id=source_conn_id)
                    source_engine = ConnectorEngine(source_conn)
                    source_cols_map = {c['name'].lower(): c['type'] for c in source_engine.get_columns(source_schema if source_schema != 'file' else None, source_table)}
                except Exception:
                    pass

                try:
                    target_conn = DataConnection.objects.get(id=target_conn_id)
                    target_engine = ConnectorEngine(target_conn)
                    target_cols_map = {c['name'].lower(): c['type'] for c in target_engine.get_columns(target_schema if target_schema != 'file' else None, target_table)}
                except Exception:
                    pass

                for col in columns:
                    s_col = col.get('source_column', '')
                    t_col = col.get('target_column', '')
                    s_type = col.get('source_datatype', '')
                    t_type = col.get('target_datatype', '')
                    
                    if s_type == 'unknown' or not s_type:
                        col['source_datatype'] = source_cols_map.get(s_col.lower(), 'unknown')
                    if t_type == 'unknown' or not t_type:
                        col['target_datatype'] = target_cols_map.get(t_col.lower(), 'unknown')

            for col in columns:
                s_col = col.get('source_column', '')
                t_col = col.get('target_column', '')
                s_type = col.get('source_datatype', 'unknown')
                t_type = col.get('target_datatype', 'unknown')

                col_mapping = ColumnMapping.objects.create(
                    mapping=mapping,
                    source_column=s_col,
                    source_datatype=s_type,
                    target_column=t_col,
                    target_datatype=t_type,
                )

                # Add validation rules
                selected_ops = col.get('operations', [])
                for op in selected_ops:
                    ValidationRule.objects.create(
                        column_mapping=col_mapping,
                        operation=op,
                    )

            try:
                from logs.models import AuditLog
                AuditLog.objects.create(
                    user=request.user,
                    action=f'Created Mapping: {name}',
                    entity_type='Mapping',
                    entity_id=mapping.id,
                    details={
                        'source': f'{source_schema}.{source_table}',
                        'target': f'{target_schema}.{target_table}',
                        'columns': len(columns),
                    },
                    ip_address=request.META.get('REMOTE_ADDR'),
                    level='info',
                )
            except Exception:
                pass

            messages.success(request, f'Mapping "{name}" created successfully.')
            return redirect('mappings:detail', mapping_id=mapping.id)

        except Exception as e:
            logger.error(f"Error creating mapping: {e}")
            messages.error(request, f'Error creating mapping: {str(e)}')

    return render(request, 'mappings/create.html', {
        'connections': connections,
        'operations': operations,
    })


@login_required
def mapping_detail_view(request, mapping_id):
    """View mapping details."""
    mapping = get_object_or_404(
        Mapping.objects.select_related('source_connection', 'target_connection', 'created_by'),
        id=mapping_id
    )
    column_mappings = mapping.column_mappings.prefetch_related('rules').all()
    return render(request, 'mappings/detail.html', {
        'mapping': mapping,
        'column_mappings': column_mappings,
    })


@login_required
@contributor_or_admin_required
def mapping_delete_view(request, mapping_id):
    """Delete a mapping."""
    mapping = get_object_or_404(Mapping, id=mapping_id)
    if request.method == 'POST':
        mapping.is_active = False
        mapping.save()
        messages.success(request, f'Mapping "{mapping.name}" deleted.')
    return redirect('mappings:list')


@login_required
def api_mapping_columns(request, mapping_id):
    """AJAX endpoint: get columns in a mapping."""
    mapping = get_object_or_404(Mapping, id=mapping_id)
    columns = [cm.source_column for cm in mapping.column_mappings.all()]
    return JsonResponse({'columns': columns})


def format_date_val(val):
    if not val:
        return ''
    if isinstance(val, str):
        return val
    try:
        return val.isoformat()
    except AttributeError:
        return str(val)


@login_required
@contributor_or_admin_required
def mapping_edit_view(request, mapping_id):
    """Edit an existing mapping."""
    mapping = get_object_or_404(Mapping, id=mapping_id, is_active=True)
    connections = DataConnection.objects.filter(is_active=True)
    operations = ValidationRule.OPERATION_CHOICES

    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Mapping name is required and cannot be empty.')
                return render(request, 'mappings/edit.html', {
                    'mapping': mapping,
                    'connections': connections,
                    'operations': operations,
                })

            description = request.POST.get('description', '')
            source_conn_id = request.POST.get('source_connection')
            source_schema = request.POST.get('source_schema', '')
            source_table = request.POST.get('source_table', '')
            target_conn_id = request.POST.get('target_connection')
            target_schema = request.POST.get('target_schema', '')
            target_table = request.POST.get('target_table', '')
            is_draft = request.POST.get('is_draft', 'false') == 'true'

            # Resolving Source Date Filters
            source_date_column = request.POST.get('source_date_column', '')
            source_date_filter_type = request.POST.get('source_date_filter_type', 'none')
            if not source_date_column:
                source_date_filter_type = 'none'
            source_date_filter_start = None
            source_date_filter_end = None
            source_date_operator = request.POST.get('source_date_operator', '=')

            if source_date_filter_type == 'specific':
                source_date_single = request.POST.get('source_date_single')
                if source_date_single:
                    source_date_filter_start = source_date_single
                    source_date_filter_end = source_date_single
            elif source_date_filter_type == 'range':
                source_date_filter_start = request.POST.get('source_date_filter_start') or None
                source_date_filter_end = request.POST.get('source_date_filter_end') or None

            # Resolving Target Date Filters
            target_date_column = request.POST.get('target_date_column', '')
            target_date_filter_type = request.POST.get('target_date_filter_type', 'none')
            if not target_date_column:
                target_date_filter_type = 'none'
            target_date_filter_start = None
            target_date_filter_end = None
            target_date_operator = request.POST.get('target_date_operator', '=')

            if target_date_filter_type == 'specific':
                target_date_single = request.POST.get('target_date_single')
                if target_date_single:
                    target_date_filter_start = target_date_single
                    target_date_filter_end = target_date_single
            elif target_date_filter_type == 'range':
                target_date_filter_start = request.POST.get('target_date_filter_start') or None
                target_date_filter_end = request.POST.get('target_date_filter_end') or None

            # Prepare mapping update data
            mapping_data = {
                'name': name,
                'description': description,
                'source_connection_id': source_conn_id,
                'source_schema': source_schema,
                'source_table': source_table,
                'target_connection_id': target_conn_id,
                'target_schema': target_schema,
                'target_table': target_table,
                'is_draft': is_draft,
                'source_date_column': source_date_column,
                'source_date_filter_type': source_date_filter_type,
                'source_date_filter_start': source_date_filter_start,
                'source_date_filter_end': source_date_filter_end,
                'source_date_operator': source_date_operator,
                'source_date_range_operator_start': request.POST.get('source_date_range_operator_start', '>='),
                'source_date_range_operator_end': request.POST.get('source_date_range_operator_end', '<='),
                'target_date_column': target_date_column,
                'target_date_filter_type': target_date_filter_type,
                'target_date_filter_start': target_date_filter_start,
                'target_date_filter_end': target_date_filter_end,
                'target_date_operator': target_date_operator,
                'target_date_range_operator_start': request.POST.get('target_date_range_operator_start', '>='),
                'target_date_range_operator_end': request.POST.get('target_date_range_operator_end', '<='),
            }

            # Update mapping dynamically
            model_fields = set()
            for f in Mapping._meta.get_fields():
                model_fields.add(f.name)
                if hasattr(f, 'attname'):
                    model_fields.add(f.attname)

            rejected_fields = {}
            for key in list(mapping_data.keys()):
                if key not in model_fields:
                    rejected_fields[key] = mapping_data[key]
                    logger.warning(
                        f"Field mismatch: Submitted field '{key}' is not a valid attribute of the Mapping model. "
                        f"Removing from parameters list. "
                        f"Model fields: {sorted(list(model_fields))}"
                    )
                    del mapping_data[key]

            # Update fields on instance
            for key, val in mapping_data.items():
                setattr(mapping, key, val)

            try:
                mapping.save()
            except Exception as e:
                logger.error(
                    f"Error saving edited mapping. "
                    f"Submitted data keys: {list(mapping_data.keys())}. "
                    f"Rejected data: {rejected_fields}. "
                    f"Model fields: {sorted(list(model_fields))}. "
                    f"Failure reason: {e}"
                )
                raise

            # Delete existing column mappings and rules
            mapping.column_mappings.all().delete()

            # Process column mappings
            column_data = request.POST.get('column_mappings_json', '[]')
            try:
                columns = json.loads(column_data)
            except json.JSONDecodeError:
                columns = []

            # If "__all__" is passed in columns mapping
            if columns and columns[0].get('source_column') == '__all__':
                user_selected_ops = columns[0].get('operations', [])
                from connections.connector import ConnectorEngine
                source_conn = DataConnection.objects.get(id=source_conn_id)
                target_conn = DataConnection.objects.get(id=target_conn_id)
                source_engine = ConnectorEngine(source_conn)
                target_engine = ConnectorEngine(target_conn)
                
                src_all_cols = source_engine.get_columns(source_schema if source_schema != 'file' else None, source_table)
                tgt_all_cols = target_engine.get_columns(target_schema if target_schema != 'file' else None, target_table)
                
                expanded_columns = []
                for s_col in src_all_cols:
                    matched_t = next((t_col for t_col in tgt_all_cols if t_col['name'].lower() == s_col['name'].lower()), None)
                    if matched_t:
                        s_cat = get_datatype_category(s_col['type'], s_col['name'])
                        all_ops = get_applicable_operations(s_cat)
                        if user_selected_ops:
                            ops = [op for op in all_ops if op in user_selected_ops]
                        else:
                            ops = all_ops
                        expanded_columns.append({
                            'source_column': s_col['name'],
                            'source_datatype': s_col['type'],
                            'target_column': matched_t['name'],
                            'target_datatype': matched_t['type'],
                            'operations': ops
                        })
                columns = expanded_columns
            else:
                # Resolve datatypes for manually selected columns if unknown
                from connections.connector import ConnectorEngine
                source_cols_map = {}
                target_cols_map = {}
                try:
                    source_conn = DataConnection.objects.get(id=source_conn_id)
                    source_engine = ConnectorEngine(source_conn)
                    source_cols_map = {c['name'].lower(): c['type'] for c in source_engine.get_columns(source_schema if source_schema != 'file' else None, source_table)}
                except Exception:
                    pass

                try:
                    target_conn = DataConnection.objects.get(id=target_conn_id)
                    target_engine = ConnectorEngine(target_conn)
                    target_cols_map = {c['name'].lower(): c['type'] for c in target_engine.get_columns(target_schema if target_schema != 'file' else None, target_table)}
                except Exception:
                    pass

                for col in columns:
                    s_col = col.get('source_column', '')
                    t_col = col.get('target_column', '')
                    s_type = col.get('source_datatype', '')
                    t_type = col.get('target_datatype', '')
                    
                    if s_type == 'unknown' or not s_type:
                        col['source_datatype'] = source_cols_map.get(s_col.lower(), 'unknown')
                    if t_type == 'unknown' or not t_type:
                        col['target_datatype'] = target_cols_map.get(t_col.lower(), 'unknown')

            for col in columns:
                s_col = col.get('source_column', '')
                t_col = col.get('target_column', '')
                s_type = col.get('source_datatype', 'unknown')
                t_type = col.get('target_datatype', 'unknown')

                col_mapping = ColumnMapping.objects.create(
                    mapping=mapping,
                    source_column=s_col,
                    source_datatype=s_type,
                    target_column=t_col,
                    target_datatype=t_type,
                )

                # Add validation rules
                selected_ops = col.get('operations', [])
                for op in selected_ops:
                    ValidationRule.objects.create(
                        column_mapping=col_mapping,
                        operation=op,
                    )

            try:
                from logs.models import AuditLog
                AuditLog.objects.create(
                    user=request.user,
                    action=f'Edited Mapping: {name}',
                    entity_type='Mapping',
                    entity_id=mapping.id,
                    details={
                        'source': f'{source_schema}.{source_table}',
                        'target': f'{target_schema}.{target_table}',
                        'columns': len(columns),
                    },
                    ip_address=request.META.get('REMOTE_ADDR'),
                    level='info',
                )
            except Exception:
                pass

            messages.success(request, f'Mapping "{name}" updated successfully.')
            return redirect('mappings:detail', mapping_id=mapping.id)

        except Exception as e:
            logger.error(f"Error editing mapping: {e}")
            messages.error(request, f'Error editing mapping: {str(e)}')

    # GET request: Prepare JSON config of current mapping
    from django.core.serializers.json import DjangoJSONEncoder
    col_mappings_list = []
    for cm in mapping.column_mappings.all():
        col_mappings_list.append({
            'source_column': cm.source_column,
            'source_datatype': cm.source_datatype,
            'target_column': cm.target_column,
            'target_datatype': cm.target_datatype,
            'operations': [r.operation for r in cm.rules.all()]
        })

    # Infer selection mode
    mode = 'single'
    if col_mappings_list and col_mappings_list[0]['source_column'] == '__all__':
        mode = 'all'
    elif len(col_mappings_list) > 1:
        mode = 'multiple'

    source_columns = []
    target_columns = []
    single_ops = []
    if mode == 'single' and col_mappings_list:
        first_map = col_mappings_list[0]
        source_columns = [first_map['source_column']]
        target_columns = [first_map['target_column']]
        single_ops = first_map['operations']
    elif mode == 'multiple' and col_mappings_list:
        source_columns = [m['source_column'] for m in col_mappings_list]
        target_columns = [m['target_column'] for m in col_mappings_list]

    draft_data = {
        'name': mapping.name,
        'description': mapping.description,
        'source_connection': mapping.source_connection_id,
        'source_schema': mapping.source_schema,
        'source_table': mapping.source_table,
        'target_connection': mapping.target_connection_id,
        'target_schema': mapping.target_schema,
        'target_table': mapping.target_table,
        'column_selection_mode': mode,
        'column_mappings_json': json.dumps(col_mappings_list),
        'source_columns': source_columns,
        'target_columns': target_columns,
        'operations': single_ops,
        'source_single_operations': single_ops,
        'target_single_operations': single_ops,
        'source_date_single': format_date_val(mapping.source_date_filter_start) if (mapping.source_date_filter_type == 'specific' and mapping.source_date_filter_start) else '',
        'source_date_filter_start': format_date_val(mapping.source_date_filter_start) if mapping.source_date_filter_start else '',
        'source_date_filter_end': format_date_val(mapping.source_date_filter_end) if mapping.source_date_filter_end else '',
        'source_date_operator': mapping.source_date_operator,
        'source_date_range_operator_start': mapping.source_date_range_operator_start,
        'source_date_range_operator_end': mapping.source_date_range_operator_end,
        'target_date_column': mapping.target_date_column,
        'target_date_filter_type': mapping.target_date_filter_type,
        'target_date_single': format_date_val(mapping.target_date_filter_start) if (mapping.target_date_filter_type == 'specific' and mapping.target_date_filter_start) else '',
        'target_date_filter_start': format_date_val(mapping.target_date_filter_start) if mapping.target_date_filter_start else '',
        'target_date_filter_end': format_date_val(mapping.target_date_filter_end) if mapping.target_date_filter_end else '',
        'target_date_operator': mapping.target_date_operator,
        'target_date_range_operator_start': mapping.target_date_range_operator_start,
        'target_date_range_operator_end': mapping.target_date_range_operator_end,
    }
    mapping_json = json.dumps(draft_data, cls=DjangoJSONEncoder)

    return render(request, 'mappings/edit.html', {
        'mapping': mapping,
        'mapping_json': mapping_json,
        'connections': connections,
        'operations': operations,
    })
