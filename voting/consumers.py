"""WebSocket consumers for voting application.

Owns:
- KioskConsumer: Real-time authorization reception, readiness reporting, and lock/unlock signaling for Voting Kiosks.
- OfficerConsumer: Real-time Kiosk presence monitoring and authorization updates for Officer Stations.
"""

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from accounts.models import DeviceSession, DeviceType, Role


class KioskConsumer(AsyncJsonWebsocketConsumer):
    """Consumer for Voting Kiosks.
    
    Subscribes to booth and kiosk channel groups to receive `kiosk.unlock` and `kiosk.lock` signals.
    Emits presence updates so the officer station knows when the kiosk is online and ready.
    """

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated or getattr(user, "role", None) != Role.KIOSK:
            await self.close(code=4003)
            return

        device = await sync_to_async(self._get_kiosk_device)(user)
        if not device or not device.booth_id:
            await self.close(code=4004)
            return

        self.device = device
        self.device_id = device.id
        self.booth_id = device.booth_id
        self.kiosk_group = f"kiosk_{self.device_id}"
        self.booth_group = f"booth_{self.booth_id}"

        await self.channel_layer.group_add(self.kiosk_group, self.channel_name)
        await self.channel_layer.group_add(self.booth_group, self.channel_name)
        await self.accept()

        # Broadcast to paired Officer Station that this Kiosk is connected and ready
        await self.channel_layer.group_send(
            self.booth_group,
            {
                "type": "kiosk.presence",
                "kiosk_online": True,
            }
        )

    async def disconnect(self, close_code):
        if hasattr(self, "booth_group"):
            # Broadcast to paired Officer Station that this Kiosk has disconnected
            await self.channel_layer.group_send(
                self.booth_group,
                {
                    "type": "kiosk.presence",
                    "kiosk_online": False,
                }
            )
            await self.channel_layer.group_discard(self.kiosk_group, self.channel_name)
            await self.channel_layer.group_discard(self.booth_group, self.channel_name)

    async def kiosk_unlock(self, event):
        """Receive unlock signal from server-side create_authorization() commit."""
        # BALLOT SECRECY INVARIANT: Zero voter identity transmitted to Kiosk!
        await self.send_json({
            "event": "kiosk.unlock",
            "authorization_id": event.get("authorization_id"),
        })

    async def kiosk_lock(self, event):
        """Receive lock signal from server-side cancellation or ballot submission."""
        await self.send_json({
            "event": "kiosk.lock",
        })

    async def kiosk_presence(self, event):
        # Kiosk ignores its own presence broadcast
        pass

    def _get_kiosk_device(self, user):
        device = getattr(user, "device", None)
        if device and device.device_type == DeviceType.KIOSK:
            return device
        return None


class OfficerConsumer(AsyncJsonWebsocketConsumer):
    """Consumer for Polling Officer Stations.
    
    Subscribes to booth channel group to receive real-time Kiosk connectivity status and turnout updates.
    """

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated or getattr(user, "role", None) != Role.OFFICER:
            await self.close(code=4003)
            return

        device = await sync_to_async(self._get_officer_device)(user)
        if not device or not device.booth_id:
            await self.close(code=4004)
            return

        self.device = device
        self.device_id = device.id
        self.booth_id = device.booth_id
        self.officer_group = f"officer_{self.device_id}"
        self.booth_group = f"booth_{self.booth_id}"

        await self.channel_layer.group_add(self.officer_group, self.channel_name)
        await self.channel_layer.group_add(self.booth_group, self.channel_name)
        await self.accept()

        # Send initial status of paired kiosk
        is_online = await sync_to_async(self._is_kiosk_online)(self.booth_id)
        await self.send_json({
            "event": "kiosk.status",
            "online": is_online,
        })

    async def disconnect(self, close_code):
        if hasattr(self, "booth_group"):
            await self.channel_layer.group_discard(self.officer_group, self.channel_name)
            await self.channel_layer.group_discard(self.booth_group, self.channel_name)

    async def kiosk_presence(self, event):
        """Forward real-time Kiosk connectivity changes to the Officer Dashboard."""
        await self.send_json({
            "event": "kiosk.status",
            "online": event.get("kiosk_online", False),
        })

    async def authorization_updated(self, event):
        """Forward authorization changes (voted, cancelled) to Officer Dashboard."""
        await self.send_json({
            "event": "authorization.updated",
            "voter_id": event.get("voter_id"),
            "status": event.get("status"),
        })

    def _get_officer_device(self, user):
        device = getattr(user, "device", None)
        if device and device.device_type == DeviceType.OFFICER:
            return device
        return None

    def _is_kiosk_online(self, booth_id):
        return DeviceSession.objects.filter(
            device__booth_id=booth_id,
            device__device_type=DeviceType.KIOSK,
            is_active=True
        ).exists()
