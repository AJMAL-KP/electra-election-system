"""WebSocket consumers for voting application.

Owns:
- KioskConsumer: Real-time authorization reception, readiness reporting, and lock/unlock signaling for Voting Kiosks.
- OfficerConsumer: Real-time Kiosk presence monitoring and authorization updates for Officer Stations.
"""

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.cache import cache
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
        self.officer_booth_group = f"booth_officer_{self.booth_id}"

        await self.channel_layer.group_add(self.kiosk_group, self.channel_name)
        await self.channel_layer.group_add(self.booth_group, self.channel_name)
        await self.accept()

        # Mark Kiosk WS as online in cache
        cache.set(f"kiosk_online_{self.booth_id}", True, timeout=86400)
        # Initialize kiosk fullscreen state to False until client reports readiness
        cache.set(f"kiosk_fullscreen_{self.booth_id}", False, timeout=86400)

        # Broadcast to paired Officer Station that this Kiosk is connected
        await self.channel_layer.group_send(
            self.booth_group,
            {
                "type": "kiosk.presence",
                "kiosk_online": True,
            }
        )
        await self.channel_layer.group_send(
            self.officer_booth_group,
            {
                "type": "kiosk.presence",
                "kiosk_online": True,
            }
        )
        await self.channel_layer.group_send(
            "admin_live",
            {
                "type": "kiosk.presence",
                "booth_id": self.booth_id,
                "kiosk_online": True,
            }
        )

        # AGENTS.md Section 18: Reconnection / Resynchronization
        # If an active authorization exists for this kiosk, immediately send kiosk.unlock!
        active_auth_info = await sync_to_async(self._get_active_auth_info)(self.device)
        if active_auth_info:
            await self.send_json({
                "event": "kiosk.unlock",
                "authorization_id": active_auth_info["authorization_id"],
                "eligible_position_ids": active_auth_info["eligible_position_ids"],
            })

    async def disconnect(self, close_code):
        if hasattr(self, "booth_id"):
            cache.set(f"kiosk_online_{self.booth_id}", False, timeout=86400)
            cache.set(f"kiosk_fullscreen_{self.booth_id}", False, timeout=86400)
        if hasattr(self, "booth_group"):
            # Broadcast to paired Officer Station that this Kiosk has disconnected
            await self.channel_layer.group_send(
                self.booth_group,
                {
                    "type": "kiosk.presence",
                    "kiosk_online": False,
                }
            )
            if hasattr(self, "officer_booth_group"):
                await self.channel_layer.group_send(
                    self.officer_booth_group,
                    {
                        "type": "kiosk.presence",
                        "kiosk_online": False,
                    }
                )
            await self.channel_layer.group_send(
                self.booth_group,
                {
                    "type": "kiosk.fullscreen_status",
                    "fullscreen": False,
                }
            )
            if hasattr(self, "officer_booth_group"):
                await self.channel_layer.group_send(
                    self.officer_booth_group,
                    {
                        "type": "kiosk.fullscreen_status",
                        "fullscreen": False,
                    }
                )
            await self.channel_layer.group_send(
                "admin_live",
                {
                    "type": "kiosk.presence",
                    "booth_id": self.booth_id,
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
            "eligible_position_ids": event.get("eligible_position_ids", []),
        })

    async def kiosk_lock(self, event):
        """Receive lock signal from server-side cancellation or ballot submission."""
        await self.send_json({
            "event": "kiosk.lock",
            "booth_completed": event.get("booth_completed", False),
        })

    async def booth_completed(self, event):
        """Receive signal that all voters in this booth have cast ballots."""
        await self.send_json({
            "event": "booth.completed",
            "booth_id": event.get("booth_id"),
        })

    async def kiosk_presence(self, event):
        # Kiosk ignores its own presence broadcast
        pass

    async def kiosk_fullscreen_status(self, event):
        """No-op on Kiosk terminal."""
        pass

    async def kiosk_ballot_error(self, event):
        """No-op on Kiosk terminal."""
        pass

    async def authorization_updated(self, event):
        """BALLOT SECRECY: Kiosk terminal ignores voter authorization events."""
        pass

    async def ballot_recorded(self, event):
        """Kiosk terminal ignores booth-level ballot recording events."""
        pass

    async def election_started(self, event):
        """Receive real-time signal that election has launched ACTIVE."""
        await self.send_json({
            "event": "election.started",
            "election_id": event.get("election_id"),
        })

    async def election_closed(self, event):
        """Receive real-time signal that election has CLOSED."""
        await self.send_json({
            "event": "election.closed",
            "election_id": event.get("election_id"),
        })

    async def election_cutoff(self, event):
        """Receive real-time signal that election cutoff has arrived / new auths frozen."""
        await self.send_json({
            "event": "election.cutoff",
            "election_id": event.get("election_id"),
        })

    async def credentials_revoked(self, event):
        """Notify kiosk its credentials have been revoked — force logout redirect."""
        await self.send_json({"event": "credentials.revoked"})

    async def receive_json(self, content):
        """Handle incoming messages from Kiosk terminal (e.g. Fullscreen changes)."""
        msg_type = content.get("type") or content.get("event")
        fullscreen_val = content.get("fullscreen")
        if msg_type in ("fullscreen_entered", "fullscreen.entered", "kiosk.fullscreen") or fullscreen_val is True:
            if hasattr(self, "booth_id"):
                cache.set(f"kiosk_fullscreen_{self.booth_id}", True, timeout=86400)
            if hasattr(self, "booth_group"):
                await self.channel_layer.group_send(
                    self.booth_group,
                    {
                        "type": "kiosk.fullscreen_status",
                        "fullscreen": True,
                    }
                )
            if hasattr(self, "officer_booth_group"):
                await self.channel_layer.group_send(
                    self.officer_booth_group,
                    {
                        "type": "kiosk.fullscreen_status",
                        "fullscreen": True,
                    }
                )
        elif msg_type in ("fullscreen_exited", "fullscreen.exited", "kiosk.fullscreen_exit") or fullscreen_val is False:
            if hasattr(self, "booth_id"):
                cache.set(f"kiosk_fullscreen_{self.booth_id}", False, timeout=86400)
            if hasattr(self, "booth_group"):
                await self.channel_layer.group_send(
                    self.booth_group,
                    {
                        "type": "kiosk.fullscreen_status",
                        "fullscreen": False,
                    }
                )
            if hasattr(self, "officer_booth_group"):
                await self.channel_layer.group_send(
                    self.officer_booth_group,
                    {
                        "type": "kiosk.fullscreen_status",
                        "fullscreen": False,
                    }
                )

    def _get_kiosk_device(self, user):
        device = getattr(user, "device", None)
        if device and device.device_type == DeviceType.KIOSK:
            return device
        return None

    def _get_active_auth_info(self, device):
        from voting.models import AuthorizationStatus, VoterAuthorization
        from voting.services import is_voter_eligible_for_position
        from django.db.models import Count
        auth = VoterAuthorization.objects.filter(
            kiosk=device,
            status=AuthorizationStatus.ACTIVE
        ).select_related("election_voter__voter", "booth__election").first()
        if not auth or not auth.election_voter:
            return None

        election = auth.booth.election
        voter = auth.election_voter.voter
        all_contested = list(
            election.positions
            .prefetch_related('eligible_groups')
            .annotate(cand_count=Count('candidates'))
            .filter(cand_count__gt=1)
            .order_by('display_order', 'id')
        )
        eligible_pos_ids = [
            pos.id for pos in all_contested
            if is_voter_eligible_for_position(voter, pos)
        ]
        return {
            "authorization_id": auth.id,
            "eligible_position_ids": eligible_pos_ids,
        }


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
        self.officer_booth_group = f"booth_officer_{self.booth_id}"

        await self.channel_layer.group_add(self.officer_group, self.channel_name)
        await self.channel_layer.group_add(self.booth_group, self.channel_name)
        await self.channel_layer.group_add(self.officer_booth_group, self.channel_name)
        await self.accept()

        # Broadcast to admin_live that this Officer station is connected
        await self.channel_layer.group_send(
            "admin_live",
            {
                "type": "officer.presence",
                "booth_id": self.booth_id,
                "officer_online": True,
            }
        )

        # Send authoritative initial status of paired kiosk
        is_online = await sync_to_async(self._is_kiosk_online)(self.booth_id)
        await self.send_json({
            "event": "kiosk.status",
            "online": is_online,
        })
        is_fullscreen = bool(cache.get(f"kiosk_fullscreen_{self.booth_id}", False)) if is_online else False
        await self.send_json({
            "event": "kiosk.fullscreen",
            "fullscreen": is_fullscreen,
        })

        # AGENTS.md Section 18: Reconnection / Resynchronization
        # If an active authorization exists for this booth, sync to Officer station
        active_auth = await sync_to_async(self._get_active_auth_voter)(self.booth_id)
        if active_auth:
            await self.send_json({
                "event": "authorization.updated",
                "voter_id": active_auth["voter_id"],
                "authorization_id": active_auth["authorization_id"],
                "status": "in_progress",
            })

    async def disconnect(self, close_code):
        if hasattr(self, "booth_id"):
            await self.channel_layer.group_send(
                "admin_live",
                {
                    "type": "officer.presence",
                    "booth_id": self.booth_id,
                    "officer_online": False,
                }
            )
        if hasattr(self, "booth_group"):
            await self.channel_layer.group_discard(self.officer_group, self.channel_name)
            await self.channel_layer.group_discard(self.booth_group, self.channel_name)
            await self.channel_layer.group_discard(self.officer_booth_group, self.channel_name)

    async def kiosk_presence(self, event):
        """Forward real-time Kiosk connectivity changes to the Officer Dashboard."""
        is_online = event.get("kiosk_online", False)
        await self.send_json({
            "event": "kiosk.status",
            "online": is_online,
        })
        if not is_online:
            await self.send_json({
                "event": "kiosk.fullscreen",
                "fullscreen": False,
            })

    async def authorization_updated(self, event):
        """Forward authorization changes (voted, cancelled) to Officer Dashboard."""
        await self.send_json({
            "event": "authorization.updated",
            "voter_id": event.get("voter_id"),
            "status": event.get("status"),
        })

    async def ballot_recorded(self, event):
        """Forward ballot recorded notification to the Officer Station."""
        await self.send_json({
            "event": "ballot.recorded",
            "voter_id": event.get("voter_id"),
        })

    async def election_started(self, event):
        """Receive real-time signal that election has launched ACTIVE."""
        await self.send_json({
            "event": "election.started",
            "election_id": event.get("election_id"),
        })

    async def election_closed(self, event):
        """Receive real-time signal that election has CLOSED."""
        await self.send_json({
            "event": "election.closed",
            "election_id": event.get("election_id"),
        })

    async def election_cutoff(self, event):
        """Receive real-time signal that election cutoff has arrived / new auths frozen."""
        await self.send_json({
            "event": "election.cutoff",
            "election_id": event.get("election_id"),
        })

    async def credentials_revoked(self, event):
        """Notify officer their credentials have been revoked — force logout redirect."""
        await self.send_json({"event": "credentials.revoked"})

    async def kiosk_ballot_error(self, event):
        """Notify Officer Station if ballot submission failed at paired Kiosk."""
        await self.send_json({
            "event": "kiosk.ballot_error",
            "error": event.get("error", "Ballot submission failed"),
        })

    async def booth_completed(self, event):
        """Receive signal that all voters in this booth have cast ballots."""
        await self.send_json({
            "event": "booth.completed",
            "booth_id": event.get("booth_id"),
        })

    async def kiosk_fullscreen_status(self, event):
        """Notify Officer Station of Kiosk fullscreen state."""
        await self.send_json({
            "event": "kiosk.fullscreen",
            "fullscreen": event.get("fullscreen", False),
        })

    def _get_officer_device(self, user):
        device = getattr(user, "device", None)
        if device and device.device_type == DeviceType.OFFICER:
            return device
        return None

    def _is_kiosk_online(self, booth_id):
        has_session = DeviceSession.objects.filter(
            device__booth_id=booth_id,
            device__device_type=DeviceType.KIOSK,
            is_active=True
        ).exists()
        is_ws_offline = (cache.get(f"kiosk_online_{booth_id}") is False)
        return has_session and not is_ws_offline

    def _get_active_auth_voter(self, booth_id):
        from voting.models import AuthorizationStatus, VoterAuthorization
        auth = VoterAuthorization.objects.filter(
            booth_id=booth_id,
            status=AuthorizationStatus.ACTIVE
        ).select_related("election_voter__voter").first()
        if auth and auth.election_voter and auth.election_voter.voter:
            return {
                "voter_id": auth.election_voter.voter.primary_registry_value,
                "authorization_id": auth.id,
            }
        return None


