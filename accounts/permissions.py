"""Role-based access control and session verification decorators for accounts app."""
from functools import wraps
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
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
        session_key = request.session.session_key
        if not session_key:
            return redirect('accounts:login')

        active_session = get_active_session_by_key(session_key)
        if not active_session or active_session.device.credential_status != CredentialStatus.ACTIVE:
            return HttpResponseForbidden("Device session is inactive or credentials have been revoked.")

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

        session_key = request.session.session_key
        if not session_key:
            return redirect('accounts:login')

        active_session = get_active_session_by_key(session_key)
        if not active_session or active_session.device.credential_status != CredentialStatus.ACTIVE:
            return HttpResponseForbidden("Kiosk session is inactive or credentials have been revoked.")

        return view_func(request, *args, **kwargs)
    return _wrapped_view
