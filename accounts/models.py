from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    ADMIN = 'ADMIN', 'Administrator'
    OFFICER = 'OFFICER', 'Officer Station'
    KIOSK = 'KIOSK', 'Voting Kiosk'


class DeviceType(models.TextChoices):
    OFFICER = 'OFFICER', 'Officer Station'
    KIOSK = 'KIOSK', 'Voting Kiosk'


class CredentialStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active'
    REVOKED = 'REVOKED', 'Revoked'


class User(AbstractUser):
    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.ADMIN,
        help_text="Role of the authenticated identity."
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['role'],
                condition=models.Q(role='ADMIN'),
                name='unique_single_installation_admin'
            )
        ]

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"


class Device(models.Model):
    """Represents a logical Officer Station or Voting Kiosk technical identity.
    
    A Device is a technical identity bound to a physical booth, rather than a specific physical computer.
    Credentials can be rotated without changing the stable identifier.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='device',
        help_text="Underlying authentication identity."
    )
    identifier = models.CharField(
        max_length=100,
        unique=True,
        help_text="Stable logical device identifier (e.g. booth-1-officer, booth-1-kiosk)."
    )
    device_type = models.CharField(
        max_length=10,
        choices=DeviceType.choices,
        help_text="Type of station device."
    )
    credential_status = models.CharField(
        max_length=10,
        choices=CredentialStatus.choices,
        default=CredentialStatus.ACTIVE,
        help_text="Status of credentials (ACTIVE or REVOKED)."
    )
    credential_version = models.PositiveIntegerField(
        default=1,
        help_text="Incremented on each credential rotation."
    )
    last_rotated_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when credentials were last generated or rotated."
    )
    booth = models.ForeignKey(
        'voters.Booth',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='devices',
        help_text="Physical booth this device is bound to."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['booth', 'device_type'],
                condition=models.Q(booth__isnull=False),
                name='unique_device_per_booth'
            )
        ]

    def __str__(self):
        return f"{self.identifier} ({self.get_device_type_display()}) [{self.credential_status}]"


class DeviceSession(models.Model):
    """Tracks active authenticated session for a technical device.
    
    Enforces the single active session invariant per Device.
    """
    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
        related_name='sessions'
    )
    session_key = models.CharField(
        max_length=40,
        unique=True,
        help_text="Django session key."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this session is currently active."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['device'],
                condition=models.Q(is_active=True),
                name='unique_active_session_per_device'
            )
        ]

    def __str__(self):
        status = "Active" if self.is_active else "Terminated"
        return f"Session {self.session_key[:8]}... on {self.device.identifier} ({status})"