class AdminLiveConsumer(AsyncJsonWebsocketConsumer):
    """Consumer for Admin Live Election Dashboard.
    
    Subscribes to 'admin_live' group to receive real-time notifications:
    - 'ballot.recorded'
    - 'election.closed'
    - 'election.started'
    - 'kiosk.presence'
    """

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated or getattr(user, "role", None) != Role.ADMIN:
            await self.close(code=4003)
            return

        self.group_name = "admin_live"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def ballot_recorded(self, event):
        await self.send_json({
            "event": "ballot.recorded",
            "booth_id": event.get("booth_id"),
        })

    async def election_closed(self, event):
        await self.send_json({
            "event": "election.closed",
            "election_id": event.get("election_id"),
        })

    async def election_cutoff(self, event):
        await self.send_json({
            "event": "election.cutoff",
            "election_id": event.get("election_id"),
        })

    async def election_started(self, event):
        await self.send_json({
            "event": "election.started",
            "election_id": event.get("election_id"),
        })

    async def kiosk_presence(self, event):
        await self.send_json({
            "event": "kiosk.presence",
            "booth_id": event.get("booth_id"),
            "kiosk_online": event.get("kiosk_online"),
        })

    async def officer_presence(self, event):
        await self.send_json({
            "event": "officer.presence",
            "booth_id": event.get("booth_id"),
            "officer_online": event.get("officer_online"),
        })

    async def review_refresh(self, event):
        await self.send_json({
            "event": "review.refresh",
            "election_id": event.get("election_id"),
        })

    async def receive_json(self, content):
        action = content.get("action")
        if action == "check_review_status":
            election_id = content.get("election_id")
            if election_id:
                from elections.selectors import get_election_review_status
                status_data = await sync_to_async(get_election_review_status)(int(election_id))
                await self.send_json({
                    "event": "review.status",
                    "status": status_data,
                })
