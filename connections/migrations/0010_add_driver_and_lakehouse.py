# Generated migration — adds driver field and lakehouse connection type

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('connections', '0009_alter_dataconnection_connection_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='dataconnection',
            name='driver',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Optional DBAPI driver override (e.g. cx_Oracle, oracledb, pyodbc)',
                max_length=100,
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='dataconnection',
            name='connection_type',
            field=models.CharField(
                choices=[
                    ('postgresql', 'PostgreSQL'),
                    ('mysql', 'MySQL'),
                    ('databricks', 'Databricks'),
                    ('db2', 'DB2'),
                    ('oracle', 'Oracle'),
                    ('lakehouse', 'Lakehouse'),
                    ('csv', 'Flat File (CSV)'),
                    ('parquet', 'Flat File (Parquet)'),
                    ('excel', 'Flat File (Excel)'),
                    ('text', 'Flat File (Text)'),
                ],
                max_length=20,
            ),
        ),
    ]
