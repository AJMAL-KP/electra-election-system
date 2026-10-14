from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from accounts.models import Role, User
from accounts.selectors import get_administrator, is_installation_initialized
from accounts.services import initialize_installation


class Phase1FoundationTests(TestCase):
    """Phase 1 verification tests covering foundational setup, user model, and initialization."""

    def test_installation_initially_uninitialized(self):
        """When no administrator exists, the installation reports uninitialized."""
        self.assertFalse(is_installation_initialized())
        self.assertIsNone(get_administrator())

    def test_initialize_installation_creates_admin(self):
        """First-run setup successfully creates the single Administrator account."""
        admin = initialize_installation(
            username="test_admin",
            email="admin@institution.edu",
            password="secure_password_123"
        )
        self.assertEqual(admin.username, "test_admin")
        self.assertEqual(admin.role, Role.ADMIN)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(is_installation_initialized())
        self.assertEqual(get_administrator(), admin)

    def test_cannot_initialize_installation_twice(self):
        """Attempting to initialize an already-initialized installation raises ValidationError."""
        initialize_installation("admin_one", "one@inst.edu", "pass123")
        with self.assertRaises(ValidationError):
            initialize_installation("admin_two", "two@inst.edu", "pass456")

    def test_database_enforces_single_admin_constraint(self):
        """Database constraint prevents more than one user with role=ADMIN."""
        User.objects.create_user(username="first_admin", password="p1", role=Role.ADMIN)
        with self.assertRaises(IntegrityError):
            User.objects.create_user(username="second_admin", password="p2", role=Role.ADMIN)

    def test_multiple_technical_identities_allowed(self):
        """Multiple Officer and Kiosk technical identities may exist without violating admin constraint."""
        User.objects.create_user(username="admin_user", password="p", role=Role.ADMIN)
        officer1 = User.objects.create_user(username="off_1", password="p", role=Role.OFFICER)
        officer2 = User.objects.create_user(username="off_2", password="p", role=Role.OFFICER)
        kiosk1 = User.objects.create_user(username="kiosk_1", password="p", role=Role.KIOSK)
        self.assertEqual(User.objects.filter(role=Role.OFFICER).count(), 2)
        self.assertEqual(User.objects.filter(role=Role.KIOSK).count(), 1)

    def test_health_check_endpoint(self):
        """Health check returns 200 with operational status."""
        client = Client()
        response = client.get(reverse('health-check'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["system"], "electra")
        self.assertEqual(data["database"], "connected")
        self.assertFalse(data["installation_initialized"])

        # After initialization
        initialize_installation("inst_admin", "adm@inst.edu", "p123456")
        response2 = client.get(reverse('health-check'))
        self.assertTrue(response2.json()["installation_initialized"])

    def test_root_index_view(self):
        """Root URL renders the system overview landing template."""
        client = Client()
        response = client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "index.html")
        self.assertIn("is_initialized", response.context)
        self.assertFalse(response.context["is_initialized"])


from accounts.models import CredentialStatus, Device, DeviceSession, DeviceType
from accounts.permissions import admin_required, kiosk_required, officer_required
from accounts.services import (
    create_device,
    create_device_session,
    login_device_user,
    logout_user,
    revoke_device_credentials,
    rotate_device_credentials,
)
from django.http import HttpResponse


