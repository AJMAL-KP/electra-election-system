"""Domain services for accounts application.

Owns:
- Device credentials generation, rotation, revocation
- Device session creation and enforcement
- User/role management operations
- Installation first-run initialization
"""

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from accounts.models import CredentialStatus, Device, DeviceSession, Role, User
from accounts.selectors import is_installation_initialized


def _notify_credentials_revoked(device_id: int) -> None:
    """Post-commit: broadcast credentials.revoked to the device's WS channel."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                f"kiosk_{device_id}",
                {"type": "credentials.revoked"},
            )
            async_to_sync(channel_layer.group_send)(
                f"officer_{device_id}",
                {"type": "credentials.revoked"},
            )
    except Exception:
        pass  # channel layer not configured — non-fatal


import secrets
from typing import Any, Optional, Tuple
from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from accounts.models import CredentialStatus, Device, DeviceSession, DeviceType, Role, User
from accounts.selectors import get_active_device_session, is_installation_initialized


READABLE_WORDS = [
    'amber', 'cedar', 'delta', 'eagle', 'falcon', 'harbor', 'island', 'jupiter',
    'maple', 'nexus', 'orbit', 'pacific', 'quartz', 'river', 'summit', 'tiger',
    'valley', 'zenith', 'beacon', 'canyon', 'forest', 'meadow', 'shadow', 'silver',
    'aurora', 'blaze', 'coral', 'drift', 'glade', 'haven', 'lagoon', 'oasis',
    'prism', 'ridge', 'sierra', 'tundra', 'vortex', 'willow', 'zephyr', 'zen'
]


def generate_secure_readable_password() -> str:
    """Generate a high-entropy, human-readable secure password (e.g. Falcon-482-River).
    
    Easily communicated and typed by station poll workers while maintaining strong cryptographic entropy.
    """
    w1 = secrets.choice(READABLE_WORDS).capitalize()
    w2 = secrets.choice(READABLE_WORDS).capitalize()
    num = secrets.randbelow(900) + 100  # 100-999
    return f"{w1}-{num}-{w2}"


def generate_secure_password(length: int = 14) -> str:
    """Generate a high-entropy, human-readable secure password."""
    return generate_secure_readable_password()


def initialize_installation(username: str, email: str, password: str) -> User:
    """Create the single human Administrator account during first-run setup.
    
    Transitions installation state from UNINITIALIZED -> INITIALIZED.
    Raises ValidationError if the installation is already initialized.
    """
    with transaction.atomic():
        if is_installation_initialized():
            raise ValidationError(
                "Installation is already initialized. Only one Administrator account is permitted."
            )

        admin_user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            role=Role.ADMIN,
            is_staff=True,
            is_superuser=True,
        )
        return admin_user


def purge_device_sessions(device: Device) -> int:
    """Purge all sessions (both active and revoked/old) for a technical device,
    removing matching rows from both Django's Session store and DeviceSession table
    to guarantee zero ambiguous or stale sessions remain.
    """
    from django.contrib.sessions.models import Session

    session_qs = DeviceSession.objects.filter(device=device)
    session_keys = list(session_qs.values_list("session_key", flat=True))
    if session_keys:
        Session.objects.filter(session_key__in=session_keys).delete()
    deleted_count, _ = session_qs.delete()
    return deleted_count


def purge_stale_device_sessions(device: Device) -> int:
    """Purge any inactive or expired sessions for the device."""
    from django.contrib.sessions.models import Session

    inactive_qs = DeviceSession.objects.filter(device=device, is_active=False)
    keys = list(inactive_qs.values_list("session_key", flat=True))
    if keys:
        Session.objects.filter(session_key__in=keys).delete()
    deleted_count, _ = inactive_qs.delete()
    return deleted_count


def cleanup_past_credentials() -> int:
    """Automatically remove station device credentials (User and Device records)
    that belong to a booth whose election is no longer in draft or active status.
    At any given time, device credentials that are in draft and active election state remain.
    """
    from django.contrib.sessions.models import Session
    from accounts.models import Device, DeviceSession, DeviceType, Role, User
    from elections.models import Election, ElectionStatus

    active_or_draft_election_ids = list(Election.objects.filter(
        status__in=[ElectionStatus.DRAFT, ElectionStatus.ACTIVE]
    ).values_list("id", flat=True))

    # Station devices that do not belong to an election in DRAFT or ACTIVE status
    stale_devices = Device.objects.filter(
        device_type__in=[DeviceType.OFFICER, DeviceType.KIOSK]
    ).exclude(booth__election_id__in=active_or_draft_election_ids)
    stale_user_ids = set(stale_devices.values_list("user_id", flat=True))

    deleted_count = 0
    if stale_user_ids:
        # Purge all sessions from Django session store and DeviceSession table
        stale_sessions = DeviceSession.objects.filter(device__user_id__in=stale_user_ids)
        stale_keys = list(stale_sessions.values_list("session_key", flat=True))
        if stale_keys:
            Session.objects.filter(session_key__in=stale_keys).delete()
        stale_sessions.delete()

        stale_devices.delete()
        deleted_count, _ = User.objects.filter(id__in=stale_user_ids, role__in=[Role.OFFICER, Role.KIOSK]).delete()
    return deleted_count


def purge_election_stale_credentials_and_sessions(election) -> dict:
    """For an active election:
    1. Purges all revoked/old/inactive sessions across all booths of the election.
    2. Ensures exactly one valid active credential per device.
       If any booth device was marked REVOKED, automatically rotates it to fresh ACTIVE credentials.
    3. Purges all credentials and sessions from past/closed elections or orphan devices.
    """
    from django.contrib.sessions.models import Session

    with transaction.atomic():
        booth_devices = Device.objects.filter(booth__election=election)

        # 1. Purge all inactive sessions belonging to devices in this election
        stale_sessions = DeviceSession.objects.filter(device__in=booth_devices, is_active=False)
        stale_keys = list(stale_sessions.values_list("session_key", flat=True))
        if stale_keys:
            Session.objects.filter(session_key__in=stale_keys).delete()
        stale_sessions_count = stale_sessions.delete()[0]

        # 2. Re-activate/rotate any REVOKED device in this active election so every device
        # has exactly ONE valid active credential and no ambiguity
        reactivated_count = 0
        for dev in booth_devices.filter(credential_status=CredentialStatus.REVOKED):
            rotate_device_credentials(dev.id)
            reactivated_count += 1

        # 3. Purge past credentials from non-active elections
        cleanup_past_credentials()

        return {
            "purged_sessions": stale_sessions_count,
            "reactivated_devices": reactivated_count,
        }


def create_device(
    identifier: str,
    device_type: str,
    initial_password: Optional[str] = None,
    booth: Optional[Any] = None,
) -> Tuple[Device, str]:
    """Provision a new technical device identity with autogenerated readable credentials.
    
    Returns (Device, plain_password).
    """
    if device_type not in (DeviceType.OFFICER, DeviceType.KIOSK):
        raise ValidationError(f"Invalid device type: {device_type}")

    if initial_password is None:
        initial_password = generate_secure_readable_password()

    with transaction.atomic():
        # If an orphan user with this username still exists, remove it
        orphan = User.objects.filter(username=identifier, role__in=[Role.OFFICER, Role.KIOSK]).first()
        if orphan and (not hasattr(orphan, "device") or orphan.device.booth is None):
            orphan.delete()

        if Device.objects.filter(identifier=identifier).exists() or User.objects.filter(username=identifier).exists():
            raise ValidationError(f"Device with identifier '{identifier}' already exists.")

        role = Role.OFFICER if device_type == DeviceType.OFFICER else Role.KIOSK
        user = User.objects.create_user(
            username=identifier,
            password=initial_password,
            role=role,
            is_staff=False,
            is_superuser=False
        )

        device = Device.objects.create(
            user=user,
            identifier=identifier,
            device_type=device_type,
            booth=booth,
            cleartext_password=initial_password,
            credential_status=CredentialStatus.ACTIVE,
            credential_version=1,
            last_rotated_at=timezone.now()
        )
        return device, initial_password



def rotate_device_credentials(device_id: int) -> Tuple[Device, str]:
    """Rotate credentials for a device: generates a new readable password, updates cleartext_password,
    and completely purges all old/revoked sessions for the device to eliminate ambiguity.
    
    Returns (Device, new_plain_password).
    """
    new_password = generate_secure_readable_password()
    with transaction.atomic():
        device = Device.objects.select_for_update().get(id=device_id)
        user = device.user

        # Update password securely
        user.set_password(new_password)
        user.save()

        # Update device rotation tracking & stored readable password
        device.credential_version += 1
        device.cleartext_password = new_password
        device.last_rotated_at = timezone.now()
        device.credential_status = CredentialStatus.ACTIVE
        device.save()

        # Invalidate active sessions so only a future newly authenticated session can exist
        active_sessions = DeviceSession.objects.filter(device=device, is_active=True)
        session_keys = list(active_sessions.values_list("session_key", flat=True))
        if session_keys:
            from django.contrib.sessions.models import Session
            Session.objects.filter(session_key__in=session_keys).delete()
        active_sessions.update(is_active=False, revoked_at=timezone.now())

        device_id_val = device.id
        transaction.on_commit(lambda: _notify_credentials_revoked(device_id_val))

        return device, new_password


def revoke_device_credentials(device_id: int) -> Device:
    """Revoke credentials for a device and invalidate active sessions.
    
    Emits a 'credentials.revoked' WebSocket event post-commit.
    """
    with transaction.atomic():
        device = Device.objects.select_for_update().get(id=device_id)
        device.credential_status = CredentialStatus.REVOKED
        device.save()

        active_sessions = DeviceSession.objects.filter(device=device, is_active=True)
        session_keys = list(active_sessions.values_list("session_key", flat=True))
        if session_keys:
            from django.contrib.sessions.models import Session
            Session.objects.filter(session_key__in=session_keys).delete()
        active_sessions.update(is_active=False, revoked_at=timezone.now())

        device_id_val = device.id
        transaction.on_commit(lambda: _notify_credentials_revoked(device_id_val))
        return device


def create_device_session(device: Device, session_key: str, takeover: bool = False) -> DeviceSession:
    """Establish an active session for a Device, enforcing single-active-session invariant."""
    with transaction.atomic():
        locked_device = Device.objects.select_for_update().get(id=device.id)

        if locked_device.credential_status != CredentialStatus.ACTIVE:
            raise ValidationError("Device credentials are not active.")

        active_sessions = list(DeviceSession.objects.filter(device=locked_device, is_active=True))
        if active_sessions:
            if not takeover:
                raise ValidationError(
                    f"Device '{locked_device.identifier}' already has an active session. Simultaneous sessions are prohibited."
                )
            old_keys = [s.session_key for s in active_sessions]
            from django.contrib.sessions.models import Session
            Session.objects.filter(session_key__in=old_keys).delete()
            DeviceSession.objects.filter(device=locked_device, is_active=True).update(
                is_active=False,
                revoked_at=timezone.now()
            )

        session, _ = DeviceSession.objects.update_or_create(
            session_key=session_key,
            defaults={
                "device": locked_device,
                "is_active": True,
                "revoked_at": None,
            }
        )
        return session


def terminate_device_session(session_key: str) -> None:
    """Terminate and mark an active device session as inactive."""
    with transaction.atomic():
        from django.contrib.sessions.models import Session
        Session.objects.filter(session_key=session_key).delete()
        DeviceSession.objects.filter(session_key=session_key).update(
            is_active=False,
            revoked_at=timezone.now()
        )
        def _notify_admin_live():
            try:
                from asgiref.sync import async_to_sync
                from channels.layers import get_channel_layer
                layer = get_channel_layer()
                if layer:
                    async_to_sync(layer.group_send)("admin_live", {"type": "review.refresh"})
            except Exception:
                pass
        transaction.on_commit(_notify_admin_live)


def login_device_user(request, user: User) -> None:
    """Authenticate and log in a User, enforcing single-active-session takeover."""
    from accounts.models import AdminSession
    from django.contrib.sessions.models import Session

    if user.role == Role.ADMIN:
        with transaction.atomic():
            locked_user = User.objects.select_for_update().get(id=user.id)

            # Revoke any previous active session(s) belonging to this Administrator
            old_admin_sessions = list(AdminSession.objects.filter(user=locked_user, is_active=True))
            old_keys = [s.session_key for s in old_admin_sessions]
            if old_keys:
                Session.objects.filter(session_key__in=old_keys).delete()
                AdminSession.objects.filter(user=locked_user, is_active=True).update(
                    is_active=False,
                    revoked_at=timezone.now()
                )

            # Establish the new session
            login(request, user)
            if not request.session.session_key:
                request.session.save()

            AdminSession.objects.update_or_create(
                session_key=request.session.session_key,
                defaults={
                    "user": locked_user,
                    "is_active": True,
                    "revoked_at": None,
                }
            )

            new_key = request.session.session_key

            def _notify_admin_superseded():
                try:
                    from asgiref.sync import async_to_sync
                    from channels.layers import get_channel_layer
                    layer = get_channel_layer()
                    if layer:
                        async_to_sync(layer.group_send)(
                            "admin_live",
                            {
                                "type": "session.superseded",
                                "new_session_key": new_key,
                            }
                        )
                except Exception:
                    pass

            transaction.on_commit(_notify_admin_superseded)
            return

    # For technical device identities (OFFICER, KIOSK)
    try:
        device = user.device
    except (AttributeError, Device.DoesNotExist):
        device = Device.objects.filter(identifier=user.username).first()

    if not device:
        raise ValidationError("No associated device found for this identity.")

    if device.credential_status != CredentialStatus.ACTIVE:
        raise ValidationError("Device credentials have been revoked. Contact the Administrator.")

    booth = getattr(device, "booth", None)
    if booth and getattr(booth, "election", None):
        from elections.models import ElectionStatus
        if booth.election.status not in (ElectionStatus.DRAFT, ElectionStatus.ACTIVE):
            raise ValidationError("Station credentials are only valid for draft or active elections.")

    with transaction.atomic():
        locked_device = Device.objects.select_for_update().get(id=device.id)
        if locked_device.credential_status != CredentialStatus.ACTIVE:
            raise ValidationError("Device credentials have been revoked.")

        # Revoke any previous active session(s) belonging to this logical device
        old_device_sessions = list(DeviceSession.objects.filter(device=locked_device, is_active=True))
        old_keys = [s.session_key for s in old_device_sessions]
        if old_keys:
            Session.objects.filter(session_key__in=old_keys).delete()
            DeviceSession.objects.filter(device=locked_device, is_active=True).update(
                is_active=False,
                revoked_at=timezone.now()
            )

        # Login with Django session
        login(request, user)
        if not request.session.session_key:
            request.session.save()

        DeviceSession.objects.update_or_create(
            session_key=request.session.session_key,
            defaults={
                "device": locked_device,
                "is_active": True,
                "revoked_at": None,
            }
        )

        new_key = request.session.session_key
        device_id = locked_device.id
        device_type = locked_device.device_type
        election_id = locked_device.booth.election_id if locked_device.booth else None

        def _notify_takeover():
            try:
                from asgiref.sync import async_to_sync
                from channels.layers import get_channel_layer
                layer = get_channel_layer()
                if not layer:
                    return

                # Notify superseded sessions over WebSocket
                group_name = f"kiosk_{device_id}" if device_type == DeviceType.KIOSK else f"officer_{device_id}"
                async_to_sync(layer.group_send)(
                    group_name,
                    {
                        "type": "session.superseded",
                        "new_session_key": new_key,
                    }
                )

                # Broadcast review.refresh to admin live dashboard
                async_to_sync(layer.group_send)(
                    "admin_live",
                    {
                        "type": "review.refresh",
                        "election_id": election_id,
                    }
                )
            except Exception:
                pass

        transaction.on_commit(_notify_takeover)


def logout_user(request) -> None:
    """Log out user and terminate only the specific session matching this request."""
    if request.user.is_authenticated:
        session_key = getattr(getattr(request, "session", None), "session_key", None)
        if getattr(request.user, "role", None) == Role.ADMIN:
            if session_key:
                from django.contrib.sessions.models import Session
                Session.objects.filter(session_key=session_key).delete()
                from accounts.models import AdminSession
                AdminSession.objects.filter(session_key=session_key).update(
                    is_active=False,
                    revoked_at=timezone.now()
                )
        elif hasattr(request.user, 'device'):
            if session_key:
                terminate_device_session(session_key)
    logout(request)



def get_post_login_redirect_url(user: User) -> str:
    """Return the canonical role-based dashboard destination URL."""
    from django.urls import reverse
    if getattr(user, "role", None) == Role.ADMIN:
        return reverse("elections:dashboard")
    elif getattr(user, "role", None) == Role.OFFICER:
        return reverse("voting:officer_dashboard")
    elif getattr(user, "role", None) == Role.KIOSK:
        return reverse("voting:kiosk")
    return reverse("index")


