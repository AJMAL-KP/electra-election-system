"""WebSocket URL routing for Electra.

Maps WebSocket connections to Channels consumers.
"""
from django.urls import path
from voting.consumers import KioskConsumer, OfficerConsumer

websocket_urlpatterns = [
    path("ws/kiosk/", KioskConsumer.as_asgi(), name="ws_kiosk"),
    path("ws/officer/", OfficerConsumer.as_asgi(), name="ws_officer"),
]
