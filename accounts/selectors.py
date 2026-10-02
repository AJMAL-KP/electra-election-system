"""Query selectors for accounts application.

Owns:
- Read-only query helpers for devices, sessions, and roles
- Dynamic administrator resolution
"""

from typing import Optional
from django.db.models import QuerySet
from accounts.models import CredentialStatus, Device, DeviceSession, DeviceType, Role, User


def get_administrator() -> Optional[User]:
    """Dynamically resolve the single human Administrator for the installation.
    
    Never hardcode usernames ('admin') or IDs (1).
    """
    return User.objects.filter(role=Role.ADMIN).first()


def is_installation_initialized() -> bool:
    """Check whether the installation has been initialized with an Administrator account."""
    return User.objects.filter(role=Role.ADMIN).exists()


def get_device_by_user(user: User) -> Optional[Device]:
    """Retrieve the Device record linked to a User identity."""
    try:
        return user.device
    except (AttributeError, Device.DoesNotExist):
        return None


def get_device_by_identifier(identifier: str) -> Optional[Device]:
    """Retrieve a device by its stable logical identifier."""
    return Device.objects.filter(identifier=identifier).first()


def get_active_device_session(device: Device) -> Optional[DeviceSession]:
    """Retrieve the single active session for a device, if one exists."""
    return DeviceSession.objects.filter(device=device, is_active=True).first()


def get_active_session_by_key(session_key: str) -> Optional[DeviceSession]:
    """Retrieve an active device session by Django session key."""
    return DeviceSession.objects.select_related('device', 'device__user').filter(
        session_key=session_key,
        is_active=True
    ).first()


def list_devices(device_type: Optional[str] = None) -> QuerySet[Device]:
    """List devices, optionally filtered by device type."""
    qs = Device.objects.select_related('user').order_by('identifier')
    if device_type:
        qs = qs.filter(device_type=device_type)
    return qs

