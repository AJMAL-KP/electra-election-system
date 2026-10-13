"""Tests for voting application covering Officer Login, RBAC, and Officer Workflow."""

from django.test import Client, TestCase
from django.urls import reverse
from accounts.models import CredentialStatus, Device, DeviceSession, DeviceType, Role, User
from accounts.services import create_device, create_device_session, initialize_installation
from elections.models import Election, ElectionStatus
from elections.services import create_election
from voters.models import AcademicGroup, ElectionVoter, Voter, VoterRegistry
from voters.services import create_booth, enroll_voters_in_election


class OfficerLoginAndRBACRedirectionTests(TestCase):
    """Verifies Role-Based Access Control login routing and station boundaries."""

    def setUp(self):
        # 1. Initialize installation with single Admin
        self.admin = initialize_installation("electra_admin", "admin@inst.edu", "admin_pass_1234")

        # 2. Setup Election, Booth, Officer & Kiosk devices
        self.election = create_election(name="Campus Union 2026")
        booth_res = create_booth(election_id=self.election.id, name="Auditorium North")
        self.booth = booth_res["booth"]
        self.officer_device = booth_res["officer_device"]
        self.officer_password = booth_res["officer_password"]
        self.kiosk_device = booth_res["kiosk_device"]
        self.kiosk_password = booth_res["kiosk_password"]

    def test_admin_login_redirects_to_election_dashboard(self):
        """Admin login redirects canonically to /elections/dashboard/."""
        client = Client()
        res = client.post(reverse("accounts:login"), {
            "username": "electra_admin",
            "password": "admin_pass_1234",
        })
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("elections:dashboard"))

    def test_officer_login_redirects_to_officer_dashboard(self):
        """Officer station login redirects canonically to /voting/officer/."""
        client = Client()
        res = client.post(reverse("accounts:login"), {
            "username": self.officer_device.identifier,
            "password": self.officer_password,
        })
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("voting:officer_dashboard"))

        # Verify active device session created
        active_sess = DeviceSession.objects.filter(device=self.officer_device, is_active=True).first()
        self.assertIsNotNone(active_sess)

    def test_kiosk_login_redirects_to_kiosk(self):
        """Voting Kiosk login redirects canonically to /voting/kiosk/."""
        client = Client()
        res = client.post(reverse("accounts:login"), {
            "username": self.kiosk_device.identifier,
            "password": self.kiosk_password,
        })
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("voting:kiosk"))

        active_sess = DeviceSession.objects.filter(device=self.kiosk_device, is_active=True).first()
        self.assertIsNotNone(active_sess)

    def test_authenticated_admin_visiting_login_redirects_to_dashboard(self):
        """An already-authenticated Admin visiting /accounts/login/ is routed to dashboard."""
        client = Client()
        client.login(username="electra_admin", password="admin_pass_1234")
        res = client.get(reverse("accounts:login"))
        self.assertRedirects(res, reverse("elections:dashboard"))

    def test_authenticated_officer_visiting_login_redirects_to_officer_dashboard(self):
        """An already-authenticated Officer visiting /accounts/login/ is routed to officer dashboard."""
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer_device.identifier,
            "password": self.officer_password,
        })
        res = client.get(reverse("accounts:login"))
        self.assertRedirects(res, reverse("voting:officer_dashboard"))

    def test_authenticated_kiosk_visiting_login_redirects_to_kiosk(self):
        """An already-authenticated Kiosk visiting /accounts/login/ is routed to kiosk."""
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.kiosk_device.identifier,
            "password": self.kiosk_password,
        })
        res = client.get(reverse("accounts:login"))
        self.assertRedirects(res, reverse("voting:kiosk"))

    def test_authenticated_roles_visiting_root_index_redirects_correctly(self):
        """Root URL (/) redirects authenticated users based on their role."""
        # 1. Admin -> elections:dashboard
        c_admin = Client()
        c_admin.login(username="electra_admin", password="admin_pass_1234")
        res_admin = c_admin.get(reverse("index"))
        self.assertRedirects(res_admin, reverse("elections:dashboard"))

        # 2. Officer -> voting:officer_dashboard
        c_officer = Client()
        c_officer.post(reverse("accounts:login"), {
            "username": self.officer_device.identifier,
            "password": self.officer_password,
        })
        res_officer = c_officer.get(reverse("index"))
        self.assertRedirects(res_officer, reverse("voting:officer_dashboard"))

        # 3. Kiosk -> voting:kiosk
        c_kiosk = Client()
        c_kiosk.post(reverse("accounts:login"), {
            "username": self.kiosk_device.identifier,
            "password": self.kiosk_password,
        })
        res_kiosk = c_kiosk.get(reverse("index"))
        self.assertRedirects(res_kiosk, reverse("voting:kiosk"))

    def test_officer_forbidden_from_admin_dashboard(self):
        """Officer station cannot access admin-only dashboard (403 Forbidden)."""
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer_device.identifier,
            "password": self.officer_password,
        })
        res = client.get(reverse("elections:dashboard"))
        self.assertEqual(res.status_code, 403)

    def test_admin_forbidden_from_officer_dashboard(self):
        """Administrator cannot access officer station desk (403 Forbidden)."""
        client = Client()
        client.login(username="electra_admin", password="admin_pass_1234")
        res = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res.status_code, 403)

    def test_kiosk_forbidden_from_officer_dashboard(self):
        """Voting Kiosk cannot access officer station desk (403 Forbidden)."""
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.kiosk_device.identifier,
            "password": self.kiosk_password,
        })
        res = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res.status_code, 403)

    def test_officer_forbidden_from_kiosk(self):
        """Officer station cannot access kiosk interface (403 Forbidden)."""
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer_device.identifier,
            "password": self.officer_password,
        })
        res = client.get(reverse("voting:kiosk"))
        self.assertEqual(res.status_code, 403)

    def test_unauthenticated_officer_dashboard_redirects_to_login(self):
        """Unauthenticated user accessing officer dashboard is redirected to login."""
        client = Client()
        res = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("accounts:login"))


