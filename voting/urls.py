"""URL patterns for voting application."""
from django.urls import path
from django.views.generic.base import RedirectView
from voting.views import (
    kiosk_view,
    officer_authorize_view,
    officer_cancel_authorization_view,
    officer_dashboard_view,
)

app_name = "voting"

urlpatterns = [
    path("officer/", officer_dashboard_view, name="officer_dashboard"),
    path("officer/dashboard/", officer_dashboard_view),
    path("officer/authorize/", officer_authorize_view, name="officer_authorize"),
    path("officer/cancel/", officer_cancel_authorization_view, name="officer_cancel_authorization"),
    path("officer/<int:booth_id>/", RedirectView.as_view(url="/voting/officer/", permanent=False)),
    path("kiosk/", kiosk_view, name="kiosk"),
    path("kiosk/<int:booth_id>/", RedirectView.as_view(url="/voting/kiosk/", permanent=False)),
]
