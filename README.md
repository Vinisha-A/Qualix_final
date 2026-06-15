# Qualix Data Quality Platform

## Overview

Qualix is an enterprise Data Quality and Validation Platform that enables users to connect to multiple data sources, configure validation rules, execute quality checks, and monitor results through dashboards and reports.

Supported Sources:

* Databricks
* PostgreSQL
* MySQL
* Oracle
* DB2
* CSV Files
* Parquet Files

---

# System Architecture

## Core Components

### 1. User Interface Layer

Provides web-based access for:

* Connection Management
* Rule Configuration
* Validation Execution
* Dashboard Monitoring
* Report Viewing

---

### 2. Connection Management Layer

Responsible for:

* Creating Connections
* Managing Credentials
* Testing Connectivity
* Metadata Discovery

Functions:

* Connection Configuration
* Schema Retrieval
* Table Discovery
* Column Discovery

---

### 3. Validation Engine

Responsible for:

* Rule Execution
* Data Quality Checks
* Schema Validation
* Data Validation
* Result Generation

Validations include:

* Null Checks
* Duplicate Checks
* Row Count Validation
* Schema Comparison
* Custom Business Rules

---

### 4. Workflow Orchestration Layer

Responsible for:

* Job Scheduling
* Workflow Execution
* Trigger Management
* Task Coordination

---

### 5. Reporting & Dashboard Layer

Provides:

* Quality Metrics
* Validation Results
* Historical Trends
* Execution Monitoring
* Alerts & Notifications

---

### 6. Data Source Layer

External systems connected to Qualix:

* Databricks SQL Warehouse
* PostgreSQL
* MySQL
* Oracle
* DB2
* CSV Files
* Parquet Files

---

### 7. Platform Storage Layer

Stores:

* User Information
* Connection Configurations
* Validation Rules
* Execution History
* Reports
* Audit Logs

---

# End-to-End Flow

```text
User
 │
 ▼
Web Portal
 │
 ▼
Connection Management
 │
 ▼
Metadata Discovery
 │
 ▼
Rule Configuration
 │
 ▼
Validation Engine
 │
 ▼
Workflow Orchestration
 │
 ▼
Report Generation
 │
 ▼
Dashboard & Monitoring
 │
 ▼
Platform Database
```

---

# Architecture Diagram Definition

```text
┌──────────────────────────────┐
│           USERS              │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│      WEB APPLICATION         │
│  Connections • Rules • UI    │
└──────────────┬───────────────┘
               │
      ┌────────┴────────┐
      ▼                 ▼
┌──────────────┐ ┌──────────────┐
│ CONNECTION   │ │ VALIDATION   │
│ MANAGEMENT   │ │ ENGINE       │
└──────┬───────┘ └──────┬───────┘
       │                │
       ▼                ▼
┌──────────────────────────────┐
│   WORKFLOW ORCHESTRATION     │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ REPORTING & DASHBOARD        │
└──────────────┬───────────────┘
               │
      ┌────────┴────────┐
      ▼                 ▼
┌──────────────┐ ┌──────────────┐
│ PLATFORM DB  │ │ DATA SOURCES │
│              │ │ Databricks   │
│ Users        │ │ PostgreSQL   │
│ Rules        │ │ MySQL        │
│ Reports      │ │ Oracle       │
│ Audit Logs   │ │ DB2          │
└──────────────┘ └──────────────┘
```