class OfficerDashboardWorkflowTests(TestCase):
    """Tests for Officer Station Voting Desk data rendering, booth isolation, and kiosk status."""

    def setUp(self):
        self.admin = initialize_installation("admin_officer_desk", "adm@i.edu", "pass12345678")

        # Setup 2 booths to verify booth isolation
        self.election = create_election(name="Student Union 2026")
        
        b1_res = create_booth(election_id=self.election.id, name="Main Auditorium")
        self.booth1 = b1_res["booth"]
        self.officer1 = b1_res["officer_device"]
        self.officer1_pass = b1_res["officer_password"]
        self.kiosk1 = b1_res["kiosk_device"]
        self.kiosk1_pass = b1_res["kiosk_password"]

        b2_res = create_booth(election_id=self.election.id, name="Library Hall")
        self.booth2 = b2_res["booth"]
        self.officer2 = b2_res["officer_device"]
        self.officer2_pass = b2_res["officer_password"]

        # Setup voters
        self.registry = VoterRegistry.objects.create(name="Campus Registry")
        self.grp_cs = AcademicGroup.objects.create(registry=self.registry, name="Computer Science")
        self.grp_ds = AcademicGroup.objects.create(registry=self.registry, name="Data Science")

        self.voter_b1 = Voter.objects.create(
            registry=self.registry,
            primary_registry_value="CS101",
            name="Alice Smith",
            academic_group=self.grp_cs
        )
        self.voter_b2 = Voter.objects.create(
            registry=self.registry,
            primary_registry_value="DS201",
            name="Bob Jones",
            academic_group=self.grp_ds
        )

        enroll_voters_in_election(election_id=self.election.id, voter_ids=[self.voter_b1.id, self.voter_b2.id])
        
        # Allocate Alice to Booth 1, Bob to Booth 2
        ev1 = ElectionVoter.objects.get(election=self.election, voter=self.voter_b1)
        ev1.booth = self.booth1
        ev1.save()

        ev2 = ElectionVoter.objects.get(election=self.election, voter=self.voter_b2)
        ev2.booth = self.booth2
        ev2.save()

    def test_officer_dashboard_enforces_booth_isolation(self):
        """Officer 1 sees only voters allocated to Booth 1; Booth 2 voters are strictly excluded."""
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })

        res = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "voting/officer/dashboard.html")

        # Booth 1 details present
        self.assertContains(res, "Voting Desk")
        self.assertContains(res, "Student Union 2026")
        self.assertContains(res, "Booth 01")
        self.assertContains(res, "Main Auditorium")

        # Alice (allocated to Booth 1) must be present
        self.assertContains(res, "CS101")
        self.assertContains(res, "Alice Smith")
        self.assertContains(res, "Computer Science")

        # Bob (allocated to Booth 2) must NOT be present (Booth Isolation)
        self.assertNotContains(res, "DS201")
        self.assertNotContains(res, "Bob Jones")

    def test_officer_dashboard_reflects_kiosk_online_and_offline(self):
        """Dashboard displays 'Kiosk Offline' when kiosk session inactive, 'Kiosk Ready' when active."""
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })

        # Initially Kiosk 1 is not logged in -> Kiosk Offline
        res_offline = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res_offline.status_code, 200)
        self.assertContains(res_offline, "Kiosk Offline")
        self.assertNotContains(res_offline, "Kiosk Ready")

        # Now simulate Kiosk 1 logging in
        client_kiosk = Client()
        client_kiosk.post(reverse("accounts:login"), {
            "username": self.kiosk1.identifier,
            "password": self.kiosk1_pass,
        })

        # Officer 1 refreshes -> now Kiosk Ready
        res_online = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res_online.status_code, 200)
        self.assertContains(res_online, "Kiosk Ready")

    def test_officer_can_login_when_election_is_draft_and_dashboard_is_blurred(self):
        """In DRAFT status, officer can log in, but dashboard is blurred with 'Election Not Active Yet' overlay."""
        self.assertEqual(self.election.status, "DRAFT")
        client = Client()
        res_login = client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })
        self.assertEqual(res_login.status_code, 302)
        self.assertRedirects(res_login, reverse("voting:officer_dashboard"))

        # View dashboard in draft mode
        res = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "is-draft-blurred")
        self.assertContains(res, "Election Not Active Yet")
        self.assertContains(res, "Election in Draft")
        self.assertContains(res, "The voting desk is locked.")
        self.assertContains(res, "btn-draft-logout")
        self.assertContains(res, "Log Out Station")
        # Exit station topbar also remains available
        self.assertContains(res, "Exit Station")

        # Inputs and buttons are disabled in draft mode
        self.assertContains(res, 'id="officer-voter-search"')
        self.assertContains(res, 'disabled')

    def test_officer_dashboard_unblurred_when_election_is_active(self):
        """When election is ACTIVE, dashboard is unblurred, no draft overlay, and interactions are active."""
        from elections.models import ElectionStatus
        self.election.status = ElectionStatus.ACTIVE
        self.election.save()

        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })

        res = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, "is-draft-blurred")
        self.assertNotContains(res, "Election Not Active Yet")
        self.assertNotContains(res, "btn-draft-logout")

    def test_officer_login_rejected_when_election_is_closed(self):
        """Officer credentials and sessions are automatically purged when election is CLOSED."""
        from elections.models import ElectionStatus
        self.election.status = ElectionStatus.CLOSED
        self.election.save()

        client = Client()
        res = client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })
        # Credentials were automatically removed
        self.assertEqual(res.status_code, 401)
        self.assertContains(res, "Invalid identifier or password.", status_code=401)
        self.assertFalse(Device.objects.filter(id=self.officer1.id).exists())
        self.assertFalse(User.objects.filter(username=self.officer1.identifier).exists())

    def test_officer_dashboard_metadata_matches_references(self):
        """Dashboard renders dynamic registry schema fields and unselected placeholder."""
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })

        res = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "No voter selected")
        self.assertContains(res, "Student ID")
        self.assertContains(res, "CS101")
        self.assertContains(res, "Alice Smith")

    def test_officer_dashboard_css_blocks_and_no_base_chrome(self):
        """Dashboard template includes officer.css, removes base LAN header/footer, and matches references/officer_01.png filters."""
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })

        res = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'css/officer.css')
        self.assertContains(res, 'class="officer-page"')
        self.assertNotContains(res, 'app-header')
        self.assertNotContains(res, 'app-footer')

        # Filter tabs: Not voted, Voted, All (In progress tab removed, Group select removed)
        self.assertContains(res, 'data-filter="not_voted"')
        self.assertContains(res, 'data-filter="voted"')
        self.assertContains(res, 'data-filter="all"')
        self.assertNotContains(res, 'data-filter="in_progress"')
        self.assertNotContains(res, 'id="officer-group-filter"')

        # Editorial header & labels matching references/officer_01.png
        self.assertContains(res, 'Voting Desk')
        self.assertNotContains(res, 'officer-brand-label')
        self.assertContains(res, 'VOTER DETAILS')

    def test_officer_dashboard_election_closed_renders_closed_error_page(self):
        """When election is concluded, officer desk returns 403 and renders election_closed.html."""
        from elections.models import ElectionStatus
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })

        # Transition election to CLOSED without deleting the current device directly to test the view guard
        self.election.status = ElectionStatus.CLOSED
        self.election.save()

        res = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res.status_code, 403)
        self.assertTemplateUsed(res, "voting/officer/election_closed.html")
        self.assertContains(res, "Election Concluded", status_code=403)
        self.assertContains(res, "Voting for this election has officially concluded", status_code=403)
        self.assertContains(res, "Sign out & Exit", status_code=403)

    def test_officer_dashboard_unassigned_station_renders_unassigned_error_page(self):
        """When an authenticated officer device has no booth, officer desk returns 403 and renders unassigned.html."""
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })

        # Invalidate device booth to simulate revoked/invalid device state
        self.officer1.booth = None
        self.officer1.save()

        res = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res.status_code, 403)
        self.assertTemplateUsed(res, "voting/officer/session_revoked.html")
        self.assertContains(res, "Session Inactive or Revoked", status_code=403)

    def test_officer_dashboard_revoked_session_renders_session_revoked_page(self):
        """When officer device session is deactivated or revoked, accessing desk returns 403 and renders session_revoked.html."""
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })

        # Invalidate the device session
        DeviceSession.objects.filter(device=self.officer1).update(is_active=False)

        res = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res.status_code, 403)
        self.assertTemplateUsed(res, "voting/officer/session_revoked.html")
        self.assertContains(res, "Session Inactive or Revoked", status_code=403)
        self.assertContains(res, "Return to Login", status_code=403)

    def test_kiosk_locked_page_device_name_and_logout_button(self):
        """Kiosk locked page displays device identifier, logout station button, and submission error banner."""
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.kiosk1.identifier,
            "password": self.kiosk1_pass,
        })

        res = client.get(reverse("voting:kiosk"))
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "voting/kiosk/kiosk_landing.html")

        # 1. Device Name & Logout on Kiosk Locked Page
        self.assertContains(res, "Voting Station Locked")
        self.assertContains(res, self.kiosk1.identifier)
        self.assertContains(res, 'id="btn-kiosk-logout"')
        self.assertContains(res, "Log Out Station")

        # 2. Submission Error banner
        self.assertContains(res, 'id="kiosk-submission-error"')
        self.assertContains(res, "Submission failed, vote again.")

    def test_officer_dashboard_status_box_states_and_unscoped_search(self):
        """Officer dashboard template contains all required status box states and unscoped search filtering."""
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })

        res = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res.status_code, 200)

        # Status headings in template
        self.assertContains(res, "Ready to authorize")
        self.assertContains(res, "In progress")
        self.assertContains(res, "Submission error")
        self.assertContains(res, "Kiosk disconnected")
        self.assertContains(res, "Ballot recorded")
        self.assertContains(res, "Kiosk not full screen")

        # Persistent voter selection via sessionStorage
        self.assertContains(res, "officer_selected_voter_id")



