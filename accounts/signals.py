"""Signal handlers for accounts session lifecycle and takeover synchronization."""

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.utils import timezone
from accounts.models import AdminSession, DeviceSession, Role


@receiver(user_logged_in)
def handle_user_logged_in(sender, request, user, **kwargs):
    """Enforce single active session record upon successful user login."""
    if not request or not hasattr(request, "session"):
        return
    session_key = getattr(request.session, "session_key", None)
    if not session_key:
        try:
            request.session.save()
            session_key = request.session.session_key
        except Exception:
            return
    if not session_key:
        return

    if getattr(user, "role", None) == Role.ADMIN:
        # Check if already registered
        if AdminSession.objects.filter(user=user, session_key=session_key, is_active=True).exists():
            return
        # Deactivate previous active sessions for this Admin identity
        AdminSession.objects.filter(user=user, is_active=True).exclude(session_key=session_key).update(
            is_active=False,
            revoked_at=timezone.now()
        )
        AdminSession.objects.update_or_create(
            session_key=session_key,
            defaults={
                "user": user,
                "is_active": True,
                "revoked_at": None,
            }
        )
    elif getattr(user, "role", None) in (Role.OFFICER, Role.KIOSK):
        device = getattr(user, "device", None)
        if device:
            if DeviceSession.objects.filter(device=device, session_key=session_key, is_active=True).exists():
                return
            # Deactivate previous active sessions for this Device identity
            DeviceSession.objects.filter(device=device, is_active=True).exclude(session_key=session_key).update(
                is_active=False,
                revoked_at=timezone.now()
            )
            DeviceSession.objects.update_or_create(
                session_key=session_key,
                defaults={
                    "device": device,
                    "is_active": True,
                    "revoked_at": None,
                }
            )


@receiver(user_logged_out)
def handle_user_logged_out(sender, request, user, **kwargs):
    """Mark session inactive upon user logout."""
    if not request or not hasattr(request, "session"):
        return
    session_key = getattr(request.session, "session_key", None)
    if not session_key:
        return

    if getattr(user, "role", None) == Role.ADMIN:
        AdminSession.objects.filter(session_key=session_key).update(
            is_active=False,
            revoked_at=timezone.now()
        )
    elif getattr(user, "role", None) in (Role.OFFICER, Role.KIOSK):
        DeviceSession.objects.filter(session_key=session_key).update(
            is_active=False,
            revoked_at=timezone.now()
        )
