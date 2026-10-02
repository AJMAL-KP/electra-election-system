from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from accounts.models import Role
from accounts.selectors import is_installation_initialized


@never_cache
def index_view(request):
    """System overview landing page. Authenticated users are routed to their dashboard."""
    if request.user.is_authenticated:
        if getattr(request.user, "role", None) == Role.ADMIN:
            return redirect("elections:dashboard")
        return redirect("elections:dashboard")

    initialized = is_installation_initialized()
    return render(request, "index.html", {
        "is_initialized": initialized,
        "is_authenticated": False,
    })