class Slice10KioskAuthorizationTests(TestCase):
    """Unit and integration tests for Slice 10: Voter Authorization and Kiosk Unlocking."""

    def setUp(self):
        initialize_installation("admin_user", "admin@electra.org", "AdminPass123!")

        # 1. Create Election in DRAFT
        self.election = create_election(name="Student Senate 2026")

        # 2. Create Booths with Paired Devices while in DRAFT
        b1_res = create_booth(election_id=self.election.id, name="Booth 1")
        self.booth1 = b1_res["booth"]
        self.officer1 = b1_res["officer_device"]
        self.officer1_pass = b1_res["officer_password"]
        self.kiosk1 = b1_res["kiosk_device"]
        self.kiosk1_pass = b1_res["kiosk_password"]

        b2_res = create_booth(election_id=self.election.id, name="Booth 2")
        self.booth2 = b2_res["booth"]
        self.officer2 = b2_res["officer_device"]
        self.kiosk2 = b2_res["kiosk_device"]

        # 3. Create Voters and enroll
        self.reg = VoterRegistry.objects.create(name="Senate Registry", primary_id_source="Student ID")
        self.v1 = Voter.objects.create(registry=self.reg, primary_registry_value="SEN-101", name="Alice Voter")
        self.v2 = Voter.objects.create(registry=self.reg, primary_registry_value="SEN-102", name="Bob Voter")

        self.ev1 = ElectionVoter.objects.create(election=self.election, voter=self.v1, booth=self.booth1, has_voted=False)
        self.ev2 = ElectionVoter.objects.create(election=self.election, voter=self.v2, booth=self.booth2, has_voted=False)

        # 4. Activate election
        self.election.status = ElectionStatus.ACTIVE
        self.election.save()

    def test_create_authorization_service_success(self):
        """Authorizing an eligible, enrolled booth voter creates single ACTIVE VoterAuthorization."""
        from voting.services import create_authorization
        from voting.models import AuthorizationStatus, VoterAuthorization

        create_device_session(self.kiosk1, "kiosk-sess-1")

        auth = create_authorization(self.officer1, "SEN-101")
        self.assertIsNotNone(auth)
        self.assertEqual(auth.status, AuthorizationStatus.ACTIVE)
        self.assertEqual(auth.election_voter, self.ev1)
        self.assertEqual(auth.booth, self.booth1)
        self.assertEqual(auth.kiosk, self.kiosk1)

        # Unique active authorization constraint holds
        active_count = VoterAuthorization.objects.filter(election_voter=self.ev1, status=AuthorizationStatus.ACTIVE).count()
        self.assertEqual(active_count, 1)

    def test_create_authorization_cross_booth_rejected(self):
        """Officer at Booth 1 cannot authorize a voter assigned to Booth 2 (Booth Isolation)."""
        from django.core.exceptions import ValidationError
        from voting.services import create_authorization

        create_device_session(self.kiosk1, "kiosk-sess-2")

        with self.assertRaises(ValidationError) as ctx:
            create_authorization(self.officer1, "SEN-102")
        self.assertIn("Cross-booth authorization rejected", str(ctx.exception))

    def test_create_authorization_already_voted_rejected(self):
        """A voter who has already voted cannot be authorized."""
        from django.core.exceptions import ValidationError
        from voting.services import create_authorization

        create_device_session(self.kiosk1, "kiosk-sess-3")
        self.ev1.has_voted = True
        self.ev1.save()

        with self.assertRaises(ValidationError) as ctx:
            create_authorization(self.officer1, "SEN-101")
        self.assertIn("already cast their ballot", str(ctx.exception))

    def test_create_authorization_duplicate_active_rejected(self):
        """Cannot issue a second active authorization while one is already active on this kiosk/voter."""
        from django.core.exceptions import ValidationError
        from voting.services import create_authorization

        create_device_session(self.kiosk1, "kiosk-sess-4")
        create_authorization(self.officer1, "SEN-101")

        with self.assertRaises(ValidationError) as ctx:
            create_authorization(self.officer1, "SEN-101")
        self.assertIn("already exists", str(ctx.exception))

    def test_cancel_authorization_service(self):
        """Officer can cancel an active authorization, freeing the kiosk."""
        from voting.models import AuthorizationStatus
        from voting.services import cancel_authorization, create_authorization

        create_device_session(self.kiosk1, "kiosk-sess-5")
        auth = create_authorization(self.officer1, "SEN-101")
        cancelled_auth = cancel_authorization(self.officer1, auth.id)
        self.assertEqual(cancelled_auth.status, AuthorizationStatus.CANCELLED)
        self.assertIsNotNone(cancelled_auth.cancelled_at)

    def test_officer_authorize_view_endpoint(self):
        """POST /voting/officer/authorize/ successfully returns JSON and creates authorization."""
        from voting.models import AuthorizationStatus, VoterAuthorization

        # Ensure paired Kiosk 1 has an active session
        create_device_session(self.kiosk1, "kiosk-sess-endpoint")

        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })

        import json
        res = client.post(
            reverse("voting:officer_authorize"),
            json.dumps({"voter_id": "SEN-101"}),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("authorization_id", data)

        # Verify record in database
        auth = VoterAuthorization.objects.get(id=data["authorization_id"])
        self.assertEqual(auth.status, AuthorizationStatus.ACTIVE)

    def test_kiosk_view_locked_when_no_active_authorization(self):
        """Kiosk view renders with is_locked=True when no authorization is active."""
        client = Client()
        login_res = client.post(reverse("accounts:login"), {
            "username": self.kiosk1.identifier,
            "password": self.kiosk1_pass,
        })
        self.assertEqual(login_res.status_code, 302)

        res = client.get(reverse("voting:kiosk"))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.context["is_locked"])
        self.assertContains(res, "Voting Station Locked")

    def test_kiosk_view_unlocked_when_active_authorization_exists(self):
        """Kiosk view renders with is_locked=False when an active authorization exists."""
        from voting.services import create_authorization

        # Kiosk logs in first
        client = Client()
        login_res = client.post(reverse("accounts:login"), {
            "username": self.kiosk1.identifier,
            "password": self.kiosk1_pass,
        })
        self.assertEqual(login_res.status_code, 302)

        # Officer authorizes voter for this now-connected kiosk
        create_authorization(self.officer1, "SEN-101")

        res = client.get(reverse("voting:kiosk"))
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.context["is_locked"])
        self.assertContains(res, "Station Unlocked & Ready")


