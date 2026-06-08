"""
Migration: Add DB Trigger fields to Workflow model.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0004_create_hwm_chk_table'),
    ]

    operations = [
        # Add db_trigger to SCHEDULE_TYPES choices
        migrations.AlterField(
            model_name='workflow',
            name='schedule_type',
            field=models.CharField(
                choices=[
                    ('manual', 'On Demand'),
                    ('daily', 'Daily'),
                    ('weekly', 'Weekly'),
                    ('monthly', 'Monthly'),
                    ('db_trigger', 'DB Trigger'),
                ],
                default='manual',
                max_length=20,
            ),
        ),
        # New trigger-specific fields
        migrations.AddField(
            model_name='workflow',
            name='trigger_name',
            field=models.CharField(
                blank=True, max_length=255,
                help_text='Name to match in hwm_chk.trigger_name'
            ),
        ),
        migrations.AddField(
            model_name='workflow',
            name='trigger_scheduled_time',
            field=models.TimeField(
                null=True, blank=True,
                help_text='Time of day to start polling the hwm_chk table'
            ),
        ),
        migrations.AddField(
            model_name='workflow',
            name='poll_duration_hours',
            field=models.IntegerField(
                default=3,
                help_text='Number of hours to poll before declaring a timeout'
            ),
        ),
        migrations.AddField(
            model_name='workflow',
            name='trigger_status',
            field=models.CharField(
                choices=[
                    ('idle', 'Idle'),
                    ('polling', 'Polling'),
                    ('triggered', 'Triggered'),
                    ('timeout', 'Trigger Timeout'),
                    ('error', 'Error'),
                ],
                default='idle', max_length=20,
                help_text='Current DB Trigger polling status'
            ),
        ),
        migrations.AddField(
            model_name='workflow',
            name='trigger_last_polled',
            field=models.DateTimeField(
                null=True, blank=True,
                help_text='Timestamp of last poll attempt'
            ),
        ),
        migrations.AddField(
            model_name='workflow',
            name='trigger_fired_at',
            field=models.DateTimeField(
                null=True, blank=True,
                help_text='Timestamp when trigger was found and workflow started'
            ),
        ),
    ]