class Phase2AuthenticationAndRBACTests(TestCase):
    """Phase 2 verification tests covering Setup, Device provisioning, rotation, sessions, and RBAC."""

    def test_setup_view_renders_uninitialized(self):
        """Uninitialized installation displays the first-run administrator setup page."""
        client = Client()
        response = client.get(reverse('accounts:setup'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/setup.html")

    def test_setup_view_post_creates_admin_and_locks(self):
        """Submitting setup form creates administrator and locks further setup attempts."""
        client = Client()
        response = client.post(reverse('accounts:setup'), {
            'username': 'inst_admin',
            'email': 'admin@institution.edu',
            'password': 'super_secure_pass_123',
            'confirm_password': 'super_secure_pass_123'
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(is_installation_initialized())
        admin = get_administrator()
        self.assertEqual(admin.username, 'inst_admin')

        # Subsequent attempts to access setup redirect to login
        second_response = client.get(reverse('accounts:setup'))
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(second_response.url, reverse('accounts:login'))

        # Home page Start button now points to login
        index_response = client.get(reverse('index'))
        self.assertContains(index_response, 'href="/accounts/login/"')
        self.assertNotContains(index_response, 'href="/accounts/setup/"')

    def test_create_device_provisioning(self):
        """Device creation establishes technical User and Device with active credentials."""
        device, plain_password = create_device("booth-1-officer", DeviceType.OFFICER)
        self.assertEqual(device.identifier, "booth-1-officer")
        self.assertEqual(device.device_type, DeviceType.OFFICER)
        self.assertEqual(device.credential_status, CredentialStatus.ACTIVE)
        self.assertEqual(device.credential_version, 1)
        self.assertEqual(device.user.role, Role.OFFICER)
        self.assertTrue(device.user.check_password(plain_password))

    def test_rotate_device_credentials_updates_password_and_invalidates_session(self):
        """Credential rotation increments version, updates password, and terminates active session."""
        device, initial_pass = create_device("booth-1-kiosk", DeviceType.KIOSK)
        # Create an active session
        session = DeviceSession.objects.create(device=device, session_key="session_abc_123", is_active=True)

        device, new_pass = rotate_device_credentials(device.id)
        self.assertNotEqual(initial_pass, new_pass)
        self.assertTrue(device.user.check_password(new_pass))
        self.assertFalse(device.user.check_password(initial_pass))
        self.assertEqual(device.credential_version, 2)

        # Active session must be invalidated
        session.refresh_from_db()
        self.assertFalse(session.is_active)
        self.assertIsNotNone(session.revoked_at)

    def test_revoke_device_credentials(self):
        """Revocation sets status to REVOKED and terminates active sessions."""
        device, _ = create_device("booth-2-officer", DeviceType.OFFICER)
        session = DeviceSession.objects.create(device=device, session_key="session_xyz", is_active=True)

        revoked_device = revoke_device_credentials(device.id)
        self.assertEqual(revoked_device.credential_status, CredentialStatus.REVOKED)

        session.refresh_from_db()
        self.assertFalse(session.is_active)

    def test_single_active_session_enforcement(self):
        """A device cannot have two simultaneous active sessions."""
        device, _ = create_device("booth-3-kiosk", DeviceType.KIOSK)
        create_device_session(device, "sess_first")

        # Attempting second session raises ValidationError
        with self.assertRaises(ValidationError):
            create_device_session(device, "sess_second")

        # Database level unique constraint also prevents it
        with self.assertRaises(IntegrityError):
            DeviceSession.objects.create(device=device, session_key="sess_second_raw", is_active=True)

    def test_login_device_and_active_session_lifecycle(self):
        """Logging in a device user creates an active DeviceSession; logging out terminates it."""
        initialize_installation("admin_login_test", "adm@i.edu", "pass12345678")
        client = Client()
        device, password = create_device("booth-4-officer", DeviceType.OFFICER)

        login_res = client.post(reverse('accounts:login'), {
            'username': 'booth-4-officer',
            'password': password
        })
        self.assertEqual(login_res.status_code, 302)

        # Active session must exist
        active_sess = DeviceSession.objects.filter(device=device, is_active=True).first()
        self.assertIsNotNone(active_sess)
        self.assertTrue(active_sess.is_active)

        # Second simultaneous login attempt takes over the session
        client2 = Client()
        takeover_res = client2.post(reverse('accounts:login'), {
            'username': 'booth-4-officer',
            'password': password
        })
        self.assertEqual(takeover_res.status_code, 302)

        # First session must now be marked inactive
        active_sess.refresh_from_db()
        self.assertFalse(active_sess.is_active)

        # Exactly one active session exists for this device, matching client2
        active_sessions = DeviceSession.objects.filter(device=device, is_active=True)
        self.assertEqual(active_sessions.count(), 1)
        self.assertNotEqual(active_sessions.first().session_key, active_sess.session_key)

        # Logout client2 terminates the new session
        client2.get(reverse('accounts:logout'))
        self.assertEqual(DeviceSession.objects.filter(device=device, is_active=True).count(), 0)

    def test_revoked_device_login_rejected(self):
        """A revoked device cannot log in."""
        initialize_installation("admin_revoked_test", "rev@i.edu", "pass12345678")
        client = Client()
        device, password = create_device("booth-5-kiosk", DeviceType.KIOSK)
        revoke_device_credentials(device.id)

        res = client.post(reverse('accounts:login'), {
            'username': 'booth-5-kiosk',
            'password': password
        })
        self.assertEqual(res.status_code, 403)
        self.assertIn("revoked", res.content.decode().lower())

    def test_role_based_access_control(self):
        """Role decorators enforce strict boundaries between ADMIN, OFFICER, and KIOSK."""
        @admin_required
        def dummy_admin(request):
            return HttpResponse("admin_ok")

        @officer_required
        def dummy_officer(request):
            return HttpResponse("officer_ok")

        @kiosk_required
        def dummy_kiosk(request):
            return HttpResponse("kiosk_ok")

        from django.test import RequestFactory
        factory = RequestFactory()

        # Admin user
        admin_user = User.objects.create_user("admin_rbac", "a@i.edu", "pass", role=Role.ADMIN)
        req = factory.get('/')
        req.user = admin_user
        self.assertEqual(dummy_admin(req).content.decode(), "admin_ok")

        # Admin blocked from officer view
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            dummy_officer(req)

        with self.assertRaises(PermissionDenied):
            dummy_kiosk(req)


from accounts.models import AdminSession
from django.utils import timezone
from elections.models import Election, ElectionStatus
from voters.models import Booth, VoterRegistry, Voter, ElectionVoter
from voting.models import VoterAuthorization, AuthorizationStatus
from voting.consumers import KioskConsumer, OfficerConsumer, AdminLiveConsumer
from channels.testing import WebsocketCommunicator


class SingleActiveSessionTakeoverTests(TestCase):
    """Rigorous verification tests covering Single-Active-Session Takeover and Enforcement."""

    def setUp(self):
        self.admin = initialize_installation("inst_admin", "admin@inst.edu", "pass12345678")
        self.election = Election.objects.create(
            name="Session Test Election",
            status=ElectionStatus.ACTIVE,
            starts_at=timezone.now() - timezone.timedelta(hours=1),
            ends_at=timezone.now() + timezone.timedelta(hours=5),
        )
        self.booth = Booth.objects.create(
            election=self.election,
            name="Booth 1",
            booth_number=1,
        )
        self.officer_dev, self.officer_pw = create_device("booth-1-officer", DeviceType.OFFICER, booth=self.booth)
        self.kiosk_dev, self.kiosk_pw = create_device("booth-1-kiosk", DeviceType.KIOSK, booth=self.booth)

    def test_admin_single_active_session_takeover(self):
        """Admin subsequent login takes over session; older session loses HTTP access."""
        client1 = Client()
        r1 = client1.post(reverse('accounts:login'), {'username': 'inst_admin', 'password': 'pass12345678'})
        self.assertEqual(r1.status_code, 302)
        sess1 = AdminSession.objects.filter(user=self.admin, is_active=True).first()
        self.assertIsNotNone(sess1)

        # Protected view succeeds for client1
        res1 = client1.get(reverse('elections:dashboard'))
        self.assertEqual(res1.status_code, 200)

        # Client 2 logs in (Takeover)
        client2 = Client()
        r2 = client2.post(reverse('accounts:login'), {'username': 'inst_admin', 'password': 'pass12345678'})
        self.assertEqual(r2.status_code, 302)

        # Authoritative state: sess1 is inactive, exactly one active session exists
        sess1.refresh_from_db()
        self.assertFalse(sess1.is_active)
        self.assertIsNotNone(sess1.revoked_at)
        active_admin_sessions = AdminSession.objects.filter(user=self.admin, is_active=True)
        self.assertEqual(active_admin_sessions.count(), 1)
        sess2 = active_admin_sessions.first()
        self.assertNotEqual(sess1.session_key, sess2.session_key)

        # Client 1 loses authorization immediately (redirected to login or rejected)
        res1_after = client1.get(reverse('elections:dashboard'))
        self.assertEqual(res1_after.status_code, 302)
        self.assertIn(reverse('accounts:login'), res1_after.url)

        # Client 2 retains access
        res2_after = client2.get(reverse('elections:dashboard'))
        self.assertEqual(res2_after.status_code, 200)

    def test_officer_single_active_session_takeover(self):
        """Officer subsequent login takes over session; old session rejected on protected view."""
        client1 = Client()
        r1 = client1.post(reverse('accounts:login'), {'username': 'booth-1-officer', 'password': self.officer_pw})
        self.assertEqual(r1.status_code, 302)
        sess1 = DeviceSession.objects.filter(device=self.officer_dev, is_active=True).first()
        self.assertIsNotNone(sess1)

        res1 = client1.get(reverse('voting:officer_dashboard'))
        self.assertEqual(res1.status_code, 200)

        # Client 2 logs in (Takeover)
        client2 = Client()
        r2 = client2.post(reverse('accounts:login'), {'username': 'booth-1-officer', 'password': self.officer_pw})
        self.assertEqual(r2.status_code, 302)

        sess1.refresh_from_db()
        self.assertFalse(sess1.is_active)
        self.assertEqual(DeviceSession.objects.filter(device=self.officer_dev, is_active=True).count(), 1)

        # Client 1 loses authorization immediately
        res1_after = client1.get(reverse('voting:officer_dashboard'))
        self.assertIn(res1_after.status_code, (302, 403))
        if res1_after.status_code == 302:
            self.assertIn(reverse('accounts:login'), res1_after.url)

        # Client 2 preserves booth binding and accesses dashboard
        res2_after = client2.get(reverse('voting:officer_dashboard'))
        self.assertEqual(res2_after.status_code, 200)

    def test_kiosk_takeover_preserves_active_voting_authorization(self):
        """Kiosk takeover invalidates old session but preserves active authorization and voting state."""
        registry = VoterRegistry.objects.create(name="Reg 1", primary_id_source="Roll No")
        voter = Voter.objects.create(registry=registry, primary_registry_value="V001", name="Test Voter")
        ev = ElectionVoter.objects.create(election=self.election, voter=voter, booth=self.booth)
        auth = VoterAuthorization.objects.create(
            booth=self.booth,
            kiosk=self.kiosk_dev,
            election_voter=ev,
            status=AuthorizationStatus.ACTIVE,
        )

        client1 = Client()
        r1 = client1.post(reverse('accounts:login'), {'username': 'booth-1-kiosk', 'password': self.kiosk_pw})
        self.assertEqual(r1.status_code, 302)
        sess1 = DeviceSession.objects.filter(device=self.kiosk_dev, is_active=True).first()
        self.assertIsNotNone(sess1)

        # Kiosk view renders with unlocked active_auth
        res1 = client1.get(reverse('voting:kiosk'))
        self.assertEqual(res1.status_code, 200)
        self.assertFalse(res1.context['is_locked'])
        self.assertEqual(res1.context['active_auth'].id, auth.id)

        # Client 2 logs in (Takeover)
        client2 = Client()
        r2 = client2.post(reverse('accounts:login'), {'username': 'booth-1-kiosk', 'password': self.kiosk_pw})
        self.assertEqual(r2.status_code, 302)

        # Invariant: VoterAuthorization was NEVER modified, consumed, or reset
        auth.refresh_from_db()
        self.assertEqual(auth.status, AuthorizationStatus.ACTIVE)
        self.assertFalse(auth.election_voter.has_voted)

        # Old session is rejected on kiosk submit/view
        sess1.refresh_from_db()
        self.assertFalse(sess1.is_active)
        res1_after = client1.get(reverse('voting:kiosk'))
        self.assertIn(res1_after.status_code, (302, 403))
        if res1_after.status_code == 302:
            self.assertIn(reverse('accounts:login'), res1_after.url)

        # New session restores the active authorization interface
        res2_after = client2.get(reverse('voting:kiosk'))
        self.assertEqual(res2_after.status_code, 200)
        self.assertFalse(res2_after.context['is_locked'])
        self.assertEqual(res2_after.context['active_auth'].id, auth.id)

    def test_cross_identity_isolation(self):
        """Takeover for one identity must not invalidate sessions belonging to other identities."""
        c_admin = Client()
        c_admin.post(reverse('accounts:login'), {'username': 'inst_admin', 'password': 'pass12345678'})

        c_off = Client()
        c_off.post(reverse('accounts:login'), {'username': 'booth-1-officer', 'password': self.officer_pw})

        c_kio = Client()
        c_kio.post(reverse('accounts:login'), {'username': 'booth-1-kiosk', 'password': self.kiosk_pw})

        self.assertEqual(c_admin.get(reverse('elections:dashboard')).status_code, 200)
        self.assertEqual(c_off.get(reverse('voting:officer_dashboard')).status_code, 200)
        self.assertEqual(c_kio.get(reverse('voting:kiosk')).status_code, 200)

        # Takeover Officer session with new client
        c_off2 = Client()
        c_off2.post(reverse('accounts:login'), {'username': 'booth-1-officer', 'password': self.officer_pw})

        # Admin and Kiosk MUST remain active
        self.assertEqual(c_admin.get(reverse('elections:dashboard')).status_code, 200)
        self.assertEqual(c_kio.get(reverse('voting:kiosk')).status_code, 200)
        # New officer is active
        self.assertEqual(c_off2.get(reverse('voting:officer_dashboard')).status_code, 200)

    def test_delayed_logout_cannot_revoke_newer_session(self):
        """A delayed logout from an old browser cannot revoke the newer taken-over session."""
        c1 = Client()
        c1.post(reverse('accounts:login'), {'username': 'inst_admin', 'password': 'pass12345678'})

        c2 = Client()
        c2.post(reverse('accounts:login'), {'username': 'inst_admin', 'password': 'pass12345678'})

        sess2 = AdminSession.objects.filter(user=self.admin, is_active=True).first()
        self.assertIsNotNone(sess2)

        # Client 1 sends a delayed logout request
        c1.get(reverse('accounts:logout'))

        # Invariant: Client 2 session remains active!
        sess2.refresh_from_db()
        self.assertTrue(sess2.is_active)
        self.assertEqual(c2.get(reverse('elections:dashboard')).status_code, 200)

    def test_websocket_superseded_session_rejected(self):
        """WebSocket connection with superseded session key is rejected."""
        from asgiref.sync import async_to_sync, sync_to_async

        async def _run():
            def _setup_sessions():
                s_old = DeviceSession.objects.create(
                    device=self.kiosk_dev,
                    session_key="old-kiosk-ws-key",
                    is_active=False
                )
                s_new = DeviceSession.objects.create(
                    device=self.kiosk_dev,
                    session_key="new-kiosk-ws-key",
                    is_active=True
                )
                return s_old, s_new

            await sync_to_async(_setup_sessions)()

            # Old session communicator
            comm_old = WebsocketCommunicator(
                KioskConsumer.as_asgi(),
                "/ws/kiosk/",
            )
            comm_old.scope["user"] = self.kiosk_dev.user
            comm_old.scope["session"] = type("SessionMock", (), {"session_key": "old-kiosk-ws-key"})()

            connected_old, code_old = await comm_old.connect()
            self.assertFalse(connected_old)
            self.assertEqual(code_old, 4003)

            # New session communicator
            comm_new = WebsocketCommunicator(
                KioskConsumer.as_asgi(),
                "/ws/kiosk/",
            )
            comm_new.scope["user"] = self.kiosk_dev.user
            comm_new.scope["session"] = type("SessionMock", (), {"session_key": "new-kiosk-ws-key"})()

            connected_new, _ = await comm_new.connect()
            self.assertTrue(connected_new)
            await comm_new.disconnect()

        async_to_sync(_run)()