class Slice11BallotSubmissionTests(TestCase):
    """Tests for Slice 11 & 12: Ballot Interface, Selections, and Atomic Submission."""

    def setUp(self):
        self.admin = initialize_installation("electra_admin", "admin@school.edu", "Password123!")
        self.election = create_election(name="Campus Union 2026")

        b_res = create_booth(election_id=self.election.id, name="Main Gym")
        self.booth = b_res["booth"]
        self.officer = b_res["officer_device"]
        self.kiosk = b_res["kiosk_device"]
        self.kiosk_pass = b_res["kiosk_password"]

        self.reg = VoterRegistry.objects.create(name="Campus Registry", primary_id_source="Student ID")
        self.v1 = Voter.objects.create(registry=self.reg, primary_registry_value="STU-001", name="Sarah Student")
        self.ev1 = ElectionVoter.objects.create(election=self.election, voter=self.v1, booth=self.booth, has_voted=False)

        # Create Positions
        from elections.models import Candidate, Position
        # Contested Position 1: Chairperson (2 candidates)
        self.pos_chair = Position.objects.create(election=self.election, name="Chairperson", display_order=1)
        self.cand_chair_1 = Candidate.objects.create(position=self.pos_chair, name="Alice Adams", symbol="Star")
        self.cand_chair_2 = Candidate.objects.create(position=self.pos_chair, name="Bob Brown", symbol="Tree")

        # Contested Position 2: Secretary (2 candidates)
        self.pos_sec = Position.objects.create(election=self.election, name="Secretary", display_order=2)
        self.cand_sec_1 = Candidate.objects.create(position=self.pos_sec, name="Clara Clark", symbol="Book")
        self.cand_sec_2 = Candidate.objects.create(position=self.pos_sec, name="Daniel Davis", symbol="Sun")

        # Uncontested Position 3: Treasurer (1 candidate — Unanimous win, must be skipped on kiosk)
        self.pos_treas = Position.objects.create(election=self.election, name="Treasurer", display_order=3)
        self.cand_treas = Candidate.objects.create(position=self.pos_treas, name="Evan Evans", symbol="Coin")

        # Activate election
        self.election.status = ElectionStatus.ACTIVE
        self.election.save()

    def test_kiosk_ballot_skips_uncontested_positions(self):
        """Kiosk ballot filters out single-candidate positions which win unanimously."""
        client = Client()
        login_res = client.post(reverse("accounts:login"), {
            "username": self.kiosk.identifier,
            "password": self.kiosk_pass,
        })
        self.assertEqual(login_res.status_code, 302)

        from voting.services import create_authorization
        create_authorization(self.officer, "STU-001")

        res = client.get(reverse("voting:kiosk"))
        self.assertEqual(res.status_code, 200)

        # 3 positions exist in total, but Treasurer has only 1 candidate, so only 2 contested positions on kiosk
        self.assertEqual(res.context["total_positions"], 2)
        pos_names = [p.name for p in res.context["positions"]]
        self.assertIn("Chairperson", pos_names)
        self.assertIn("Secretary", pos_names)
        self.assertNotIn("Treasurer", pos_names)

        # In positions_json, candidate data strictly contains name, symbol, and image keys — no party or group
        p_json = res.context["positions_json"]
        self.assertEqual(len(p_json), 2)
        cands_chair = p_json[0]["candidates"]
        self.assertEqual(len(cands_chair), 2)
        self.assertEqual(cands_chair[0]["name"], "Alice Adams")
        self.assertEqual(cands_chair[0]["symbol"], "Star")

    def test_submit_ballot_service_atomic_success(self):
        """Transactional ballot submission records anonymous votes and consumes authorization."""
        create_device_session(self.kiosk, "kiosk-sess-submit-1")
        from voting.models import AuthorizationStatus, Vote
        from voting.services import create_authorization, submit_ballot

        auth = create_authorization(self.officer, "STU-001")

        result = submit_ballot(self.kiosk, auth.id, {
            str(self.pos_chair.id): self.cand_chair_1.id,
            str(self.pos_sec.id): self.cand_sec_2.id,
        })

        self.assertTrue(result["success"])
        self.assertEqual(result["votes_recorded"], 2)

        # Verify Votes created anonymously in DB
        votes = Vote.objects.filter(election=self.election)
        self.assertEqual(votes.count(), 2)
        voted_cand_ids = set(votes.values_list("candidate_id", flat=True))
        self.assertEqual(voted_cand_ids, {self.cand_chair_1.id, self.cand_sec_2.id})

        # STRICT BALLOT SECRECY: Vote model has no voter foreign key
        for v in votes:
            self.assertFalse(hasattr(v, "voter"))
            self.assertFalse(hasattr(v, "election_voter"))
            self.assertFalse(hasattr(v, "authorization"))

        # Verify Voter state
        self.ev1.refresh_from_db()
        self.assertTrue(self.ev1.has_voted)

        # Verify Authorization consumed
        auth.refresh_from_db()
        self.assertEqual(auth.status, AuthorizationStatus.USED)
        self.assertIsNotNone(auth.used_at)

    def test_submit_ballot_missing_position_rejected_and_rolled_back(self):
        """Submitting a partial ballot missing a contested position is rejected with zero votes cast."""
        create_device_session(self.kiosk, "kiosk-sess-submit-2")
        from django.core.exceptions import ValidationError
        from voting.models import AuthorizationStatus, Vote
        from voting.services import create_authorization, submit_ballot

        auth = create_authorization(self.officer, "STU-001")

        # Only select for Chairperson, missing Secretary
        with self.assertRaises(ValidationError) as ctx:
            submit_ballot(self.kiosk, auth.id, {
                str(self.pos_chair.id): self.cand_chair_1.id,
            })
        self.assertIn("Missing candidate selection for position 'Secretary'", str(ctx.exception))

        # Atomicity verified: 0 votes created
        self.assertEqual(Vote.objects.filter(election=self.election).count(), 0)
        self.ev1.refresh_from_db()
        self.assertFalse(self.ev1.has_voted)
        auth.refresh_from_db()
        self.assertEqual(auth.status, AuthorizationStatus.ACTIVE)

    def test_submit_ballot_invalid_candidate_rejected(self):
        """Submitting a candidate belonging to a different position is rejected."""
        create_device_session(self.kiosk, "kiosk-sess-submit-3")
        from django.core.exceptions import ValidationError
        from voting.models import Vote
        from voting.services import create_authorization, submit_ballot

        auth = create_authorization(self.officer, "STU-001")

        with self.assertRaises(ValidationError) as ctx:
            submit_ballot(self.kiosk, auth.id, {
                str(self.pos_chair.id): self.cand_sec_1.id,  # Invalid: Clara is for Secretary, not Chair!
                str(self.pos_sec.id): self.cand_sec_2.id,
            })
        self.assertIn("Candidate not valid for position 'Chairperson'", str(ctx.exception))
        self.assertEqual(Vote.objects.filter(election=self.election).count(), 0)

    def test_submit_ballot_view_http_endpoint(self):
        """POST /voting/ballot/submit/ successfully records ballot via HTTP."""
        client = Client()
        login_res = client.post(reverse("accounts:login"), {
            "username": self.kiosk.identifier,
            "password": self.kiosk_pass,
        })
        self.assertEqual(login_res.status_code, 302)

        from voting.models import AuthorizationStatus, Vote
        from voting.services import create_authorization

        auth = create_authorization(self.officer, "STU-001")

        import json
        payload = {
            "authorization_id": auth.id,
            "selections": {
                str(self.pos_chair.id): self.cand_chair_2.id,
                str(self.pos_sec.id): self.cand_sec_1.id,
            }
        }

        res = client.post(
            reverse("voting:submit_ballot"),
            json.dumps(payload),
            content_type="application/json"
        )

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["votes_recorded"], 2)

        # Database state updated
        self.assertEqual(Vote.objects.filter(election=self.election).count(), 2)
        auth.refresh_from_db()
        self.assertEqual(auth.status, AuthorizationStatus.USED)

    def test_submit_ballot_duplicate_submission_blocked(self):
        """Once a ballot is submitted and authorization is USED, resubmitting is blocked."""
        client = Client()
        login_res = client.post(reverse("accounts:login"), {
            "username": self.kiosk.identifier,
            "password": self.kiosk_pass,
        })
        self.assertEqual(login_res.status_code, 302)

        from voting.models import Vote
        from voting.services import create_authorization

        auth = create_authorization(self.officer, "STU-001")

        import json
        payload = {
            "authorization_id": auth.id,
            "selections": {
                str(self.pos_chair.id): self.cand_chair_1.id,
                str(self.pos_sec.id): self.cand_sec_1.id,
            }
        }

        # First submission succeeds
        res1 = client.post(reverse("voting:submit_ballot"), json.dumps(payload), content_type="application/json")
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(Vote.objects.count(), 2)

        # Second submission fails
        res2 = client.post(reverse("voting:submit_ballot"), json.dumps(payload), content_type="application/json")
        self.assertEqual(res2.status_code, 400)
        data = res2.json()
        self.assertFalse(data["success"])
        self.assertIn("cannot be used", data["error"])
        # No extra votes created
        self.assertEqual(Vote.objects.count(), 2)


