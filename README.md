# Data Quality Platform

## Architecture

The platform enables users to connect to enterprise data sources, perform data quality validations, and monitor results.

### Components

#### User Interface
- Connection Management
- Validation Configuration
- Dashboard
- Reporting

#### Connection Manager
- Create/Edit Connections
- Store Credentials
- Test Connectivity
- Metadata Discovery

#### Connector Engine
- Build Connection Strings
- Create Database Sessions
- Retrieve Schemas
- Retrieve Tables
- Retrieve Columns

#### Data Sources
- Databricks
- PostgreSQL
- MySQL
- Oracle
- DB2
- CSV Files
- Parquet Files

#### Data Quality Engine
- Data Profiling
- Schema Validation
- Null Checks
- Duplicate Checks
- Custom Rules
- Metrics Calculation

#### Reporting Layer
- Quality Reports
- Dashboard
- Alerts
- Notifications

## Flow

User → Web Application → Connection Manager → Connector Engine → Data Sources → Data Quality Engine → Reports & Dashboard
