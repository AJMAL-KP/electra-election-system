from django.contrib import messages
from django.contrib.auth import authenticate, update_session_auth_hash
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from accounts.models import Role
from accounts.permissions import admin_required
from accounts.selectors import is_installation_initialized
from accounts.services import (
    cleanup_past_credentials,
    get_post_login_redirect_url,
    initialize_installation,
    login_device_user,
    logout_user,
)


@never_cache
@require_http_methods(["GET", "POST"])
def setup_view(request):
    """First-run installation setup view.
    
    Creates the single human Administrator account.
    Permanently locked once the installation is INITIALIZED (redirects to login).
    """
    if is_installation_initialized():
        return redirect("accounts:login")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")

        errors = []
        field_errors = {}
        if not username:
            errors.append("Administrator username is required.")
            field_errors["username"] = "Administrator username is required."
        if not password:
            errors.append("Password is required.")
            field_errors["password"] = "Password is required."
        elif len(password) < 8:
            errors.append("Password must be at least 8 characters long.")
            field_errors["password"] = "Password must be at least 8 characters long."
        if password != confirm_password:
            errors.append("Passwords do not match.")
            field_errors["confirm_password"] = "Passwords do not match."

        if errors:
            return render(request, "accounts/setup.html", {
                "errors": errors,
                "field_errors": field_errors,
                "username": username,
                "email": email,
            }, status=400)

        try:
            initialize_installation(username=username, email=email, password=password)
            # Per docs/05_STATE_MACHINES.md: Redirect to Administrator Login upon creation
            return redirect("accounts:login")
        except ValidationError as exc:
            val_errors = exc.messages if hasattr(exc, "messages") else [str(exc)]
            for msg in val_errors:
                msg_lower = msg.lower()
                if "username" in msg_lower and "username" not in field_errors:
                    field_errors["username"] = msg
                elif "password" in msg_lower and "password" not in field_errors:
                    field_errors["password"] = msg
                elif "email" in msg_lower and "email" not in field_errors:
                    field_errors["email"] = msg
                else:
                    field_errors.setdefault("non_field", []).append(msg)
            return render(request, "accounts/setup.html", {
                "errors": val_errors,
                "field_errors": field_errors,
                "username": username,
                "email": email,
            }, status=400)

    return render(request, "accounts/setup.html")


@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request):
    """Unified login view for Administrator, Officer Stations, and Voting Kiosks."""
    if not is_installation_initialized():
        return redirect("accounts:setup")

    if request.user.is_authenticated:
        return redirect(get_post_login_redirect_url(request.user))

    if request.method == "POST":
        # Automatically clean up non-active/draft device credentials and their sessions
        cleanup_past_credentials()

        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        field_errors = {}
        if not username or not password:
            if not username:
                field_errors["username"] = "Please enter your username."
            if not password:
                field_errors["password"] = "Please enter your password."
            return render(request, "accounts/login.html", {
                "error": "Please enter both identifier and password.",
                "field_errors": field_errors,
                "username": username,
            }, status=400)

        user = authenticate(request, username=username, password=password)
        if user is None:
            field_errors["password"] = "Invalid identifier or password."
            return render(request, "accounts/login.html", {
                "error": "Invalid identifier or password.",
                "field_errors": field_errors,
                "username": username,
            }, status=401)

        try:
            login_device_user(request, user)
            return redirect(get_post_login_redirect_url(user))
        except ValidationError as exc:
            err_msg = exc.message if hasattr(exc, "message") else str(exc)
            field_errors["password"] = err_msg
            return render(request, "accounts/login.html", {
                "error": err_msg,
                "field_errors": field_errors,
                "username": username,
            }, status=403)

    return render(request, "accounts/login.html")


@never_cache
def logout_view(request):
    """Log out current identity and terminate any active device session."""
    logout_user(request)
    return redirect("index")


@never_cache
@admin_required
@require_http_methods(["POST"])
def change_password_view(request):
    """Update administrator password securely."""
    current_password = request.POST.get("current_password", "")
    new_password = request.POST.get("new_password", "")
    confirm_password = request.POST.get("confirm_password", "")

    if not request.user.check_password(current_password):
        messages.error(request, "Current password is incorrect.", extra_tags="password_modal")
        return redirect("elections:dashboard")

    if len(new_password) < 8:
        messages.error(request, "New password must be at least 8 characters.", extra_tags="password_modal")
        return redirect("elections:dashboard")

    if new_password != confirm_password:
        messages.error(request, "New passwords do not match.", extra_tags="password_modal")
        return redirect("elections:dashboard")

    request.user.set_password(new_password)
    request.user.save()
    update_session_auth_hash(request, request.user)
    messages.success(request, "Administrator credentials successfully updated.")
    return redirect("elections:dashboard")