class Slice12AtomicVotingTests(TestCase):
    """Tests for Slice 12: Atomic Vote Recording, Eligibility Enforcement, and Concurrency."""

    def setUp(self):
        initialize_installation("electra_admin", "admin@electra.edu", "AdminPass123!")
        self.election = create_election(name="General Campus Election 2026")

        b1_res = create_booth(election_id=self.election.id, name="Booth 1")
        self.booth1 = b1_res["booth"]
        self.officer1 = b1_res["officer_device"]
        self.officer1_pass = b1_res["officer_password"]
        self.kiosk1 = b1_res["kiosk_device"]
        self.kiosk1_pass = b1_res["kiosk_password"]

        b2_res = create_booth(election_id=self.election.id, name="Booth 2")
        self.booth2 = b2_res["booth"]
        self.officer2 = b2_res["officer_device"]
        self.kiosk2 = b2_res["kiosk_device"]
        self.kiosk2_pass = b2_res["kiosk_password"]

        # Academic groups
        self.reg = VoterRegistry.objects.create(name="Campus Registry", primary_id_source="Roll No")
        self.dept_cs = AcademicGroup.objects.create(registry=self.reg, name="Computer Science")
        self.dept_mech = AcademicGroup.objects.create(registry=self.reg, name="Mechanical Engineering")

        # Voters
        # Voter 1: Female in CS
        self.v1 = Voter.objects.create(
            registry=self.reg, primary_registry_value="CS-001", name="Alice CS",
            gender="FEMALE", academic_group=self.dept_cs
        )
        self.ev1 = ElectionVoter.objects.create(election=self.election, voter=self.v1, booth=self.booth1, has_voted=False)

        # Voter 2: Male in Mech
        self.v2 = Voter.objects.create(
            registry=self.reg, primary_registry_value="ME-002", name="Bob Mech",
            gender="MALE", academic_group=self.dept_mech
        )
        self.ev2 = ElectionVoter.objects.create(election=self.election, voter=self.v2, booth=self.booth2, has_voted=False)

        # Positions:
        from elections.models import Candidate, Position
        # 1. Open to ALL
        self.pos_open = Position.objects.create(election=self.election, name="President", display_order=1)
        self.cand_open_1 = Candidate.objects.create(position=self.pos_open, name="Pres Cand A", symbol="Flag")
        self.cand_open_2 = Candidate.objects.create(position=self.pos_open, name="Pres Cand B", symbol="Torch")

        # 2. Gender restricted: FEMALE only
        self.pos_women = Position.objects.create(
            election=self.election, name="Women Representative", eligible_gender="FEMALE", display_order=2
        )
        self.cand_women_1 = Candidate.objects.create(position=self.pos_women, name="Wom Cand A", symbol="Rose")
        self.cand_women_2 = Candidate.objects.create(position=self.pos_women, name="Wom Cand B", symbol="Lotus")

        # 3. Group restricted: Computer Science only
        self.pos_cs = Position.objects.create(
            election=self.election, name="CS Dept Representative", display_order=3
        )
        self.pos_cs.eligible_groups.add(self.dept_cs)
        self.cand_cs_1 = Candidate.objects.create(position=self.pos_cs, name="CS Cand A", symbol="Chip")
        self.cand_cs_2 = Candidate.objects.create(position=self.pos_cs, name="CS Cand B", symbol="Code")

        # Activate election
        self.election.status = ElectionStatus.ACTIVE
        self.election.save()

    def test_voter_eligibility_filtering_and_submission(self):
        """Voter 1 (Female CS) is eligible for all 3 positions; Voter 2 (Male Mech) only for President."""
        from voting.services import create_authorization, submit_ballot
        create_device_session(self.kiosk1, "kiosk-sess-elig-1")
        create_device_session(self.kiosk2, "kiosk-sess-elig-2")

        # Voter 1 on Kiosk 1: eligible for all 3 positions
        auth1 = create_authorization(self.officer1, "CS-001")
        res1 = submit_ballot(self.kiosk1, auth1.id, {
            str(self.pos_open.id): self.cand_open_1.id,
            str(self.pos_women.id): self.cand_women_1.id,
            str(self.pos_cs.id): self.cand_cs_1.id,
        })
        self.assertTrue(res1["success"])
        self.assertEqual(res1["votes_recorded"], 3)

        # Voter 2 on Kiosk 2: Male Mech is only eligible for pos_open
        auth2 = create_authorization(self.officer2, "ME-002")

        # If Voter 2 tries to submit a vote for pos_women (Female only), it must be rejected!
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            submit_ballot(self.kiosk2, auth2.id, {
                str(self.pos_open.id): self.cand_open_2.id,
                str(self.pos_women.id): self.cand_women_2.id,
            })
        self.assertIn("not eligible to vote for position 'Women Representative'", str(ctx.exception))

        # Voter 2 submitting strictly their eligible position succeeds
        res2 = submit_ballot(self.kiosk2, auth2.id, {
            str(self.pos_open.id): self.cand_open_2.id,
        })
        self.assertTrue(res2["success"])
        self.assertEqual(res2["votes_recorded"], 1)

    def test_ballot_atomicity_full_rollback_on_candidate_mismatch(self):
        """If one position candidate is invalid, entire ballot is rolled back with zero votes recorded."""
        from django.core.exceptions import ValidationError
        from voting.models import Vote
        from voting.services import create_authorization, submit_ballot
        create_device_session(self.kiosk1, "kiosk-sess-rollback")

        auth = create_authorization(self.officer1, "CS-001")
        initial_votes = Vote.objects.count()

        with self.assertRaises(ValidationError):
            submit_ballot(self.kiosk1, auth.id, {
                str(self.pos_open.id): self.cand_open_1.id,
                str(self.pos_women.id): self.cand_open_2.id,  # Invalid: Pres Cand B is not a Women Rep candidate!
                str(self.pos_cs.id): self.cand_cs_1.id,
            })

        # Rollback: no new votes created
        self.assertEqual(Vote.objects.count(), initial_votes)
        self.ev1.refresh_from_db()
        self.assertFalse(self.ev1.has_voted)

    def test_submission_rejected_when_election_concluded(self):
        """Cannot submit ballot if election has been closed."""
        from django.core.exceptions import ValidationError
        from elections.models import ElectionStatus
        from voting.services import create_authorization, submit_ballot
        create_device_session(self.kiosk1, "kiosk-sess-closed")

        auth = create_authorization(self.officer1, "CS-001")

        # Conclude election
        self.election.status = ElectionStatus.CLOSED
        self.election.save()

        with self.assertRaises(ValidationError) as ctx:
            submit_ballot(self.kiosk1, auth.id, {
                str(self.pos_open.id): self.cand_open_1.id,
                str(self.pos_women.id): self.cand_women_1.id,
                str(self.pos_cs.id): self.cand_cs_1.id,
            })
        self.assertIn("Election is not currently active", str(ctx.exception))

    def test_cross_booth_submission_rejected(self):
        """Kiosk at Booth 1 cannot submit ballot using an authorization issued for Booth 2."""
        from django.core.exceptions import ValidationError
        from voting.services import create_authorization, submit_ballot
        create_device_session(self.kiosk1, "kiosk-sess-cross-1")
        create_device_session(self.kiosk2, "kiosk-sess-cross-2")

        # Officer 2 issues authorization for Voter 2 at Booth 2
        auth2 = create_authorization(self.officer2, "ME-002")

        # Kiosk 1 tries to submit auth2
        with self.assertRaises(ValidationError) as ctx:
            submit_ballot(self.kiosk1, auth2.id, {
                str(self.pos_open.id): self.cand_open_2.id,
            })
        self.assertIn("Authorization not found for this Voting Kiosk", str(ctx.exception))

    def test_officer_dashboard_reflects_active_authorization(self):
        """Dashboard passes active_auth_id, active_voter_id, and sets in_progress_count when auth is active."""
        from voting.services import create_authorization
        create_device_session(self.kiosk1, "kiosk-sess-active-test")
        auth = create_authorization(self.officer1, "CS-001")

        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })
        res = client.get(reverse("voting:officer_dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["active_auth_id"], auth.id)
        self.assertEqual(res.context["active_voter_id"], "CS-001")
        self.assertEqual(res.context["in_progress_count"], 1)

    def test_officer_cancel_authorization_endpoint(self):
        """Officer can cancel active authorization via officer_cancel_authorization endpoint."""
        from voting.models import AuthorizationStatus, VoterAuthorization
        from voting.services import create_authorization
        create_device_session(self.kiosk1, "kiosk-sess-cancel-test")
        auth = create_authorization(self.officer1, "CS-001")

        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })
        res = client.post(
            reverse("voting:officer_cancel_authorization"),
            data='{"authorization_id": %d}' % auth.id,
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])

        auth.refresh_from_db()
        self.assertEqual(auth.status, AuthorizationStatus.CANCELLED)


