"""
Migration: Create hwm_chk trigger tracking table.
This table is used by the DB Trigger scheduler to detect when a workflow
should be executed based on an ETL flag set by upstream processes.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0003_alter_workflow_description_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            # ── Forward SQL ───────────────────────────────────────────────────
            sql=[
                """
                CREATE TABLE IF NOT EXISTS hwm_chk (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger_name VARCHAR(255) NOT NULL,
                    hwm_flag     CHAR(1)      NOT NULL DEFAULT 'N',
                    etl_date     DATE         NOT NULL,
                    created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
                );
                """,
                "CREATE INDEX IF NOT EXISTS idx_hwm_trigger_name ON hwm_chk(trigger_name);",
                "CREATE INDEX IF NOT EXISTS idx_hwm_etl_date     ON hwm_chk(etl_date);",
                "CREATE INDEX IF NOT EXISTS idx_hwm_trigger_date ON hwm_chk(trigger_name, etl_date);",
            ],
            # ── Reverse SQL ───────────────────────────────────────────────────
            reverse_sql=[
                "DROP INDEX IF EXISTS idx_hwm_trigger_date;",
                "DROP INDEX IF EXISTS idx_hwm_etl_date;",
                "DROP INDEX IF EXISTS idx_hwm_trigger_name;",
                "DROP TABLE IF EXISTS hwm_chk;",
            ],
            hints={'target_db': 'default'},
        ),
    ]
