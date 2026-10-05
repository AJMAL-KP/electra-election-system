"""Role-based access control and session verification decorators for accounts app."""
from functools import wraps
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from accounts.models import CredentialStatus, Role
from accounts.selectors import get_active_session_by_key


def admin_required(view_func):
    """Ensure the authenticated user has the ADMIN role."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        if request.user.role != Role.ADMIN:
            raise PermissionDenied("Administrative privileges required.")
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def officer_required(view_func):
    """Ensure the authenticated user is an Officer Station with an active session."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        if request.user.role != Role.OFFICER:
            raise PermissionDenied("Officer Station access required.")

        # Verify active device session and credential status
        session_obj = getattr(request, "session", None)
        session_key = getattr(session_obj, "session_key", None) if session_obj else None
        if not session_key:
            return redirect('accounts:login')

        active_session = get_active_session_by_key(session_key)
        if not active_session or active_session.device.credential_status != CredentialStatus.ACTIVE:
            if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
                return HttpResponseForbidden("Device session is inactive or credentials have been revoked.")
            return render(request, "voting/officer/session_revoked.html", {
                "error_title": "Session Inactive or Revoked",
                "error_message": "This station session is inactive or credentials have been revoked.",
            }, status=403)

        device = getattr(request.user, "device", None)
        if not device:
            if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
                return HttpResponseForbidden("Device credentials are no longer valid.")
            return render(request, "voting/officer/session_revoked.html", {
                "error_title": "Device Invalid",
                "error_message": "Device credentials are no longer valid for this station.",
            }, status=403)

        request.device = device
        request.booth = device.booth

        return view_func(request, *args, **kwargs)
    return _wrapped_view


def kiosk_required(view_func):
    """Ensure the authenticated user is a Voting Kiosk with an active session."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        if request.user.role != Role.KIOSK:
            raise PermissionDenied("Voting Kiosk access required.")

        session_obj = getattr(request, "session", None)
        session_key = getattr(session_obj, "session_key", None) if session_obj else None
        if not session_key:
            return redirect('accounts:login')

        active_session = get_active_session_by_key(session_key)
        if not active_session or active_session.device.credential_status != CredentialStatus.ACTIVE:
            return HttpResponseForbidden("Kiosk session is inactive or credentials have been revoked.")

        device = getattr(request.user, "device", None)
        if device:
            request.device = device
            request.booth = device.booth

        return view_func(request, *args, **kwargs)
    return _wrapped_view
