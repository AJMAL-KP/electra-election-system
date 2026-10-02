"""Views for accounts application (Setup, Login, Logout)."""
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from accounts.models import Role
from accounts.selectors import is_installation_initialized
from accounts.services import initialize_installation, login_device_user, logout_user


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
        if not username:
            errors.append("Administrator username is required.")
        if not password:
            errors.append("Password is required.")
        elif len(password) < 8:
            errors.append("Password must be at least 8 characters long.")
        if password != confirm_password:
            errors.append("Passwords do not match.")

        if errors:
            return render(request, "accounts/setup.html", {
                "errors": errors,
                "username": username,
                "email": email,
            }, status=400)

        try:
            initialize_installation(username=username, email=email, password=password)
            # Per docs/05_STATE_MACHINES.md: Redirect to Administrator Login upon creation
            return redirect("accounts:login")
        except ValidationError as exc:
            return render(request, "accounts/setup.html", {
                "errors": exc.messages if hasattr(exc, "messages") else [str(exc)],
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
        if getattr(request.user, "role", None) == Role.ADMIN:
            return redirect("elections:dashboard")
        return redirect("index")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        if not username or not password:
            return render(request, "accounts/login.html", {
                "error": "Please enter both identifier and password.",
                "username": username,
            }, status=400)

        user = authenticate(request, username=username, password=password)
        if user is None:
            return render(request, "accounts/login.html", {
                "error": "Invalid identifier or password.",
                "username": username,
            }, status=401)

        try:
            login_device_user(request, user)
            if user.role == Role.ADMIN:
                return redirect("elections:dashboard")
            return redirect("index")
        except ValidationError as exc:
            return render(request, "accounts/login.html", {
                "error": exc.message if hasattr(exc, "message") else str(exc),
                "username": username,
            }, status=403)

    return render(request, "accounts/login.html")


@never_cache
def logout_view(request):
    """Log out current identity and terminate any active device session."""
    logout_user(request)
    return redirect("index")