from django.test import Client, TestCase, TransactionTestCase


class KioskAndOfficerWebSocketConsumerTests(TransactionTestCase):
    """Verifies KioskConsumer and OfficerConsumer WebSocket lifecycles and event handling."""

    def setUp(self):
        from accounts.services import initialize_installation, create_device_session
        from elections.services import create_election
        from voters.services import create_booth

        self.admin = initialize_installation("ws_admin_u", "ws_admin@inst.edu", "pass1234")
        self.election = create_election(name="WS Test Election")
        booth_res = create_booth(election_id=self.election.id, name="WS Booth")
        self.booth = booth_res["booth"]
        self.officer_device = booth_res["officer_device"]
        self.kiosk_device = booth_res["kiosk_device"]
        self.officer_user = self.officer_device.user
        self.kiosk_user = self.kiosk_device.user

    def tearDown(self):
        from django.db import connections
        connections.close_all()

    def test_kiosk_consumer_rejects_unauthenticated_user(self):
        import asyncio
        from channels.testing import WebsocketCommunicator
        from voting.consumers import KioskConsumer

        async def _test():
            communicator = WebsocketCommunicator(KioskConsumer.as_asgi(), "/ws/kiosk/")
            connected, close_code = await communicator.connect()
            self.assertFalse(connected)
            self.assertEqual(close_code, 4003)

        asyncio.run(_test())

    def test_kiosk_consumer_rejects_wrong_role(self):
        import asyncio
        from channels.testing import WebsocketCommunicator
        from voting.consumers import KioskConsumer

        async def _test():
            communicator = WebsocketCommunicator(KioskConsumer.as_asgi(), "/ws/kiosk/")
            communicator.scope["user"] = self.officer_user  # Officer trying to connect to Kiosk WS
            connected, close_code = await communicator.connect()
            self.assertFalse(connected)
            self.assertEqual(close_code, 4003)

        asyncio.run(_test())

    def test_kiosk_consumer_resilient_to_booth_events(self):
        """KioskConsumer ignores authorization.updated and ballot.recorded without crashing or disconnecting."""
        import asyncio
        from channels.testing import WebsocketCommunicator
        from channels.layers import get_channel_layer
        from voting.consumers import KioskConsumer

        async def _test():
            communicator = WebsocketCommunicator(KioskConsumer.as_asgi(), "/ws/kiosk/")
            communicator.scope["user"] = self.kiosk_user
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            channel_layer = get_channel_layer()

            # 1. Send authorization.updated to booth group (previously caused AttributeError / disconnect)
            await channel_layer.group_send(
                f"booth_{self.booth.id}",
                {
                    "type": "authorization.updated",
                    "voter_id": "V-123",
                    "status": "in_progress",
                }
            )

            # 2. Send ballot.recorded to booth group (previously caused AttributeError / disconnect)
            await channel_layer.group_send(
                f"booth_{self.booth.id}",
                {
                    "type": "ballot.recorded",
                }
            )

            # 3. Send kiosk.unlock to kiosk group -> Kiosk must still be connected and receive it!
            await channel_layer.group_send(
                f"kiosk_{self.kiosk_device.id}",
                {
                    "type": "kiosk.unlock",
                    "authorization_id": 42,
                    "eligible_position_ids": [101, 102],
                }
            )

            response = await communicator.receive_json_from(timeout=3)
            self.assertEqual(response["event"], "kiosk.unlock")
            self.assertEqual(response["authorization_id"], 42)
            self.assertEqual(response["eligible_position_ids"], [101, 102])

            # 4. Send kiosk.lock
            await channel_layer.group_send(
                f"kiosk_{self.kiosk_device.id}",
                {
                    "type": "kiosk.lock",
                }
            )
            response_lock = await communicator.receive_json_from(timeout=3)
            self.assertEqual(response_lock["event"], "kiosk.lock")

            await communicator.disconnect()

        asyncio.run(_test())

    def test_officer_consumer_receives_status_and_updates(self):
        """OfficerConsumer connects, receives initial kiosk status, and receives authorization updates."""
        import asyncio
        from accounts.services import create_device_session
        from channels.testing import WebsocketCommunicator
        from channels.layers import get_channel_layer
        from voting.consumers import OfficerConsumer

        create_device_session(self.kiosk_device, "kiosk-sess-ws-online")

        async def _test():
            communicator = WebsocketCommunicator(OfficerConsumer.as_asgi(), "/ws/officer/")
            communicator.scope["user"] = self.officer_user
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            # Officer receives initial kiosk.status
            initial_status = await communicator.receive_json_from(timeout=3)
            self.assertEqual(initial_status["event"], "kiosk.status")
            self.assertTrue(initial_status["online"])

            channel_layer = get_channel_layer()

            # Send authorization.updated
            await channel_layer.group_send(
                f"booth_officer_{self.booth.id}",
                {
                    "type": "authorization.updated",
                    "voter_id": "V-999",
                    "status": "in_progress",
                }
            )
            msg_auth = await communicator.receive_json_from(timeout=3)
            self.assertEqual(msg_auth["event"], "authorization.updated")
            self.assertEqual(msg_auth["voter_id"], "V-999")
            self.assertEqual(msg_auth["status"], "in_progress")

            # Send ballot.recorded
            await channel_layer.group_send(
                f"booth_officer_{self.booth.id}",
                {
                    "type": "ballot.recorded",
                }
            )
            msg_ballot = await communicator.receive_json_from(timeout=3)
            self.assertEqual(msg_ballot["event"], "ballot.recorded")

            await communicator.disconnect()

        asyncio.run(_test())


