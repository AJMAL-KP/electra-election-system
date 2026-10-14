"""Middleware for enforcing single-active-session ownership across all HTTP requests.

Guarantees that a superseded or revoked session immediately loses access to protected resources.
"""

from django.contrib.auth import logout as auth_logout
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from accounts.models import Role
from accounts.selectors import is_user_session_active


class SessionTakeoverMiddleware:
    """Enforces server-side session ownership on every authenticated HTTP request.
    
    If the presented session has been superseded by a subsequent login or revoked,
    the request is rejected and the superseded session is terminated immediately.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            session_obj = getattr(request, 'session', None)
            session_key = getattr(session_obj, 'session_key', None) if session_obj else None

            # Verify active session ownership against server-side database
            if not session_key or not is_user_session_active(request.user, session_key):
                auth_logout(request)

                is_ajax = (
                    request.headers.get('x-requested-with') == 'XMLHttpRequest'
                    or request.content_type == 'application/json'
                    or 'application/json' in request.headers.get('accept', '')
                )
                if is_ajax:
                    return JsonResponse(
                        {
                            "success": False,
                            "error": "Session has been superseded by another active login.",
                            "superseded": True
                        },
                        status=403
                    )

                user_role = getattr(request.user, 'role', None)
                if user_role == Role.OFFICER:
                    return render(request, "voting/officer/session_revoked.html", {
                        "error_title": "Session Superseded",
                        "error_message": "This station identity has been taken over by a new login session.",
                    }, status=403)
                elif user_role == Role.KIOSK:
                    return HttpResponseForbidden("Kiosk session has been superseded by another active login.")

                login_url = reverse('accounts:login')
                return redirect(f"{login_url}?reason=session_superseded")

        return self.get_response(request)
