from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from accounts.selectors import is_installation_initialized
from accounts.services import get_post_login_redirect_url


@never_cache
def index_view(request):
    """System overview landing page. Authenticated users are routed to their role dashboard."""
    if request.user.is_authenticated:
        return redirect(get_post_login_redirect_url(request.user))

    initialized = is_installation_initialized()
    return render(request, "index.html", {
        "is_initialized": initialized,
        "is_authenticated": False,
    })

