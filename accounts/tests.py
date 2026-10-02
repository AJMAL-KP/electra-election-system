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

        # Second simultaneous login attempt while first is active is rejected
        client2 = Client()
        rejected_res = client2.post(reverse('accounts:login'), {
            'username': 'booth-4-officer',
            'password': password
        })
        self.assertEqual(rejected_res.status_code, 403)
        self.assertIn("already has an active session", rejected_res.content.decode())

        # Logout first client terminates session
        client.get(reverse('accounts:logout'))
        active_sess.refresh_from_db()
        self.assertFalse(active_sess.is_active)

        # Now client2 can log in
        login_res2 = client2.post(reverse('accounts:login'), {
            'username': 'booth-4-officer',
            'password': password
        })
        self.assertEqual(login_res2.status_code, 302)

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


