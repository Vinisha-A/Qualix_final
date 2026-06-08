from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MaxLengthValidator
from mappings.models import Mapping


class Workflow(models.Model):
    """Scheduled workflow that automatically runs validations."""

    SCHEDULE_TYPES = [
        ('manual', 'On Demand'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('db_trigger', 'DB Trigger'),
    ]

    TRIGGER_STATUS_CHOICES = [
        ('idle', 'Idle'),
        ('polling', 'Polling'),
        ('triggered', 'Triggered'),
        ('timeout', 'Trigger Timeout'),
        ('error', 'Error'),
    ]

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, validators=[MaxLengthValidator(1000)])
    mapping = models.ForeignKey(Mapping, on_delete=models.CASCADE, related_name='workflows')

    schedule_type = models.CharField(max_length=20, choices=SCHEDULE_TYPES, default='manual')
    schedule_time = models.TimeField(null=True, blank=True, help_text='Time of day to run (for daily/weekly/monthly)')
    schedule_day = models.IntegerField(
        null=True, blank=True,
        help_text='Day of week (0=Mon, 6=Sun) for weekly; Day of month for monthly'
    )
    cron_expression = models.CharField(max_length=100, blank=True, help_text='Custom cron expression (5 fields)')

    # ── DB Trigger fields ─────────────────────────────────────────────────────
    trigger_name = models.CharField(
        max_length=255, blank=True,
        help_text='Name to match in hwm_chk.trigger_name'
    )
    trigger_scheduled_time = models.TimeField(
        null=True, blank=True,
        help_text='Time of day to start polling the hwm_chk table'
    )
    poll_duration_hours = models.IntegerField(
        default=3,
        help_text='Number of hours to poll before declaring a timeout'
    )
    trigger_status = models.CharField(
        max_length=20, choices=TRIGGER_STATUS_CHOICES, default='idle',
        help_text='Current DB Trigger polling status'
    )
    trigger_last_polled = models.DateTimeField(
        null=True, blank=True,
        help_text='Timestamp of last poll attempt'
    )
    trigger_fired_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Timestamp when trigger was found and workflow started'
    )

    is_active = models.BooleanField(default=True)
    celery_task_name = models.CharField(max_length=200, blank=True)
    selected_columns = models.TextField(blank=True, help_text='Comma-separated columns to validate. Leave empty for all.')

    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='workflows')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_run = models.DateTimeField(null=True, blank=True)
    next_run = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Workflow'

    def __str__(self):
        return f"{self.name} ({self.get_schedule_type_display()})"

    @property
    def is_db_trigger(self):
        return self.schedule_type == 'db_trigger'

    @property
    def trigger_status_color(self):
        return {
            'idle': 'secondary',
            'polling': 'primary',
            'triggered': 'success',
            'timeout': 'warning',
            'error': 'danger',
        }.get(self.trigger_status, 'secondary')
