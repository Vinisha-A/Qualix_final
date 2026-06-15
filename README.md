# Qualix Data Quality Platform

## Overview

Qualix is a Django-based Data Quality Management Platform that allows users to:

* Configure data source connections
* Test connectivity
* Browse schemas, tables, and columns
* Execute data quality validations
* Generate quality reports
* Monitor data quality through dashboards

Supported data sources:

* PostgreSQL
* MySQL
* Databricks
* Oracle
* DB2
* CSV Files
* Parquet Files

---

# High Level Architecture

```text
User
 │
 ▼
Django Web Application
 │
 ├── Connection Management Module
 │     ├── Create Connection
 │     ├── Edit Connection
 │     ├── Test Connection
 │     └── Store Credentials
 │
 ├── Connector Engine
 │     ├── Build Connection String
 │     ├── Create SQLAlchemy Engine
 │     ├── Connect to Database
 │     └── Retrieve Metadata
 │
 ├── Data Quality Engine
 │     ├── Schema Validation
 │     ├── Table Validation
 │     ├── Column Validation
 │     ├── Row Count Validation
 │     ├── Null Check
 │     ├── Duplicate Check
 │     └── Custom Rules
 │
 ├── Reporting Module
 │     ├── Validation Results
 │     ├── Quality Score
 │     └── Report Generation
 │
 └── Dashboard
       ├── Summary Metrics
       ├── Execution History
       └── Notifications
 │
 ▼
Data Sources
 ├── PostgreSQL
 ├── MySQL
 ├── Databricks
 ├── Oracle
 ├── DB2
 ├── CSV
 └── Parquet
```

---

# Project Structure

```text
qualix/
│
├── connections/
│   ├── models.py
│   ├── forms.py
│   ├── views.py
│   ├── urls.py
│   └── connector.py
│
├── dashboard/
│
├── logs/
│
├── validations/
│
├── reports/
│
└── templates/
```

---

# Component Responsibilities

## models.py

Responsible for:

* DataConnection model
* Connection configuration storage
* Password encryption using Fernet
* Connection string generation
* Supported connection types

Main Functions:

* set_password()
* get_password()
* get_connection_string()

---

## forms.py

Responsible for:

* Connection creation forms
* Connection editing forms
* Input validation
* Databricks configuration validation
* Password handling

Validation Examples:

* Host required
* Username required
* Database name required
* Access token required

---

## views.py

Responsible for:

* UI requests
* CRUD operations for connections
* Connection testing API
* Metadata retrieval APIs

Endpoints:

* Create Connection
* Edit Connection
* Delete Connection
* Test Connection
* Get Schemas
* Get Tables
* Get Columns

---

## connector.py

Responsible for:

* Connection engine implementation
* SQLAlchemy integration
* Engine creation
* Metadata extraction
* Query execution

Main Functions:

* get_engine()
* test_connection()
* get_schemas()
* get_tables()
* get_columns()

---

# Connection Flow

```text
User
 │
 ▼
Create Connection
 │
 ▼
forms.py Validation
 │
 ▼
models.py Save Configuration
 │
 ▼
Encrypt Password
 │
 ▼
Store Connection
 │
 ▼
User Clicks Test
 │
 ▼
views.py
 │
 ▼
ConnectorEngine
 │
 ▼
get_engine()
 │
 ▼
Build Connection String
 │
 ▼
SQLAlchemy Engine
 │
 ▼
Target Database
 │
 ▼
Execute SELECT 1
 │
 ▼
Success / Failure Response
```

---

# Databricks Connection Flow

```text
User
 │
 ▼
Enter Databricks Details
 │
 ├── Server Hostname
 │
 ├── HTTP Path
 │
 └── Access Token
 │
 ▼
forms.py Validation
 │
 ▼
models.py
 │
 ▼
Generate Connection String
 │
 ▼
ConnectorEngine.get_engine()
 │
 ▼
SQLAlchemy Databricks Dialect
 │
 ▼
Databricks SQL Warehouse
 │
 ▼
Connection Test
 │
 ▼
Schema Extraction
```

---

# Security

Credentials are protected using:

* Fernet Encryption
* Encrypted Password Storage
* Secure Credential Retrieval

Methods:

* set_password()
* get_password()

---

# Data Quality Workflow

```text
Connection
 │
 ▼
Metadata Discovery
 │
 ▼
Schema Selection
 │
 ▼
Table Selection
 │
 ▼
Column Selection
 │
 ▼
Validation Rules
 │
 ▼
Execution Engine
 │
 ▼
Results
 │
 ▼
Reports
 │
 ▼
Dashboard
```

---

# Technologies Used

Backend:

* Python
* Django

Database Connectivity:

* SQLAlchemy
* PyODBC
* Database Drivers

Security:

* Cryptography (Fernet)

Supported Databases:

* PostgreSQL
* MySQL
* Databricks
* Oracle
* DB2

Files:

* CSV
* Parquet

---

# End-to-End Execution Flow

```text
User
 │
 ▼
Connection Management
 │
 ▼
Connector Engine
 │
 ▼
Database/File Source
 │
 ▼
Metadata Extraction
 │
 ▼
Data Quality Validation
 │
 ▼
Report Generation
 │
 ▼
Dashboard & Notifications
```
