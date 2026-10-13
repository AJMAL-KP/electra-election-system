"""WebSocket URL routing for Electra.

Maps WebSocket connections to Channels consumers.
"""
from django.urls import path
from voting.consumers import AdminLiveConsumer, KioskConsumer, OfficerConsumer

websocket_urlpatterns = [
    path("ws/kiosk/", KioskConsumer.as_asgi(), name="ws_kiosk"),
    path("ws/officer/", OfficerConsumer.as_asgi(), name="ws_officer"),
    path("ws/admin/live/", AdminLiveConsumer.as_asgi(), name="ws_admin_live"),
]
