"""Root URL configuration for electra project."""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


from django.db import connection
from accounts.selectors import is_installation_initialized


def health_check(request):
    """Diagnostic health check endpoint for Phase 1 verification."""
    db_ok = True
    db_error = None
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    initialized = False
    if db_ok:
        try:
            initialized = is_installation_initialized()
        except Exception:
            initialized = False

    return JsonResponse({
        "status": "ok" if db_ok else "degraded",
        "system": "electra",
        "database": "connected" if db_ok else f"disconnected: {db_error}",
        "installation_initialized": initialized,
    })


from electra.views import index_view


urlpatterns = [
    path('', index_view, name='index'),
    path('admin/', admin.site.urls),
    path('health/', health_check, name='health-check'),
    path('accounts/', include('accounts.urls')),
    path('elections/', include('elections.urls')),
    path('voters/', include('voters.urls')),
    path('voting/', include('voting.urls')),
]
