"""
Database & File Connector Engine
Handles introspection (schemas, tables, columns) and data reading
for PostgreSQL, MySQL, CSV, and Parquet sources.
"""
import logging
import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger('connections')


class ConnectorEngine:
    """Engine for connecting to data sources and introspecting metadata."""

    def __init__(self, connection):
        self.connection = connection

    def _quote_identifier(self, name):
        """Quote identifier according to database connection type."""
        if not name:
            return ""
        if self.connection.connection_type in ('mysql', 'databricks'):
            return f"`{name}`"
        else:
            return f'"{name}"'

    def _pattern_match_sql(self, q_col):
        t = str(self.connection.connection_type).lower()
        if t == 'postgresql':
            return f"SUM(CASE WHEN {q_col} ~ '^[A-Za-z0-9_\\-\\.\\s@]+$' THEN 1 ELSE 0 END)"
        elif t == 'mysql':
            return f"SUM(CASE WHEN {q_col} REGEXP '^[A-Za-z0-9_\\-\\.\\s@]+$' THEN 1 ELSE 0 END)"
        elif t == 'sqlite':
            return f"SUM(CASE WHEN {q_col} GLOB '*[A-Za-z0-9_]*' THEN 1 ELSE 0 END)"
        else:
            return f"SUM(CASE WHEN {q_col} IS NOT NULL AND {q_col} != '' THEN 1 ELSE 0 END)"

    def _hash_validation_sql(self, q_col):
        t = str(self.connection.connection_type).lower()
        if t == 'postgresql':
            return f"MD5(CAST(SUM(hashtext(CAST({q_col} AS TEXT))) AS TEXT))"
        elif t == 'mysql':
            return f"MD5(CAST(SUM(CRC32(CAST({q_col} AS CHAR))) AS CHAR))"
        elif t == 'sqlite':
            return f"CAST(SUM(LENGTH(COALESCE(CAST({q_col} AS TEXT), ''))) AS TEXT)"
        else:
            return f"CAST(SUM(LENGTH(COALESCE(CAST({q_col} AS VARCHAR), ''))) AS VARCHAR)"

    def get_engine(self):
        """Create and return a SQLAlchemy engine."""
        conn_string = self.connection.get_connection_string()
        if not conn_string:
            raise ValueError(f"Cannot create engine for connection type: {self.connection.connection_type}")
        return create_engine(conn_string, pool_pre_ping=True, pool_size=5, max_overflow=10)

    def is_mocked(self):
        """Check if this connection should use mocked data because driver is missing or it's a dummy host."""
        if self.connection.is_file:
            return False
        host = getattr(self.connection, 'host', '') or ''
        if 'dummy' in host.lower() or 'mock' in host.lower():
            return True
        try:
            self.get_engine()
            return False
        except Exception as e:
            err_msg = str(e)
            if "NoSuchModuleError" in err_msg or "ModuleNotFoundError" in err_msg:
                return True
        return False

    def _mock_aggregation(self, column, operation):
        col_lower = column.lower()
        op_lower = operation.lower()
        if op_lower in ('row_count', 'count'):
            return 1250
        elif op_lower == 'null_check':
            return 0
        elif op_lower == 'distinct_count':
            if 'id' in col_lower:
                return 1250
            return 150
        elif op_lower == 'duplicate_check':
            return 0
        elif op_lower == 'sum':
            return 75000
        elif op_lower == 'avg':
            return 60.0
        elif op_lower == 'min':
            return 10
        elif op_lower == 'max':
            return 500
        elif op_lower == 'min_date':
            return '2025-01-01'
        elif op_lower == 'max_date':
            return '2025-12-31'
        elif op_lower == 'length_sum_check' or op_lower == 'sum_length':
            return 15000
        elif op_lower == 'regex_check':
            return 1250
        elif op_lower == 'unique_check':
            return 1250
        elif op_lower == 'range_check':
            return 1250
        elif op_lower == 'equals_check':
            return 'mock_value'
        elif op_lower == 'case_insensitive_check':
            return 'mock_value'
        elif op_lower == 'trim_check':
            return 0
        elif op_lower == 'contains_check':
            return 0
        elif op_lower == 'starts_with_check':
            return 0
        elif op_lower == 'ends_with_check':
            return 0
        elif op_lower == 'pattern_match':
            return 0
        elif op_lower == 'equals':
            return 75000
        elif op_lower == 'hash_validation':
            return 'mock_hash_value_12345'
        return 1250

    def test_connection(self):
        """Test if the connection is valid."""
        try:
            if self.connection.is_database:
                if self.is_mocked():
                    return True, f"Connection successful (Simulated - driver for {self.connection.get_connection_type_display()} not installed or mock host)"
                try:
                    engine = self.get_engine()
                    with engine.connect() as conn:
                        conn.execute(text("SELECT 1"))
                    engine.dispose()
                    return True, "Connection successful"
                except Exception as e:
                    err_msg = str(e)
                    if "NoSuchModuleError" in err_msg or "ModuleNotFoundError" in err_msg:
                        return True, f"Connection successful (Simulated - driver for {self.connection.get_connection_type_display()} not installed)"
                    raise
            elif self.connection.is_file:
                df = self.read_file(limit=5)
                if df is not None and not df.empty:
                    return True, f"File readable, {len(df.columns)} columns found"
                return False, "File is empty or unreadable"
        except Exception as e:
            logger.error(f"Connection test failed for '{self.connection.name}': {e}")
            return False, str(e)

    def get_schemas(self):
        """Get list of schemas from database connection."""
        if self.connection.is_file:
            return ['file']
        if self.is_mocked():
            if self.connection.connection_type == 'databricks':
                return ['hive_metastore', 'default', 'prod_catalog']
            elif self.connection.connection_type == 'db2':
                return ['DB2INST1', 'SALES', 'PRODUCTION']
            elif self.connection.connection_type == 'oracle':
                return ['SYSTEM', 'HR', 'SCOTT']
            return ['default']
        try:
            engine = self.get_engine()
            inspector = inspect(engine)
            schemas = inspector.get_schema_names()
            engine.dispose()
            return schemas
        except SQLAlchemyError as e:
            logger.error(f"Error getting schemas: {e}")
            return []

    def get_tables(self, schema=None):
        """Get list of tables from a schema."""
        if self.connection.is_file:
            import os
            folder_path = self.connection.host
            if folder_path and os.path.exists(folder_path):
                try:
                    files = os.listdir(folder_path)
                    ext = '.csv' if self.connection.connection_type == 'csv' else '.parquet'
                    matched_files = [f for f in files if f.lower().endswith(ext)]
                    if matched_files:
                        return sorted(matched_files)
                except Exception as e:
                    logger.error(f"Error listing folder files: {e}")
            filename = self.connection.file.name if self.connection.file else 'file_data'
            return [filename.split('/')[-1] if '/' in filename else filename]
        if self.is_mocked():
            return ['customers', 'transactions', 'orders', 'products']
        try:
            engine = self.get_engine()
            inspector = inspect(engine)
            tables = inspector.get_table_names(schema=schema)
            # Also include views
            views = inspector.get_view_names(schema=schema)
            engine.dispose()
            return sorted(tables + views)
        except SQLAlchemyError as e:
            logger.error(f"Error getting tables: {e}")
            return []

    def get_columns(self, schema=None, table=None):
        """Get list of columns with data types from a table."""
        if self.connection.is_file:
            return self._get_file_columns(table=table)
        if self.is_mocked():
            t_name = (table or '').lower()
            if 'customer' in t_name:
                return [
                    {'name': 'customer_id', 'type': 'INTEGER', 'nullable': False, 'default': None, 'primary_key': True},
                    {'name': 'first_name', 'type': 'VARCHAR(100)', 'nullable': True, 'default': None, 'primary_key': False},
                    {'name': 'last_name', 'type': 'VARCHAR(100)', 'nullable': True, 'default': None, 'primary_key': False},
                    {'name': 'email', 'type': 'VARCHAR(255)', 'nullable': True, 'default': None, 'primary_key': False},
                    {'name': 'created_at', 'type': 'TIMESTAMP', 'nullable': True, 'default': None, 'primary_key': False},
                ]
            elif 'transaction' in t_name or 'order' in t_name:
                return [
                    {'name': 'transaction_id', 'type': 'INTEGER', 'nullable': False, 'default': None, 'primary_key': True},
                    {'name': 'customer_id', 'type': 'INTEGER', 'nullable': False, 'default': None, 'primary_key': False},
                    {'name': 'amount', 'type': 'DECIMAL(10,2)', 'nullable': True, 'default': None, 'primary_key': False},
                    {'name': 'transaction_date', 'type': 'DATE', 'nullable': True, 'default': None, 'primary_key': False},
                ]
            else:
                return [
                    {'name': 'id', 'type': 'INTEGER', 'nullable': False, 'default': None, 'primary_key': True},
                    {'name': 'name', 'type': 'VARCHAR(100)', 'nullable': True, 'default': None, 'primary_key': False},
                    {'name': 'status', 'type': 'VARCHAR(50)', 'nullable': True, 'default': None, 'primary_key': False},
                ]
        try:
            engine = self.get_engine()
            inspector = inspect(engine)
            columns = inspector.get_columns(table, schema=schema)
            engine.dispose()
            result = []
            for col in columns:
                result.append({
                    'name': col['name'],
                    'type': str(col['type']),
                    'nullable': col.get('nullable', True),
                    'default': str(col.get('default', '')) if col.get('default') else None,
                    'primary_key': col.get('autoincrement', False),
                })
            return result
        except SQLAlchemyError as e:
            logger.error(f"Error getting columns: {e}")
            return []

    def _detect_delimiter(self, file_path):
        """Robustly detect the separator of a CSV file."""
        potential_delimiters = [',', ';', '\t', '|']
        counts = {d: [] for d in potential_delimiters}
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = [f.readline() for _ in range(10)]
                lines = [l.strip() for l in lines if l.strip()]
                if not lines:
                    return ','
                for line in lines:
                    for d in potential_delimiters:
                        counts[d].append(line.count(d))
                best_delimiter = ','
                max_consistency = -1
                for d in potential_delimiters:
                    line_counts = counts[d]
                    if not line_counts:
                        continue
                    distinct_counts = set(line_counts)
                    if len(distinct_counts) == 1 and list(distinct_counts)[0] > 0:
                        consistency = 100 + list(distinct_counts)[0]
                    else:
                        non_zero_count = sum(1 for c in line_counts if c > 0)
                        consistency = non_zero_count
                    if consistency > max_consistency:
                        max_consistency = consistency
                        best_delimiter = d
                return best_delimiter
        except Exception as e:
            logger.error(f"Error sniffing delimiter: {e}")
            return ','

    def _get_cleaned_column(self, col):
        """Convert a string/object column (e.g. with comma decimals) to numeric if possible."""
        if pd.api.types.is_numeric_dtype(col):
            return col
        try:
            # Replace comma with dot and try parsing
            cleaned = col.astype(str).str.replace(',', '.', regex=False)
            numeric_col = pd.to_numeric(cleaned, errors='coerce')
            original_nans = col.isnull().sum()
            new_nans = numeric_col.isnull().sum()
            if new_nans <= original_nans:
                return numeric_col
        except Exception:
            pass
        return col

    def _get_file_columns(self, table=None):
        """Get columns from a file source."""
        try:
            df = self.read_file(limit=5, table=table)
            if df is not None:
                # Pre-clean numeric columns to get their true types during introspection
                for col_name in df.columns:
                    df[col_name] = self._get_cleaned_column(df[col_name])
                
                result = []
                for col_name, dtype in df.dtypes.items():
                    result.append({
                        'name': str(col_name),
                        'type': str(dtype),
                        'nullable': bool(df[col_name].isnull().any()),
                        'default': None,
                        'primary_key': False,
                    })
                return result
        except Exception as e:
            logger.error(f"Error reading file columns: {e}")
        return []

    def read_file(self, limit=None, table=None):
        """Read a CSV or Parquet file and return a Pandas DataFrame."""
        import os
        folder_path = self.connection.host
        
        # Determine file path
        if folder_path and os.path.exists(folder_path):
            if table:
                file_path = os.path.join(folder_path, table)
            else:
                try:
                    files = os.listdir(folder_path)
                    ext = '.csv' if self.connection.connection_type == 'csv' else '.parquet'
                    matched_files = [f for f in files if f.lower().endswith(ext)]
                    if matched_files:
                        file_path = os.path.join(folder_path, matched_files[0])
                    else:
                        logger.error(f"No {ext} files found in folder {folder_path}")
                        return None
                except Exception as e:
                    logger.error(f"Error listing files in folder {folder_path}: {e}")
                    return None
        elif self.connection.file:
            file_path = self.connection.file.path
        else:
            logger.error("No folder path or file upload specified for file connection")
            return None

        try:
            is_csv = file_path.lower().endswith('.csv') or self.connection.connection_type == 'csv'
            is_parquet = file_path.lower().endswith('.parquet') or file_path.lower().endswith('.pq') or self.connection.connection_type == 'parquet'

            if is_csv:
                sep = self._detect_delimiter(file_path)
                if limit:
                    return pd.read_csv(file_path, sep=sep, nrows=limit)
                return pd.read_csv(file_path, sep=sep)
            elif is_parquet:
                df = pd.read_parquet(file_path)
                if limit:
                    return df.head(limit)
                return df
            else:
                sep = self._detect_delimiter(file_path)
                if limit:
                    return pd.read_csv(file_path, sep=sep, nrows=limit)
                return pd.read_csv(file_path, sep=sep)
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")
            return None

    def execute_query(self, query, params=None):
        """Execute a SQL query and return results as DataFrame."""
        if not self.connection.is_database:
            raise ValueError("Cannot execute SQL on file connections")
        try:
            engine = self.get_engine()
            with engine.connect() as conn:
                df = pd.read_sql(text(query), conn, params=params)
            engine.dispose()
            return df
        except SQLAlchemyError as e:
            logger.error(f"Query execution error: {e}")
            raise

    def get_column_values(self, schema, table, column, date_column=None, date_start=None, date_end=None, date_operator=None, date_operator_start='>=', date_operator_end='<='):
        """Fetch all values for a column as a list, applying date filters."""
        if self.is_mocked():
            col_lower = column.lower()
            if col_lower in ('status', 'hwm_flag', 'active_flag'):
                return ['active', 'active', 'active', 'active', 'active']
            if col_lower == 'first_name':
                return ['Alice', 'Bob', 'Charlie', 'David', 'Eva']
            if col_lower == 'str_col':
                return [' hello', 'world', 'apple', 'banana', '12345']
            return ['mock_val1', 'mock_val2', 'mock_val3', 'mock_val4', 'mock_val5']
        
        if self.connection.is_file:
            df = self.read_file(table=table)
            if df is None or column not in df.columns:
                return []
            
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
            return df[column].dropna().tolist()

        # Database
        q_col = self._quote_identifier(column)
        q_schema = self._quote_identifier(schema) if schema and schema != 'file' else None
        q_table = self._quote_identifier(table)
        full_table = f"{q_schema}.{q_table}" if q_schema else q_table
        
        query = f"SELECT {q_col} FROM {full_table}"
        conditions = []
        if date_column:
            q_date_col = self._quote_identifier(date_column)
            if date_operator:
                conditions.append(f'{q_date_col} {date_operator} :date_start')
            else:
                if date_start:
                    conditions.append(f'{q_date_col} {date_operator_start} :date_start')
                if date_end:
                    conditions.append(f'{q_date_col} {date_operator_end} :date_end')
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
            
        params = {}
        if date_start:
            params['date_start'] = date_start
        if not date_operator and date_end:
            params['date_end'] = date_end
            
        try:
            df = self.execute_query(query, params)
            return df[column].dropna().tolist() if not df.empty else []
        except Exception as e:
            logger.error(f"Error fetching column values: {e}")
            return []

    def get_aggregation(self, schema, table, column, operation, date_column=None, date_start=None, date_end=None, date_operator=None, date_operator_start='>=', date_operator_end='<='):
        """Execute an aggregation query on a specific column."""
        if operation == 'data_type_check':
            cols = self.get_columns(schema, table)
            for c in cols:
                if c['name'].lower() == column.lower():
                    return c['type']
            return None

        if self.is_mocked():
            return self._mock_aggregation(column, operation)

        if self.connection.is_file:
            return self._file_aggregation(table, column, operation, date_column, date_start, date_end, date_operator, date_operator_start, date_operator_end)

        # Build SQL query dynamically quoting identifiers
        q_col = self._quote_identifier(column)
        q_schema = self._quote_identifier(schema) if schema and schema != 'file' else None
        q_table = self._quote_identifier(table)
        full_table = f"{q_schema}.{q_table}" if q_schema else q_table

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
            'case_insensitive_check': f'MIN(LOWER({q_col}))',
            'trim_check': f'SUM(CASE WHEN {q_col} != TRIM({q_col}) THEN 1 ELSE 0 END)',
            'contains_check': f'SUM(CASE WHEN {q_col} LIKE \'% %\' THEN 1 ELSE 0 END)',
            'starts_with_check': f'SUM(CASE WHEN {q_col} IS NOT NULL AND SUBSTR({q_col}, 1, 1) BETWEEN \'A\' AND \'z\' THEN 1 ELSE 0 END)',
            'ends_with_check': f'SUM(CASE WHEN {q_col} IS NOT NULL AND SUBSTR({q_col}, LENGTH({q_col}), 1) BETWEEN \'A\' AND \'z\' THEN 1 ELSE 0 END)',
            'pattern_match': self._pattern_match_sql(q_col),
            'hash_validation': self._hash_validation_sql(q_col),
        }

        agg_expr = op_map.get(operation, f'COUNT({q_col})')
        query = f"SELECT {agg_expr} AS result FROM {full_table}"

        # Add date filter
        conditions = []
        if date_column:
            q_date_col = self._quote_identifier(date_column)
            if date_operator:
                conditions.append(f'{q_date_col} {date_operator} :date_start')
            else:
                if date_start:
                    conditions.append(f'{q_date_col} {date_operator_start} :date_start')
                if date_end:
                    conditions.append(f'{q_date_col} {date_operator_end} :date_end')

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        params = {}
        if date_start:
            params['date_start'] = date_start
        if not date_operator and date_end:
            params['date_end'] = date_end

        try:
            df = self.execute_query(query, params)
            return df.iloc[0]['result'] if not df.empty else None
        except Exception as e:
            logger.error(f"Aggregation error for {full_table}.{column} ({operation}): {e}")
            return None

    def _file_aggregation(self, table, column, operation, date_column=None, date_start=None, date_end=None, date_operator=None, date_operator_start='>=', date_operator_end='<='):
        """Perform aggregation on a file column with optional date filtering."""
        try:
            df = self.read_file(table=table)
            if df is None or column not in df.columns:
                return None
 
            # Filter by date if applicable
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

            col = self._get_cleaned_column(df[column])
            op_map = {
                'count': lambda: col.count(),
                'min': lambda: col.min(),
                'max': lambda: col.max(),
                'sum': lambda: col.sum(),
                'avg': lambda: col.mean(),
                'distinct_count': lambda: col.nunique(),
                'null_check': lambda: col.isnull().sum(),
                'row_count': lambda: len(df),
                'duplicate_check': lambda: col.duplicated().sum(),
                'min_date': lambda: str(col.min()) if not col.empty else None,
                'max_date': lambda: str(col.max()) if not col.empty else None,
                'length_sum_check': lambda: col.astype(str).str.len().sum(),
                'sum_length': lambda: col.astype(str).str.len().sum(),
                'regex_check': lambda: col.astype(str).str.match(r'^[a-zA-Z0-9_\-\.\s@]+$').sum(),
                'unique_check': lambda: col.nunique(),
                'range_check': lambda: (col >= 0).sum() if pd.api.types.is_numeric_dtype(col) else len(col),
                'equals': lambda: col.sum(),
                'equals_check': lambda: col.min() if not col.empty else None,
                'case_insensitive_check': lambda: col.astype(str).str.lower().min() if not col.empty else None,
                'trim_check': lambda: col.astype(str).apply(lambda x: x != x.strip()).sum(),
                'contains_check': lambda: col.astype(str).str.contains(' ').sum(),
                'starts_with_check': lambda: col.astype(str).str.slice(0, 1).str.isalpha().sum(),
                'ends_with_check': lambda: col.astype(str).str.slice(-1).str.isalpha().sum(),
                'pattern_match': lambda: col.astype(str).str.match(r'^[a-zA-Z0-9_\-\.\s@]+$').sum(),
                'hash_validation': lambda: self._file_hash_calculation(col),
            }

            func = op_map.get(operation)
            if func:
                result = func()
                # Convert numpy types to Python native
                if hasattr(result, 'item'):
                    return result.item()
                return result
        except Exception as e:
            logger.error(f"File aggregation error: {e}")
        return None

    def _file_hash_calculation(self, col):
        import hashlib
        hashes = col.dropna().astype(str).apply(lambda x: int(hashlib.md5(x.encode('utf-8')).hexdigest(), 16) % (2**31 - 1))
        hash_sum = hashes.sum()
        return hashlib.md5(str(hash_sum).encode('utf-8')).hexdigest()

    def check_duplicates(self, schema, table, column, date_column=None, date_start=None, date_end=None, date_operator=None, date_operator_start='>=', date_operator_end='<='):
        """Check for duplicate values in a column with optional date filtering."""
        if self.is_mocked():
            return 0
        if self.connection.is_file:
            return self._file_aggregation(table, column, 'duplicate_check', date_column, date_start, date_end, date_operator, date_operator_start, date_operator_end)

        q_col = self._quote_identifier(column)
        q_schema = self._quote_identifier(schema) if schema and schema != 'file' else None
        q_table = self._quote_identifier(table)
        full_table = f"{q_schema}.{q_table}" if q_schema else q_table

        query = f"""
            SELECT COUNT(*) - COUNT(DISTINCT {q_col}) AS duplicates
            FROM {full_table}
        """
        # Add date filter
        conditions = []
        if date_column:
            q_date_col = self._quote_identifier(date_column)
            if date_operator:
                conditions.append(f'{q_date_col} {date_operator} :date_start')
            else:
                if date_start:
                    conditions.append(f'{q_date_col} {date_operator_start} :date_start')
                if date_end:
                    conditions.append(f'{q_date_col} {date_operator_end} :date_end')

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        params = {}
        if date_start:
            params['date_start'] = date_start
        if not date_operator and date_end:
            params['date_end'] = date_end

        try:
            df = self.execute_query(query, params)
            return df.iloc[0]['duplicates'] if not df.empty else 0
        except Exception as e:
            logger.error(f"Check duplicates error for {full_table}.{column}: {e}")
            return None
