from django.db import models
from django.contrib.auth.models import User


class AuditLog(models.Model):
    """Audit trail for all user actions in the system."""

    LEVEL_CHOICES = [
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('error', 'Error'),
        ('success', 'Success'),
    ]

    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='audit_logs')
    action = models.CharField(max_length=200)
    entity_type = models.CharField(max_length=100, blank=True)
    entity_id = models.IntegerField(null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default='info')
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'

    def __str__(self):
        return f"[{self.level.upper()}] {self.action} by {self.user} at {self.timestamp}"
