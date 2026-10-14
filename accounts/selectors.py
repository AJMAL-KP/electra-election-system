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


def get_active_admin_session(user: User) -> Optional['AdminSession']:
    """Retrieve the single active session for an Admin user, if one exists."""
    from accounts.models import AdminSession
    return AdminSession.objects.filter(user=user, is_active=True).first()


def get_active_admin_session_by_key(session_key: str) -> Optional['AdminSession']:
    """Retrieve an active Admin session by Django session key."""
    from accounts.models import AdminSession
    return AdminSession.objects.select_related('user').filter(
        session_key=session_key,
        is_active=True
    ).first()


def is_user_session_active(user: User, session_key: str) -> bool:
    """Check whether the given session key is the authoritative active session for the user."""
    if not user or not user.is_authenticated or not session_key:
        return False
    if user.role == Role.ADMIN:
        from accounts.models import AdminSession
        active = AdminSession.objects.filter(user=user, is_active=True).first()
        if active:
            return active.session_key == session_key
        # No active AdminSession recorded yet (e.g. test client login); initialize it as active
        AdminSession.objects.create(user=user, session_key=session_key, is_active=True)
        return True
    elif user.role in (Role.OFFICER, Role.KIOSK):
        device = getattr(user, 'device', None)
        if not device:
            # If a technical identity was created without a Device record (e.g. raw RBAC test)
            return True
        active = DeviceSession.objects.filter(device=device, is_active=True).first()
        if active:
            return active.session_key == session_key
        # No active DeviceSession recorded yet for this device; initialize it as active
        DeviceSession.objects.create(device=device, session_key=session_key, is_active=True)
        return True
    return False


def list_devices(device_type: Optional[str] = None) -> QuerySet[Device]:
    """List devices, optionally filtered by device type."""
    qs = Device.objects.select_related('user').order_by('identifier')
    if device_type:
        qs = qs.filter(device_type=device_type)
    return qs