class ElectionClosingAndMultiBoothScenariosTests(TestCase):
    """Verifies Electra's lazy closing logic, multi-booth independence, and countdown state.
    
    Covers the 6 required multi-booth scenarios:
    1. Voter A is authorized at 5:59 PM and submits at 6:01 PM -> accepted.
    2. Officer attempts to authorize Voter B at 6:01 PM -> rejected.
    3. Voter A is active in Booth 1 while an officer attempts an authorization after closing in Booth 2 -> Voter A remains unaffected.
    4. Multiple voters authorized before closing can all finish independently after end_time.
    5. After the last active voting session finishes, the election can be persisted as CLOSED.
    6. Refreshing the Officer Station after the end time correctly shows the ended state and prevents new authorization.
    """

    def setUp(self):
        import datetime
        from django.core.cache import cache
        from elections.models import Candidate, Position
        cache.clear()

        # Single admin initialization
        self.admin = initialize_installation("electra_admin", "admin@inst.edu", "admin_pass_1234")

        # Fixed timestamps: 5:00 PM start, 6:00 PM end
        self.t_start = datetime.datetime(2026, 10, 7, 17, 0, 0, tzinfo=datetime.timezone.utc)
        self.t_before = datetime.datetime(2026, 10, 7, 17, 59, 0, tzinfo=datetime.timezone.utc)
        self.t_close = datetime.datetime(2026, 10, 7, 18, 0, 0, tzinfo=datetime.timezone.utc)
        self.t_after = datetime.datetime(2026, 10, 7, 18, 1, 0, tzinfo=datetime.timezone.utc)
        self.t_later = datetime.datetime(2026, 10, 7, 18, 5, 0, tzinfo=datetime.timezone.utc)

        # Election with starts_at and ends_at
        self.election = create_election(
            name="Student Council 2026",
            starts_at=self.t_start,
            ends_at=self.t_close,
        )

        # Contested Position & Candidates
        self.pos = Position.objects.create(election=self.election, name="President", display_order=1)
        self.cand_1 = Candidate.objects.create(position=self.pos, name="Candidate Alpha", symbol="Star")
        self.cand_2 = Candidate.objects.create(position=self.pos, name="Candidate Beta", symbol="Moon")

        # Booth 1 & Booth 2
        booth1_res = create_booth(election_id=self.election.id, name="Booth 01")
        self.booth1 = booth1_res["booth"]
        self.officer1 = booth1_res["officer_device"]
        self.officer1_pass = booth1_res["officer_password"]
        self.kiosk1 = booth1_res["kiosk_device"]
        self.kiosk1_pass = booth1_res["kiosk_password"]

        booth2_res = create_booth(election_id=self.election.id, name="Booth 02")
        self.booth2 = booth2_res["booth"]
        self.officer2 = booth2_res["officer_device"]
        self.officer2_pass = booth2_res["officer_password"]
        self.kiosk2 = booth2_res["kiosk_device"]
        self.kiosk2_pass = booth2_res["kiosk_password"]

        # Ensure active sessions & kiosk readiness
        create_device_session(self.kiosk1, "kiosk-1-session")
        create_device_session(self.kiosk2, "kiosk-2-session")
        cache.set(f"kiosk_online_{self.booth1.id}", True)
        cache.set(f"kiosk_fullscreen_{self.booth1.id}", True)
        cache.set(f"kiosk_online_{self.booth2.id}", True)
        cache.set(f"kiosk_fullscreen_{self.booth2.id}", True)

        # Voter Registry & Voters
        self.registry = VoterRegistry.objects.create(name="Campus Registry 2026")
        self.voter_a = Voter.objects.create(registry=self.registry, primary_registry_value="VOTER-A", name="Alice Anderson")
        self.voter_b = Voter.objects.create(registry=self.registry, primary_registry_value="VOTER-B", name="Bob Brown")
        self.voter_c = Voter.objects.create(registry=self.registry, primary_registry_value="VOTER-C", name="Charlie Clark")
        self.voter_d = Voter.objects.create(registry=self.registry, primary_registry_value="VOTER-D", name="Diana Davis")

        # Enroll Voters into Election and allocate to booths
        self.ev_a = ElectionVoter.objects.create(election=self.election, voter=self.voter_a, booth=self.booth1)
        self.ev_c = ElectionVoter.objects.create(election=self.election, voter=self.voter_c, booth=self.booth1)

        self.ev_b = ElectionVoter.objects.create(election=self.election, voter=self.voter_b, booth=self.booth2)
        self.ev_d = ElectionVoter.objects.create(election=self.election, voter=self.voter_d, booth=self.booth2)

        # Activate election
        self.election.status = ElectionStatus.ACTIVE
        self.election.save(update_fields=["status"])

    def test_scenario_1_voter_authorized_before_end_submits_after_end(self):
        """1. Voter A is authorized at 5:59 PM and submits at 6:01 PM -> accepted."""
        from unittest.mock import patch
        from voting.models import AuthorizationStatus, Vote
        from voting.services import create_authorization, submit_ballot

        # Authorized at 5:59 PM
        with patch("django.utils.timezone.now", return_value=self.t_before):
            auth_a = create_authorization(self.officer1, self.voter_a.primary_registry_value)
            self.assertEqual(auth_a.status, AuthorizationStatus.ACTIVE)

        # Advance server time past election.ends_at (6:01 PM) and submit ballot
        with patch("django.utils.timezone.now", return_value=self.t_after):
            res = submit_ballot(self.kiosk1, auth_a.id, {str(self.pos.id): self.cand_1.id})
            self.assertTrue(res["success"])
            self.assertEqual(res["votes_recorded"], 1)

            auth_a.refresh_from_db()
            self.assertEqual(auth_a.status, AuthorizationStatus.USED)
            self.ev_a.refresh_from_db()
            self.assertTrue(self.ev_a.has_voted)

            votes = Vote.objects.filter(election=self.election)
            self.assertEqual(votes.count(), 1)
            self.assertEqual(votes.first().candidate_id, self.cand_1.id)

    def test_scenario_2_new_authorization_after_end_rejected(self):
        """2. Officer attempts to authorize Voter B at 6:01 PM -> rejected."""
        from unittest.mock import patch
        from django.core.exceptions import ValidationError
        from voting.models import VoterAuthorization
        from voting.services import create_authorization

        # Direct Service Call at 6:01 PM
        with patch("django.utils.timezone.now", return_value=self.t_after):
            with self.assertRaises(ValidationError) as ctx:
                create_authorization(self.officer1, self.voter_c.primary_registry_value)
            self.assertIn("concluded", str(ctx.exception).lower())

            # Verify no authorization row was created
            self.assertFalse(
                VoterAuthorization.objects.filter(election_voter=self.ev_c).exists()
            )

        # HTTP Endpoint Call at 6:01 PM
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })

        with patch("django.utils.timezone.now", return_value=self.t_after):
            res = client.post(
                reverse("voting:officer_authorize"),
                data='{"voter_id": "VOTER-C"}',
                content_type="application/json"
            )
            self.assertEqual(res.status_code, 400)
            data = res.json()
            self.assertFalse(data["success"])
            self.assertIn("concluded", data["error"].lower())

    def test_scenario_3_booth2_post_end_rejection_does_not_affect_active_booth1_voter(self):
        """3. Voter A is active in Booth 1 while an officer attempts authorization after closing in Booth 2 -> Voter A remains unaffected."""
        import datetime
        from unittest.mock import patch
        from django.core.exceptions import ValidationError
        from voting.models import AuthorizationStatus
        from voting.services import create_authorization, submit_ballot

        # 5:59 PM: Voter A authorized in Booth 1
        with patch("django.utils.timezone.now", return_value=self.t_before):
            auth_a = create_authorization(self.officer1, self.voter_a.primary_registry_value)
            self.assertEqual(auth_a.status, AuthorizationStatus.ACTIVE)

        # 6:01 PM: Officer in Booth 2 attempts to authorize Voter B -> rejected
        with patch("django.utils.timezone.now", return_value=self.t_after):
            with self.assertRaises(ValidationError):
                create_authorization(self.officer2, self.voter_b.primary_registry_value)

            # Assert Voter A in Booth 1 is completely unaffected
            auth_a.refresh_from_db()
            self.assertEqual(auth_a.status, AuthorizationStatus.ACTIVE)
            self.ev_a.refresh_from_db()
            self.assertFalse(self.ev_a.has_voted)

            self.election.refresh_from_db()
            self.assertEqual(self.election.status, ElectionStatus.ACTIVE)

        # 6:02 PM: Voter A in Booth 1 finishes and submits ballot successfully
        t_602 = datetime.datetime(2026, 10, 7, 18, 2, 0, tzinfo=datetime.timezone.utc)
        with patch("django.utils.timezone.now", return_value=t_602):
            res = submit_ballot(self.kiosk1, auth_a.id, {str(self.pos.id): self.cand_2.id})
            self.assertTrue(res["success"])
            auth_a.refresh_from_db()
            self.assertEqual(auth_a.status, AuthorizationStatus.USED)
            self.ev_a.refresh_from_db()
            self.assertTrue(self.ev_a.has_voted)

    def test_scenario_4_multiple_voters_finish_independently_after_end_time(self):
        """4. Multiple voters authorized before closing can all finish independently after end_time."""
        import datetime
        from unittest.mock import patch
        from voting.models import AuthorizationStatus, Vote
        from voting.services import create_authorization, submit_ballot

        # 5:59 PM: Authorize Voter A in Booth 1 and Voter B in Booth 2
        with patch("django.utils.timezone.now", return_value=self.t_before):
            auth_a = create_authorization(self.officer1, self.voter_a.primary_registry_value)
            auth_b = create_authorization(self.officer2, self.voter_b.primary_registry_value)
            self.assertEqual(auth_a.status, AuthorizationStatus.ACTIVE)
            self.assertEqual(auth_b.status, AuthorizationStatus.ACTIVE)

        t_601 = datetime.datetime(2026, 10, 7, 18, 1, 0, tzinfo=datetime.timezone.utc)
        t_603 = datetime.datetime(2026, 10, 7, 18, 3, 0, tzinfo=datetime.timezone.utc)

        # Voter A finishes at 6:01 PM
        with patch("django.utils.timezone.now", return_value=t_601):
            res_a = submit_ballot(self.kiosk1, auth_a.id, {str(self.pos.id): self.cand_1.id})
            self.assertTrue(res_a["success"])

            auth_a.refresh_from_db()
            self.assertEqual(auth_a.status, AuthorizationStatus.USED)

            # Voter B in Booth 2 is STILL active and untouched
            auth_b.refresh_from_db()
            self.assertEqual(auth_b.status, AuthorizationStatus.ACTIVE)

            # Election remains ACTIVE because Voter B has not finished
            self.election.refresh_from_db()
            self.assertEqual(self.election.status, ElectionStatus.ACTIVE)

        # Voter B finishes at 6:03 PM
        with patch("django.utils.timezone.now", return_value=t_603):
            res_b = submit_ballot(self.kiosk2, auth_b.id, {str(self.pos.id): self.cand_2.id})
            self.assertTrue(res_b["success"])

            auth_b.refresh_from_db()
            self.assertEqual(auth_b.status, AuthorizationStatus.USED)

        # Both voters finished independently
        self.ev_a.refresh_from_db()
        self.ev_b.refresh_from_db()
        self.assertTrue(self.ev_a.has_voted)
        self.assertTrue(self.ev_b.has_voted)
        self.assertEqual(Vote.objects.filter(election=self.election).count(), 2)

    def test_scenario_5_election_persists_as_closed_after_last_session_finishes(self):
        """5. After the last active voting session finishes, the election can be persisted as CLOSED."""
        from unittest.mock import patch
        from elections.services import close_if_expired
        from voting.services import create_authorization, submit_ballot

        # 5:59 PM: Authorize Voter A in Booth 1 and Voter B in Booth 2
        with patch("django.utils.timezone.now", return_value=self.t_before):
            auth_a = create_authorization(self.officer1, self.voter_a.primary_registry_value)
            auth_b = create_authorization(self.officer2, self.voter_b.primary_registry_value)

        # 6:01 PM: Both are active
        with patch("django.utils.timezone.now", return_value=self.t_after):
            # Attempt lazy closing while sessions active -> returns False, election stays ACTIVE
            closed = close_if_expired(election_id=self.election.id)
            self.assertFalse(closed)
            self.election.refresh_from_db()
            self.assertEqual(self.election.status, ElectionStatus.ACTIVE)

            # Kiosk 1 finishes
            submit_ballot(self.kiosk1, auth_a.id, {str(self.pos.id): self.cand_1.id})
            self.election.refresh_from_db()
            self.assertEqual(self.election.status, ElectionStatus.ACTIVE)

            # Kiosk 2 finishes (last active session!)
            submit_ballot(self.kiosk2, auth_b.id, {str(self.pos.id): self.cand_2.id})
            self.election.refresh_from_db()
            self.assertEqual(self.election.status, ElectionStatus.CLOSED)
            self.assertIsNotNone(self.election.closed_at)

    def test_scenario_5b_cancelling_last_session_after_end_time_persists_election_closed(self):
        """5b. Cancelling the last active authorization after end_time also transitions election to CLOSED."""
        from unittest.mock import patch
        from voting.services import create_authorization, cancel_authorization

        with patch("django.utils.timezone.now", return_value=self.t_before):
            auth_a = create_authorization(self.officer1, self.voter_a.primary_registry_value)

        with patch("django.utils.timezone.now", return_value=self.t_after):
            cancel_authorization(self.officer1, auth_a.id)
            self.election.refresh_from_db()
            self.assertEqual(self.election.status, ElectionStatus.CLOSED)
            self.assertIsNotNone(self.election.closed_at)

    def test_scenario_6_refreshing_officer_station_after_end_time_shows_ended_state(self):
        """6. Refreshing the Officer Station after the end time correctly shows the ended state and prevents new authorization."""
        from unittest.mock import patch
        from voting.services import create_authorization, submit_ballot

        # Active session exists for Voter A in Booth 1
        with patch("django.utils.timezone.now", return_value=self.t_before):
            auth_a = create_authorization(self.officer1, self.voter_a.primary_registry_value)

        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.officer1.identifier,
            "password": self.officer1_pass,
        })

        # At 6:01 PM: Refresh officer dashboard while Voter A is still in-progress
        with patch("django.utils.timezone.now", return_value=self.t_after):
            res = client.get(reverse("voting:officer_dashboard"))
            self.assertEqual(res.status_code, 200)

            # Context flags
            self.assertTrue(res.context["is_ended"])
            self.assertEqual(res.context["remaining_seconds"], 0)

            content = res.content.decode("utf-8")
            self.assertIn('id="officer-countdown-pill"', content)
            self.assertIn('is-ended', content)
            self.assertIn('Election ended', content)
            self.assertIn('00:00:00', content)

            # Attempting to authorize another voter fails
            res_auth = client.post(
                reverse("voting:officer_authorize"),
                data='{"voter_id": "VOTER-C"}',
                content_type="application/json"
            )
            self.assertEqual(res_auth.status_code, 400)
            self.assertFalse(res_auth.json()["success"])

        # Once Voter A submits, election transitions to CLOSED
        with patch("django.utils.timezone.now", return_value=self.t_after):
            submit_ballot(self.kiosk1, auth_a.id, {str(self.pos.id): self.cand_1.id})
            self.election.refresh_from_db()
            self.assertEqual(self.election.status, ElectionStatus.CLOSED)

            # Now refreshing the officer dashboard renders election_closed.html (status 403)
            res_closed = client.get(reverse("voting:officer_dashboard"))
            self.assertEqual(res_closed.status_code, 403)
            self.assertTemplateUsed(res_closed, "voting/officer/election_closed.html")
