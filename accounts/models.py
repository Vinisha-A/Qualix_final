from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


class UserProfile(models.Model):
    """Extended user profile with role-based access control."""

    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('contributor', 'Contributor'),
        ('auditor', 'Auditor'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='contributor')
    department = models.CharField(max_length=100, blank=True)
    employee_id = models.CharField(max_length=50, blank=True)
    is_ldap_user = models.BooleanField(default=False, help_text='User authenticated via Active Directory')
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='created_users'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    @property
    def is_admin(self):
        return self.role == 'admin'

    @property
    def is_contributor(self):
        return self.role == 'contributor'

    @property
    def is_auditor(self):
        return self.role == 'auditor'

    @property
    def can_manage_users(self):
        return self.role == 'admin'

    @property
    def can_create_mappings(self):
        return self.role in ('admin', 'contributor')

    @property
    def can_trigger_validations(self):
        return self.role in ('admin', 'contributor')


@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    """Auto-create a UserProfile when a new User is created."""
    if created:
        # Check if profile already exists (e.g., from LDAP sync)
        if not hasattr(instance, 'profile'):
            UserProfile.objects.create(user=instance)
    else:
        if hasattr(instance, 'profile'):
            instance.profile.save()
