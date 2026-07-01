"""
Database & File Connector Engine
Handles introspection (schemas, tables, columns) and data reading
for PostgreSQL, MySQL, CSV, and Parquet sources.
"""
import logging
import threading
import time
import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
import os
os.environ["JAVA_HOME"]="/usr/lib/java/jre"
logger = logging.getLogger('connections')

# Thread-safe global SQLAlchemy engine cache
_engines_cache = {}
_engines_lock = threading.Lock()

# Thread-safe global metadata cache
_metadata_cache = {}
_metadata_cache_lock = threading.Lock()
METADATA_CACHE_TTL = 300  # 5 minutes TTL

_jvm_available = None

def get_cached_engine(conn_string):
    """Get or create a cached SQLAlchemy engine with optimized pooling settings."""
    with _engines_lock:
        if conn_string not in _engines_cache:
            _engines_cache[conn_string] = create_engine(
                conn_string,
                pool_pre_ping=True,
                pool_size=15,
                max_overflow=25,
                pool_recycle=1800,
                pool_timeout=30
            )
        return _engines_cache[conn_string]

def get_cached_metadata(cache_key, fetch_func):
    """Retrieve metadata from cache or fetch and update cache if expired."""
    now = time.time()
    with _metadata_cache_lock:
        if cache_key in _metadata_cache:
            entry_time, data = _metadata_cache[cache_key]
            if now - entry_time < METADATA_CACHE_TTL:
                return data
    # Fetch outside lock to prevent blocking database calls
    data = fetch_func()
    with _metadata_cache_lock:
        _metadata_cache[cache_key] = (now, data)
    return data


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
        elif self.connection.connection_type == 'oracle':
            return f'"{name.upper()}"'
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
        elif t == 'databricks':
            return f"SUM(CASE WHEN {q_col} RLIKE '^[A-Za-z0-9_\\\\-\\\\.\\\\s@]+$' THEN 1 ELSE 0 END)"
        elif t in ('mssql', 'sqlserver'):
            return f"SUM(CASE WHEN {q_col} NOT LIKE '%[^A-Za-z0-9_ .@-]%' THEN 1 ELSE 0 END)"
        elif t == 'oracle':
            return f"SUM(CASE WHEN REGEXP_LIKE({q_col}, '^[A-Za-z0-9_\\-\\.\\s@]+$') THEN 1 ELSE 0 END)"
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
        elif t == 'databricks':
            return f"CAST(SUM(hash(CAST({q_col} AS STRING))) AS STRING)"
        elif t in ('mssql', 'sqlserver'):
            return f"CAST(SUM(CAST(BINARY_CHECKSUM({q_col}) AS BIGINT)) AS VARCHAR)"
        elif t == 'oracle':
            return f"CAST(SUM(ORA_HASH({q_col})) AS VARCHAR(100))"
        else:
            return f"CAST(SUM(LENGTH(COALESCE(CAST({q_col} AS VARCHAR), ''))) AS VARCHAR)"

    def get_lakehouse_connection(self):
        """Get a direct JDBC connection to Lakehouse using jaydebeapi."""
        import jaydebeapi

        try:
            return jaydebeapi.connect(
                "io.trino.jdbc.TrinoDriver",
                (
                    f"jdbc:trino://{self.connection.host}:{self.connection.port or 443}/"
                    f"{self.connection.database_name}/default"
                    f"?SSL=true"
                    f"&SSLVerification=FULL"
                    f"&SSLTrustStorePath=/data1/shared/Jars/lakehouse_odbc_fullchain.pem"
                ),
                {
                    "user": self.connection.username,
                    "password": self.connection.get_password()
                },
                jars=["/data1/shared/Jars/trino-jdbc-430.jar"]
            )
        except Exception as e:
            err_msg = str(e).lower()
            if "jvm" in err_msg or "java" in err_msg or "jpype" in err_msg or "shared library" in err_msg or "libjvm" in err_msg:
                global _jvm_available
                _jvm_available = False
            raise

    def get_engine(self):
        """Create and return a cached SQLAlchemy engine."""
        if self.connection.connection_type == 'lakehouse':
            raise ValueError("SQLAlchemy engine is not supported for Lakehouse connections")
        conn_string = self.connection.get_connection_string()
        if not conn_string:
            raise ValueError(f"Cannot create engine for connection type: {self.connection.connection_type}")
        return get_cached_engine(conn_string)

    def is_mocked(self):
        """Check if this connection should use mocked data because driver is missing or it's a dummy host."""
        if self.connection.is_file:
            return False
        host = getattr(self.connection, 'host', '') or ''
        name = getattr(self.connection, 'name', '') or ''
        if 'dummy' in host.lower() or 'mock' in host.lower() or 'dummy' in name.lower() or 'mock' in name.lower():
            return True
        try:
            if self.connection.connection_type == 'lakehouse':
                global _jvm_available
                if _jvm_available is False:
                    return True
                try:
                    import jaydebeapi
                    import jpype
                except (ImportError, ModuleNotFoundError):
                    _jvm_available = False
                    return True
                try:
                    jvm_path = jpype.getDefaultJVMPath()
                    if not jvm_path:
                        _jvm_available = False
                        return True
                    import os
                    if not os.path.exists(jvm_path) or not os.path.isfile(jvm_path):
                        _jvm_available = False
                        return True
                    import ctypes
                    try:
                        ctypes.CDLL(jvm_path)
                    except OSError:
                        _jvm_available = False
                        return True
                except Exception:
                    _jvm_available = False
                    return True
                _jvm_available = True
            else:
                # SQLAlchemy check
                engine = self.get_engine()
                # Accessing the dialect property triggers importing the database-specific module/driver
                _ = engine.dialect
        except Exception:
            return True
        return False

    def _uses_catalog_hierarchy(self):
        """Return True for connection types that use catalog->schema->table hierarchy."""
        return self.connection.connection_type in ('databricks', 'lakehouse', 'oracle', 'db2', 'postgresql', 'mysql')

    def _build_full_table_name(self, table, schema=None, catalog=None):
        """Build a fully qualified table identifier for supported dialects.

        Oracle, DB2, PostgreSQL and MySQL use only schema.table in SQL (no catalog prefix).
        Only Databricks/Lakehouse use the full catalog.schema.table notation.
        """
        ctype = self.connection.connection_type
        quoted_table = self._quote_identifier(table)
        quoted_schema = self._quote_identifier(schema) if schema and schema != 'file' else None

        # For dialects that support 3-part names in SQL (Databricks / Lakehouse only)
        if ctype in ('databricks', 'lakehouse') and catalog:
            quoted_catalog = self._quote_identifier(catalog)
            if quoted_catalog and quoted_schema:
                return f"{quoted_catalog}.{quoted_schema}.{quoted_table}"
            if quoted_catalog:
                return f"{quoted_catalog}.{quoted_table}"

        if quoted_schema:
            return f"{quoted_schema}.{quoted_table}"
        return quoted_table

    def _extract_first_column(self, df):
        if df is None or df.empty:
            return []
        if df.shape[1] >= 1:
            return [str(v).strip() for v in df.iloc[:, 0].tolist() if v is not None]
        return []

    def _extract_table_names(self, df):
        if df is None or df.empty:
            return []
        if df.shape[1] >= 2:
            return [str(v).strip() for v in df.iloc[:, 1].tolist() if v is not None]
        return [str(v).strip() for v in df.iloc[:, 0].tolist() if v is not None]

    def get_catalogs(self):
        """Get list of catalogs. For Databricks/Lakehouse uses SHOW CATALOGS;
        for Oracle/DB2/PG/MySQL returns the configured database name as the single catalog."""
        if self.connection.is_file:
            return []
        if self.is_mocked():
            if self.connection.connection_type in ('databricks', 'lakehouse'):
                return ['hive_metastore', 'default', 'prod_catalog']
            elif self.connection.connection_type == 'oracle':
                return [self.connection.database_name or 'ORCL']
            elif self.connection.connection_type == 'db2':
                return [self.connection.database_name or 'DB2INST1']
            elif self.connection.connection_type in ('postgresql', 'mysql'):
                return [self.connection.database_name or 'default']
            return []

        if not self._uses_catalog_hierarchy():
            return []

        ctype = self.connection.connection_type

        # For Oracle/DB2/PG/MySQL — the catalog is just the database name stored in the connection.
        # No remote call needed; return the single catalog immediately.
        if ctype in ('oracle', 'db2', 'postgresql', 'mysql'):
            return [self.connection.database_name] if self.connection.database_name else []

        # Databricks / Lakehouse
        cache_key = f"catalogs:{self.connection.id}"
        def fetch():
            try:
                query = 'SHOW CATALOGS'
                df = self.execute_query(query)
                return self._extract_first_column(df)
            except Exception as e:
                logger.error(f"Error getting catalogs: {e}")
                return []
        return get_cached_metadata(cache_key, fetch)

    def get_schemas(self, catalog=None):
        """Get list of schemas from database connection."""
        if self.connection.is_file:
            return ['file']
        if self.is_mocked():
            if self.connection.connection_type in ('databricks', 'lakehouse'):
                if catalog:
                    return [f'{catalog}_schema', 'default']
                return ['hive_metastore', 'default', 'prod_catalog']
            elif self.connection.connection_type == 'db2':
                return ['DB2INST1', 'SALES', 'PRODUCTION']
            elif self.connection.connection_type == 'oracle':
                return ['SYSTEM', 'HR', 'SCOTT']
            schemas = ['default', 'public', 'loan_target']
            if self.connection.database_name:
                schemas.append(self.connection.database_name)
            return sorted(list(set(schemas)))

        ctype = self.connection.connection_type
        cache_key = f"schemas:{self.connection.id}:{catalog or ''}"
        def fetch():
            try:
                if ctype == 'lakehouse':
                    if catalog:
                        quoted_catalog = self._quote_identifier(catalog)
                        query = f"SHOW SCHEMAS IN {quoted_catalog}"
                    else:
                        query = "SHOW SCHEMAS"
                    df = self.execute_query(query)
                    return self._extract_first_column(df)

                engine = self.get_engine()
                # Databricks — use SHOW SCHEMAS IN <catalog>
                if ctype == 'databricks' and catalog:
                    quoted_catalog = self._quote_identifier(catalog)
                    query = f"SHOW SCHEMAS IN {quoted_catalog}"
                    df = self.execute_query(query)
                    return self._extract_first_column(df)
                # Oracle — fetch accessible schemas via ALL_USERS
                if ctype == 'oracle':
                    try:
                        df = self.execute_query("SELECT USERNAME FROM ALL_USERS ORDER BY USERNAME")
                        schemas = self._extract_first_column(df)
                        return schemas if schemas else [self.connection.username.upper() if self.connection.username else 'PUBLIC']
                    except Exception as e:
                        logger.warning(f"Oracle ALL_USERS query failed, falling back to inspector: {e}")
                        inspector = inspect(engine)
                        return inspector.get_schema_names()
                # DB2 — fetch schemas
                if ctype == 'db2':
                    try:
                        df = self.execute_query("SELECT DISTINCT SCHEMANAME FROM SYSCAT.SCHEMATA ORDER BY SCHEMANAME")
                        return self._extract_first_column(df)
                    except Exception as e:
                        logger.warning(f"DB2 schema query failed: {e}")
                # PostgreSQL / MySQL and all others — use SQLAlchemy inspector
                inspector = inspect(engine)
                schemas = inspector.get_schema_names()
                return schemas
            except Exception as e:
                logger.error(f"Error getting schemas: {e}")
                return []
        return get_cached_metadata(cache_key, fetch)

    def get_tables(self, schema=None, catalog=None):
        """Get list of tables from a schema (optionally within a catalog)."""
        if self.connection.is_file:
            import os
            folder_path = self.connection.host
            if folder_path and os.path.exists(folder_path):
                try:
                    files = os.listdir(folder_path)
                    if self.connection.connection_type == 'csv':
                        exts = ('.csv',)
                    elif self.connection.connection_type == 'parquet':
                        exts = ('.parquet', '.parq')
                    elif self.connection.connection_type == 'excel':
                        exts = ('.xlsx', '.xls')
                    elif self.connection.connection_type == 'text':
                        exts = ('.txt',)
                    else:
                        exts = ('.csv',)
                    matched_files = [f for f in files if f.lower().endswith(exts)]
                    if matched_files:
                        return sorted(matched_files)
                except Exception as e:
                    logger.error(f"Error listing folder files: {e}")
            filename = self.connection.file.name if self.connection.file else 'file_data'
            return [filename.split('/')[-1] if '/' in filename else filename]
        if self.is_mocked():
            return ['customers', 'transactions', 'orders', 'products', 'loans']

        ctype = self.connection.connection_type
        cache_key = f"tables:{self.connection.id}:{catalog or ''}:{schema or ''}"
        def fetch():
            try:
                if ctype == 'lakehouse':
                    if catalog and schema:
                        quoted_catalog = self._quote_identifier(catalog)
                        quoted_schema = self._quote_identifier(schema)
                        query = f"SHOW TABLES IN {quoted_catalog}.{quoted_schema}"
                    elif schema:
                        quoted_schema = self._quote_identifier(schema)
                        query = f"SHOW TABLES IN {quoted_schema}"
                    else:
                        quoted_catalog = self._quote_identifier(catalog) if catalog else None
                        if quoted_catalog:
                            query = f"SHOW TABLES IN {quoted_catalog}"
                        else:
                            query = 'SHOW TABLES'
                    df = self.execute_query(query)
                    return sorted(self._extract_table_names(df))

                engine = self.get_engine()
                # Databricks — SHOW TABLES syntax
                if ctype == 'databricks':
                    if catalog and schema:
                        quoted_catalog = self._quote_identifier(catalog)
                        quoted_schema = self._quote_identifier(schema)
                        query = f"SHOW TABLES IN {quoted_catalog}.{quoted_schema}"
                    elif schema:
                        quoted_schema = self._quote_identifier(schema)
                        query = f"SHOW TABLES IN {quoted_schema}"
                    else:
                        quoted_catalog = self._quote_identifier(catalog) if catalog else None
                        if quoted_catalog:
                            query = f"SHOW TABLES IN {quoted_catalog}"
                        else:
                            query = 'SHOW TABLES'
                    df = self.execute_query(query)
                    return sorted(self._extract_table_names(df))
                # Oracle — use ALL_TABLES with owner filter
                if ctype == 'oracle':
                    sch = (schema or '').upper() or (self.connection.username or '').upper()
                    try:
                        if sch:
                            df = self.execute_query(
                                f"SELECT TABLE_NAME FROM ALL_TABLES WHERE OWNER = '{sch}' ORDER BY TABLE_NAME"
                            )
                        else:
                            df = self.execute_query("SELECT TABLE_NAME FROM USER_TABLES ORDER BY TABLE_NAME")
                        tables = self._extract_first_column(df)
                        # Also get views
                        try:
                            if sch:
                                vdf = self.execute_query(
                                    f"SELECT VIEW_NAME FROM ALL_VIEWS WHERE OWNER = '{sch}' ORDER BY VIEW_NAME"
                                )
                            else:
                                vdf = self.execute_query("SELECT VIEW_NAME FROM USER_VIEWS ORDER BY VIEW_NAME")
                            views = self._extract_first_column(vdf)
                        except Exception:
                            views = []
                        return sorted(tables + views)
                    except Exception as e:
                        logger.warning(f"Oracle direct table query failed, using inspector: {e}")
                        inspector = inspect(engine)
                        tables = inspector.get_table_names(schema=schema or None)
                        views = inspector.get_view_names(schema=schema or None)
                        return sorted(tables + views)
                # All other databases — SQLAlchemy inspector
                inspector = inspect(engine)
                tables = inspector.get_table_names(schema=schema or None)
                views = inspector.get_view_names(schema=schema or None)
                return sorted(tables + views)
            except Exception as e:
                logger.error(f"Error getting tables: {e}")
                return []
        return get_cached_metadata(cache_key, fetch)

    def get_columns(self, schema=None, table=None, catalog=None):
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
            elif 'loan' in t_name:
                return [
                    {'name': 'loan_id', 'type': 'INTEGER', 'nullable': False, 'default': None, 'primary_key': True},
                    {'name': 'loan_type', 'type': 'VARCHAR(100)', 'nullable': True, 'default': None, 'primary_key': False},
                    {'name': 'amount', 'type': 'DECIMAL(12,2)', 'nullable': True, 'default': None, 'primary_key': False},
                    {'name': 'status', 'type': 'VARCHAR(50)', 'nullable': True, 'default': None, 'primary_key': False},
                ]
            else:
                return [
                    {'name': 'id', 'type': 'INTEGER', 'nullable': False, 'default': None, 'primary_key': True},
                    {'name': 'name', 'type': 'VARCHAR(100)', 'nullable': True, 'default': None, 'primary_key': False},
                    {'name': 'status', 'type': 'VARCHAR(50)', 'nullable': True, 'default': None, 'primary_key': False},
                ]

        cache_key = f"columns:{self.connection.id}:{catalog or ''}:{schema or ''}:{table or ''}"
        def fetch():
            try:
                ctype = self.connection.connection_type
                if ctype == 'lakehouse':
                    qualified_name = self._build_full_table_name(table, schema=schema, catalog=catalog)
                    try:
                        query = f"DESCRIBE {qualified_name}"
                        df = self.execute_query(query)
                    except Exception as e:
                        logger.warning(f"DESCRIBE query failed for lakehouse table {qualified_name}: {e}. Falling back to SELECT limit 0.")
                        query = f"SELECT * FROM {qualified_name} WHERE 1=0"
                        df = self.execute_query(query)

                    columns = []
                    if df is not None:
                        if df.empty and len(df.columns) > 0:
                            for col_name, dtype in df.dtypes.items():
                                columns.append({
                                    'name': str(col_name),
                                    'type': str(dtype),
                                    'nullable': True,
                                    'default': None,
                                    'primary_key': False,
                                })
                        elif not df.empty:
                            for row in df.itertuples(index=False):
                                name = str(row[0]).strip()
                                dtype = str(row[1]).strip() if len(row) > 1 else ''
                                columns.append({
                                    'name': name,
                                    'type': dtype,
                                    'nullable': True,
                                    'default': None,
                                    'primary_key': False,
                                })
                    return columns

                engine = self.get_engine()
                # Databricks — DESCRIBE TABLE
                if ctype == 'databricks':
                    qualified_name = self._build_full_table_name(table, schema=schema, catalog=catalog)
                    try:
                        query = f"DESCRIBE TABLE {qualified_name}"
                        df = self.execute_query(query)
                    except Exception as e:
                        logger.warning(f"DESCRIBE TABLE query failed for databricks table {qualified_name}: {e}. Falling back to SELECT limit 0.")
                        query = f"SELECT * FROM {qualified_name} WHERE 1=0"
                        df = self.execute_query(query)

                    columns = []
                    if df is not None:
                        if df.empty and len(df.columns) > 0:
                            for col_name, dtype in df.dtypes.items():
                                columns.append({
                                    'name': str(col_name),
                                    'type': str(dtype),
                                    'nullable': True,
                                    'default': None,
                                    'primary_key': False,
                                })
                        elif not df.empty:
                            for row in df.itertuples(index=False):
                                name = str(row[0]).strip()
                                dtype = str(row[1]).strip() if len(row) > 1 else ''
                                columns.append({
                                    'name': name,
                                    'type': dtype,
                                    'nullable': True,
                                    'default': None,
                                    'primary_key': False,
                                })
                    return columns

                # Oracle — use ALL_TAB_COLUMNS for reliable schema-qualified column fetch
                if ctype == 'oracle':
                    tbl = (table or '').upper()
                    sch = (schema or '').upper() or (self.connection.username or '').upper()
                    try:
                        if sch:
                            query = (
                                f"SELECT COLUMN_NAME, DATA_TYPE, NULLABLE, DATA_DEFAULT "
                                f"FROM ALL_TAB_COLUMNS "
                                f"WHERE OWNER = '{sch}' AND TABLE_NAME = '{tbl}' "
                                f"ORDER BY COLUMN_ID"
                            )
                        else:
                            query = (
                                f"SELECT COLUMN_NAME, DATA_TYPE, NULLABLE, DATA_DEFAULT "
                                f"FROM USER_TAB_COLUMNS "
                                f"WHERE TABLE_NAME = '{tbl}' "
                                f"ORDER BY COLUMN_ID"
                            )
                        df = self.execute_query(query)
                        result = []
                        if df is not None and not df.empty:
                            for row in df.itertuples(index=False):
                                col_name = str(row[0]).strip()
                                data_type = str(row[1]).strip() if len(row) > 1 else ''
                                nullable = str(row[2]).strip() if len(row) > 2 else 'Y'
                                default = str(row[3]).strip() if len(row) > 3 and row[3] is not None else None
                                result.append({
                                    'name': col_name,
                                    'type': data_type,
                                    'nullable': nullable != 'N',
                                    'default': default,
                                    'primary_key': False,
                                })
                        return result
                    except Exception as e:
                        logger.warning(f"Oracle ALL_TAB_COLUMNS query failed, using inspector: {e}")
                        inspector = inspect(engine)
                        sch_param = sch if sch else None
                        columns = inspector.get_columns(tbl, schema=sch_param)
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

                # All other databases — SQLAlchemy inspector
                inspector = inspect(engine)
                columns = inspector.get_columns(table, schema=schema or None)
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
            except Exception as e:
                logger.warning(f"Metadata-based columns query failed: {e}. Falling back to SELECT query fallback.")
                try:
                    qualified_name = self._build_full_table_name(table, schema=schema, catalog=catalog)
                    query = f"SELECT * FROM {qualified_name} WHERE 1=0"
                    df = self.execute_query(query)
                    columns = []
                    if df is not None and len(df.columns) > 0:
                        for col_name, dtype in df.dtypes.items():
                            columns.append({
                                'name': str(col_name),
                                'type': str(dtype),
                                'nullable': True,
                                'default': None,
                                'primary_key': False,
                            })
                        return columns
                except Exception as ex:
                    logger.error(f"Ultimate columns query fallback failed: {ex}")
                return []
        return get_cached_metadata(cache_key, fetch)
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
        global _jvm_available
        try:
            if self.connection.is_database:
                if self.is_mocked():
                    return True, f"Connection successful (Simulated - driver for {self.connection.get_connection_type_display()} not installed or mock host)"
                try:
                    if self.connection.connection_type == 'lakehouse':
                        conn = self.get_lakehouse_connection()
                        try:
                            curs = conn.cursor()
                            try:
                                curs.execute("SELECT 1")
                                curs.fetchone()
                            finally:
                                try:
                                    curs.close()
                                except Exception:
                                    pass
                            return True, "Connection successful"
                        finally:
                            try:
                                conn.close()
                            except Exception:
                                pass

                    engine = self.get_engine()
                    with engine.connect() as conn:
                        if engine.dialect.name == "oracle":
                            conn.execute(text("SELECT 1 FROM DUAL"))
                        else:
                                conn.execute(text("SELECT 1"))
                    engine.dispose()
                    return True, "Connection successful"
                except Exception as e:
                    err_msg = str(e)
                    if "NoSuchModuleError" in err_msg or "ModuleNotFoundError" in err_msg or "jvm" in err_msg.lower() or "java" in err_msg.lower() or "jpype" in err_msg.lower() or "shared library" in err_msg.lower() or "libjvm" in err_msg.lower():
                        if self.connection.connection_type == 'lakehouse':
                            _jvm_available = False
                        return True, f"Connection successful (Simulated - driver/JVM for {self.connection.get_connection_type_display()} not configured)"
                    raise
            elif self.connection.is_file:
                df = self.read_file(limit=5)
                if df is not None and not df.empty:
                    return True, f"File readable, {len(df.columns)} columns found"
                return False, "File is empty or unreadable"
        except Exception as e:
            err_msg = str(e)
            if "NoSuchModuleError" in err_msg or "ModuleNotFoundError" in err_msg or "jvm" in err_msg.lower() or "java" in err_msg.lower() or "jpype" in err_msg.lower() or "shared library" in err_msg.lower() or "libjvm" in err_msg.lower() or "no module" in err_msg.lower():
                if self.connection.connection_type == 'lakehouse':
                    _jvm_available = False
                return True, f"Connection successful (Simulated - driver/JVM for {self.connection.get_connection_type_display()} not configured)"
            logger.error(f"Connection test failed for '{self.connection.name}': {e}")
            return False, str(e)



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
        """Read a CSV, Parquet, Excel, or Text file and return a Pandas DataFrame."""
        import os
        folder_path = self.connection.host
        
        # Determine file path
        if folder_path and os.path.exists(folder_path):
            if table:
                file_path = os.path.join(folder_path, table)
            else:
                try:
                    files = os.listdir(folder_path)
                    if self.connection.connection_type == 'csv':
                        exts = ('.csv',)
                    elif self.connection.connection_type == 'parquet':
                        exts = ('.parquet', '.parq')
                    elif self.connection.connection_type == 'excel':
                        exts = ('.xlsx', '.xls')
                    elif self.connection.connection_type == 'text':
                        exts = ('.txt',)
                    else:
                        exts = ('.csv',)
                    matched_files = [f for f in files if f.lower().endswith(exts)]
                    if matched_files:
                        file_path = os.path.join(folder_path, matched_files[0])
                    else:
                        logger.error(f"No matching files found in folder {folder_path}")
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
            is_parquet = file_path.lower().endswith(('.parquet', '.parq', '.pq')) or self.connection.connection_type == 'parquet'
            is_excel = file_path.lower().endswith(('.xlsx', '.xls')) or self.connection.connection_type == 'excel'
            is_text = file_path.lower().endswith('.txt') or self.connection.connection_type == 'text'

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
            elif is_excel:
                if limit:
                    return pd.read_excel(file_path, nrows=limit)
                return pd.read_excel(file_path)
            elif is_text:
                sep = self._detect_delimiter(file_path)
                if limit:
                    return pd.read_csv(file_path, sep=sep, nrows=limit)
                return pd.read_csv(file_path, sep=sep)
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
        if self.is_mocked():
            return pd.DataFrame()
        start_time = time.time()
        try:
            if self.connection.connection_type == 'lakehouse' and not self.is_mocked():
                conn = self.get_lakehouse_connection()
                # Disable rollback if driver/db doesn't support it to prevent SQLException masking original error
                conn.rollback = lambda *args, **kwargs: None
                try:
                    df = pd.read_sql(query, conn, params=params)
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
            else:
                engine = self.get_engine()
                with engine.connect() as conn:
                    df = pd.read_sql(text(query), conn, params=params)
            duration = time.time() - start_time
            query_snippet = " ".join(query.split())[:150]
            logger.info(f"Query execution took {duration:.2f}s: {query_snippet}...")
            return df
        except Exception as e:
            logger.error(f"Query execution error after {time.time() - start_time:.2f}s: {e}")
            raise

    def get_column_values(self, schema, table, column, catalog=None, date_column=None, date_start=None, date_end=None, date_operator=None, date_operator_start='>=', date_operator_end='<='):
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
        full_table = self._build_full_table_name(table, schema=schema if schema and schema != 'file' else None, catalog=catalog)
        query = f"SELECT {q_col} FROM {full_table}"
        conditions = []
        if date_column:
            q_date_col = self._quote_identifier(date_column)
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

    def get_aggregation(self, schema, table, column, operation, catalog=None, date_column=None, date_start=None, date_end=None, date_operator=None, date_operator_start='>=', date_operator_end='<='):
        """Execute an aggregation query on a specific column."""
        if operation == 'data_type_check':
            cols = self.get_columns(schema, table, catalog=catalog)
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
        full_table = self._build_full_table_name(table, schema=schema if schema and schema != 'file' else None, catalog=catalog)

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
                if date_start:
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

    def check_duplicates(self, schema, table, column, catalog=None, date_column=None, date_start=None, date_end=None, date_operator=None, date_operator_start='>=', date_operator_end='<='):
        """Check for duplicate values in a column with optional date filtering."""
        if self.is_mocked():
            return 0
        if self.connection.is_file:
            return self._file_aggregation(table, column, 'duplicate_check', date_column, date_start, date_end, date_operator, date_operator_start, date_operator_end)

        q_col = self._quote_identifier(column)
        full_table = self._build_full_table_name(table, schema=schema if schema and schema != 'file' else None, catalog=catalog)

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

    def get_preview_data(self, schema, table, catalog=None, limit=100, offset=0):
        """Fetch up to `limit` records from a database or file source for preview starting at `offset`."""
        if self.is_mocked():
            return pd.DataFrame([
                {'id': i, 'name': f'Mock Name {i}', 'status': 'Active', 'created_at': '2025-01-01'}
                for i in range(1, 11)
            ])
        if self.connection.is_file:
            try:
                df = self.read_file(table=table)
                if df is not None:
                    return df.iloc[offset:offset+limit]
                return None
            except Exception as e:
                logger.error(f"Error getting file preview: {e}")
                return None

        # Database
        full_table = self._build_full_table_name(table, schema=schema if schema and schema != 'file' else None, catalog=catalog)
        db_type = str(self.connection.connection_type).lower()
        
        if offset == 0:
            if db_type in ('oracle', 'db2'):
                query = f"SELECT * FROM {full_table} FETCH FIRST {limit} ROWS ONLY"
            elif db_type in ('mssql', 'sqlserver'):
                query = f"SELECT TOP {limit} * FROM {full_table}"
            else:
                query = f"SELECT * FROM {full_table} LIMIT {limit}"
        else:
            if db_type in ('oracle', 'db2'):
                query = f"SELECT * FROM {full_table} OFFSET {offset} ROWS FETCH NEXT {limit} ROWS ONLY"
            elif db_type in ('mssql', 'sqlserver'):
                query = f"SELECT * FROM {full_table} ORDER BY (SELECT NULL) OFFSET {offset} ROWS FETCH NEXT {limit} ROWS ONLY"
            elif db_type in ('lakehouse', 'databricks'):
                query = f"SELECT * FROM {full_table} LIMIT {offset + limit}"
            else:
                query = f"SELECT * FROM {full_table} LIMIT {limit} OFFSET {offset}"
            
        try:
            df = self.execute_query(query)
            if df is not None and db_type in ('lakehouse', 'databricks') and offset > 0:
                return df.iloc[offset:]
            return df
        except Exception as e:
            # Fallback query if limit/offset syntax fails
            try:
                if db_type not in ('oracle', 'db2', 'mssql', 'sqlserver'):
                    try:
                        query = f"SELECT * FROM {full_table} LIMIT {offset + limit}"
                        df = self.execute_query(query)
                        if df is not None:
                            return df.iloc[offset:]
                    except Exception:
                        pass

                query = f"SELECT * FROM {full_table}"
                df = self.execute_query(query)
                return df.iloc[offset:offset+limit] if df is not None else None
            except Exception as ex:
                logger.error(f"Error getting database preview: {ex}")
                return None
