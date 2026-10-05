"""Comprehensive test suite for elections application (Phase 3).

Covers:
- Election lifecycle: DRAFT -> ACTIVE -> CLOSED -> RESULTS_PUBLISHED
- Installation boundary (no owner field)
- Configuration validation rules (readiness)
- Configuration freeze upon activation (positions and candidates locked)
- Concurrency and atomic operations
- Automatic deadline expiry (close_if_expired)
- RBAC: Administrator authorization on views
"""
from datetime import timedelta
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CredentialStatus, Device, DeviceSession, DeviceType, Role, User
from accounts.services import revoke_device_credentials, rotate_device_credentials
from elections.models import Candidate, Election, ElectionStatus, Position
from voters.models import Booth
from voters.services import create_booth
from elections.selectors import (
    get_active_election,
    get_election,
    get_election_details,
    list_elections,
)
from elections.services import (
    close_election,
    close_if_expired,
    create_candidate,
    create_election,
    create_position,
    delete_candidate,
    delete_election,
    delete_position,
    publish_results,
    start_election,
    update_candidate,
    update_election,
    update_position,
    validate_election_configuration,
)


class ElectionModelTests(TestCase):
    def test_installation_boundary_no_owner_field(self):
        """Election must belong to installation with no owner or tenant scoping."""
        fields = [f.name for f in Election._meta.get_fields()]
        self.assertNotIn("owner", fields)
        self.assertNotIn("user", fields)
        self.assertNotIn("tenant", fields)

    def test_position_and_candidate_no_description_fields(self):
        """Kiosks only need candidate identities; campaign description fields are excluded."""
        position_fields = [f.name for f in Position._meta.get_fields()]
        candidate_fields = [f.name for f in Candidate._meta.get_fields()]
        self.assertNotIn("description", position_fields)
        self.assertNotIn("description", candidate_fields)

    def test_ends_at_must_be_strictly_after_starts_at(self):
        """Election validation rejects ends_at <= starts_at."""
        now = timezone.now()
        with self.assertRaises(ValidationError):
            election = Election(
                name="Invalid Election",
                starts_at=now,
                ends_at=now - timedelta(hours=1),
            )
            election.full_clean()

    def test_unique_position_name_per_election(self):
        """Positions within the same election must have unique names."""
        now = timezone.now()
        election = create_election(
            name="Council Election",
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=2),
        )
        create_position(election_id=election.id, name="President", display_order=1)
        with self.assertRaises(ValidationError):
            create_position(election_id=election.id, name="President", display_order=2)

    def test_unique_position_order_per_election(self):
        """Display order must be unique per election to ensure unambiguous kiosk ballots."""
        now = timezone.now()
        election = create_election(
            name="Ballot Order Election",
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=2),
        )
        create_position(election_id=election.id, name="President", display_order=1)
        with self.assertRaises(ValidationError):
            create_position(election_id=election.id, name="Vice President", display_order=1)

    def test_candidate_academic_group(self):
        """Candidate records can store academic department, grade, or class details."""
        now = timezone.now()
        election = create_election(
            name="Student Reps",
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=2),
        )
        pos = create_position(election_id=election.id, name="Lead Rep", display_order=1)
        cand = create_candidate(
            position_id=pos.id,
            name="Jordan Smith",
            academic_group="Department of Computer Science - Year 3",
            symbol="Torch",
        )
        self.assertEqual(cand.academic_group, "Department of Computer Science - Year 3")

    def test_ballot_symbol_must_be_unique_within_election(self):
        """Candidates within an election cannot share the same ballot symbol."""
        now = timezone.now()
        election = create_election(
            name="Symbol Election",
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=2),
        )
        pos1 = create_position(election_id=election.id, name="President", display_order=1)
        pos2 = create_position(election_id=election.id, name="Secretary", display_order=2)
        create_candidate(position_id=pos1.id, name="Alice", symbol="Torch")

        # In same position: duplicate symbol rejected
        with self.assertRaises(ValidationError):
            create_candidate(position_id=pos1.id, name="Bob", symbol="Torch")

        # In different position within same election: duplicate symbol rejected
        with self.assertRaises(ValidationError):
            create_candidate(position_id=pos2.id, name="Charlie", symbol="Torch")

        # Multiple candidates with blank symbol are permitted
        cand1 = create_candidate(position_id=pos1.id, name="Independent 1", symbol="")
        cand2 = create_candidate(position_id=pos2.id, name="Independent 2", symbol="")
        self.assertEqual(cand1.symbol, "")
        self.assertEqual(cand2.symbol, "")


class ElectionLifecycleServiceTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.starts_at = self.now + timedelta(hours=1)
        self.ends_at = self.now + timedelta(hours=8)
        self.election = create_election(
            name="Student Body 2026",
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            description="Annual executive elections.",
        )

    def _setup_valid_election_prerequisites(self, election):
        from accounts.services import create_device_session
        from voters.models import ElectionVoter
        from voters.services import create_booth, create_voter, enroll_voters_in_election

        voter = create_voter(primary_registry_value=f"VOT-{election.id}-{timezone.now().timestamp()}", name="Test Voter")
        enroll_voters_in_election(election_id=election.id, voter_ids=[voter.id])
        booth_res = create_booth(election_id=election.id)
        ElectionVoter.objects.filter(election=election).update(booth=booth_res['booth'])
        create_device_session(booth_res['officer_device'], f"sess-off-{election.id}")
        create_device_session(booth_res['kiosk_device'], f"sess-kio-{election.id}")

    def test_initial_state_is_draft(self):
        """Created election is in DRAFT status."""
        self.assertEqual(self.election.status, ElectionStatus.DRAFT)
        self.assertTrue(self.election.is_draft)
        self.assertFalse(self.election.is_active)

    def test_cannot_start_without_positions(self):
        """Configuration validation prevents starting an election with 0 positions."""
        self._setup_valid_election_prerequisites(self.election)
        errors = validate_election_configuration(self.election)
        self.assertTrue(any("no ballot positions" in err.lower() for err in errors))
        with self.assertRaises(ValidationError):
            start_election(election_id=self.election.id)

    def test_cannot_start_if_position_has_no_candidates(self):
        """Configuration validation prevents starting if any position has 0 candidates."""
        self._setup_valid_election_prerequisites(self.election)
        pos = create_position(election_id=self.election.id, name="President", display_order=1)
        errors = validate_election_configuration(self.election)
        self.assertTrue(any("no registered candidates" in err.lower() for err in errors))
        with self.assertRaises(ValidationError):
            start_election(election_id=self.election.id)

    def test_successful_activation_when_valid(self):
        """Valid configuration allows transition DRAFT -> ACTIVE."""
        self._setup_valid_election_prerequisites(self.election)
        pos1 = create_position(election_id=self.election.id, name="President", display_order=1)
        pos2 = create_position(election_id=self.election.id, name="Vice President", display_order=2)
        create_candidate(position_id=pos1.id, name="Alice", symbol="Star")
        create_candidate(position_id=pos1.id, name="Bob", symbol="Moon")
        create_candidate(position_id=pos2.id, name="Charlie", symbol="Sun")

        errors = validate_election_configuration(self.election)
        self.assertEqual(len(errors), 0)

        active_election = start_election(election_id=self.election.id)
        self.assertEqual(active_election.status, ElectionStatus.ACTIVE)
        self.assertTrue(active_election.is_active)

    def test_cannot_activate_two_elections_concurrently(self):
        """Only one election can be ACTIVE at any time in the installation."""
        self._setup_valid_election_prerequisites(self.election)
        pos1 = create_position(election_id=self.election.id, name="President", display_order=1)
        create_candidate(position_id=pos1.id, name="Alice")
        start_election(election_id=self.election.id)

        # Create second election
        election2 = create_election(
            name="Second Election",
            starts_at=self.starts_at,
            ends_at=self.ends_at,
        )
        self._setup_valid_election_prerequisites(election2)
        pos2 = create_position(election_id=election2.id, name="Secretary", display_order=1)
        create_candidate(position_id=pos2.id, name="Dave")

        with self.assertRaises(ValidationError):
            start_election(election_id=election2.id)

    def test_configuration_freeze_after_activation(self):
        """Once ACTIVE, configuration (positions, candidates, election parameters) cannot be modified."""
        self._setup_valid_election_prerequisites(self.election)
        pos = create_position(election_id=self.election.id, name="President", display_order=1)
        cand = create_candidate(position_id=pos.id, name="Alice")
        start_election(election_id=self.election.id)

        # Attempt to add position
        with self.assertRaises(ValidationError):
            create_position(election_id=self.election.id, name="Treasurer")

        # Attempt to modify position
        with self.assertRaises(ValidationError):
            update_position(position_id=pos.id, name="Updated", display_order=2)

        # Attempt to delete position
        with self.assertRaises(ValidationError):
            delete_position(position_id=pos.id)

        # Attempt to add candidate
        with self.assertRaises(ValidationError):
            create_candidate(position_id=pos.id, name="Eve")

        # Attempt to modify candidate
        with self.assertRaises(ValidationError):
            update_candidate(candidate_id=cand.id, name="Alice Modified")

        # Attempt to delete candidate
        with self.assertRaises(ValidationError):
            delete_candidate(candidate_id=cand.id)

        # Attempt to update election metadata
        with self.assertRaises(ValidationError):
            update_election(
                election_id=self.election.id,
                name="Renamed",
                starts_at=self.starts_at,
                ends_at=self.ends_at,
            )

        # Attempt to delete election
        with self.assertRaises(ValidationError):
            delete_election(election_id=self.election.id)

    def test_lifecycle_closure_and_results_publication(self):
        """Covers ACTIVE -> CLOSED and CLOSED -> RESULTS_PUBLISHED."""
        self._setup_valid_election_prerequisites(self.election)
        pos = create_position(election_id=self.election.id, name="President")
        create_candidate(position_id=pos.id, name="Alice")
        start_election(election_id=self.election.id)

        # Results cannot be published while ACTIVE
        with self.assertRaises(ValidationError):
            publish_results(election_id=self.election.id)

        # Close election
        closed_election = close_election(election_id=self.election.id)
        self.assertEqual(closed_election.status, ElectionStatus.CLOSED)
        self.assertIsNotNone(closed_election.closed_at)

        # Publish results
        published_election = publish_results(election_id=self.election.id)
        self.assertEqual(published_election.status, ElectionStatus.RESULTS_PUBLISHED)
        self.assertIsNotNone(published_election.results_published_at)

    def test_close_if_expired(self):
        """Active election automatically closes if server time >= ends_at."""
        past_ends_at = self.now - timedelta(minutes=5)
        expired_election = Election.objects.create(
            name="Expired Election",
            starts_at=self.now - timedelta(hours=2),
            ends_at=past_ends_at,
            status=ElectionStatus.ACTIVE,
        )

        did_close = close_if_expired(election_id=expired_election.id)
        self.assertTrue(did_close)

        expired_election.refresh_from_db()
        self.assertEqual(expired_election.status, ElectionStatus.CLOSED)
        self.assertIsNotNone(expired_election.closed_at)


class ElectionViewsAndRBACTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_user(
            username="adminuser",
            password="adminpassword123",
            role=Role.ADMIN,
        )
        self.officer_user = User.objects.create_user(
            username="officeruser",
            password="officerpassword123",
            role=Role.OFFICER,
        )

    def test_unauthenticated_redirected_to_login(self):
        """Unauthenticated user cannot view elections dashboard."""
        response = self.client.get(reverse("elections:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_non_admin_forbidden_from_elections_dashboard(self):
        """Officer user is rejected with PermissionDenied when accessing admin dashboard."""
        self.client.login(username="officeruser", password="officerpassword123")
        response = self.client.get(reverse("elections:dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_dashboard_and_create_election(self):
        """Administrator can access dashboard and submit election creation form."""
        self.client.login(username="adminuser", password="adminpassword123")
        response = self.client.get(reverse("elections:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "electra")
        self.assertContains(response, "Election Workspace")

        # Create election via POST
        starts = (timezone.now() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        ends = (timezone.now() + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M")
        post_response = self.client.post(reverse("elections:create"), {
            "name": "General Election 2026",
            "description": "Annual elections.",
            "starts_at": starts,
            "ends_at": ends,
        })
        self.assertEqual(post_response.status_code, 302)

        created = Election.objects.filter(name="General Election 2026").first()
        self.assertIsNotNone(created)
        self.assertEqual(created.status, ElectionStatus.DRAFT)

    def test_authenticated_admin_redirected_away_from_landing_and_login(self):
        """Authenticated users cannot access landing page or login page."""
        self.client.login(username="adminuser", password="adminpassword123")

        # Landing page redirects to dashboard
        landing_response = self.client.get(reverse("index"))
        self.assertEqual(landing_response.status_code, 302)
        self.assertEqual(landing_response.url, reverse("elections:dashboard"))

        # Login page redirects to dashboard
        login_response = self.client.get(reverse("accounts:login"))
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response.url, reverse("elections:dashboard"))


class ElectionVotersWorkflowTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="adminworkflow",
            password="adminpassword123",
            role=Role.ADMIN,
        )
        self.client.login(username="adminworkflow", password="adminpassword123")

    def test_create_election_with_only_title_in_draft(self):
        """Admin can create a draft election with only a title and description, leaving times for launch."""
        post_response = self.client.post(reverse("elections:create"), {
            "name": "Spring Senate Election 2026",
            "description": "Annual elections.",
        })
        self.assertEqual(post_response.status_code, 302)

        election = Election.objects.get(name="Spring Senate Election 2026")
        self.assertEqual(election.status, ElectionStatus.DRAFT)
        self.assertIsNone(election.starts_at)
        self.assertIsNone(election.ends_at)
        self.assertEqual(post_response.url, reverse("elections:voters", kwargs={"election_id": election.id}))

    def test_start_election_auto_stamps_start_time_and_requires_cutoff(self):
        """When starting an election, starts_at is automatically stamped to now, and ends_at is validated."""
        from elections.services import create_candidate, create_election, create_position, start_election
        from voters.models import ElectionVoter
        from voters.services import create_booth, create_voter, enroll_voters_in_election
        from accounts.services import create_device_session

        election = create_election(name="Auto Timestamp Election")
        pos = create_position(election_id=election.id, name="President")
        create_candidate(position_id=pos.id, name="Alice")
        voter = create_voter(primary_registry_value="AUTO01", name="Auto Voter")
        enroll_voters_in_election(election_id=election.id, voter_ids=[voter.id])
        booth_res = create_booth(election_id=election.id)
        ElectionVoter.objects.filter(election=election).update(booth=booth_res['booth'])
        create_device_session(booth_res['officer_device'], "sess-off-auto")
        create_device_session(booth_res['kiosk_device'], "sess-kio-auto")

        # Starting without cutoff time must fail
        with self.assertRaises(ValidationError):
            start_election(election_id=election.id)

        # Starting with future cutoff time succeeds
        cutoff = timezone.now() + timedelta(hours=6)
        active_election = start_election(election_id=election.id, ends_at=cutoff)
        self.assertEqual(active_election.status, ElectionStatus.ACTIVE)
        self.assertIsNotNone(active_election.starts_at)
        self.assertAlmostEqual(active_election.starts_at.timestamp(), timezone.now().timestamp(), delta=5)
        self.assertEqual(active_election.ends_at, cutoff)

    def test_candidate_creation_with_voter_prefill(self):
        """Creating a candidate can reference a voter and prefill name and academic group."""
        from elections.services import create_candidate, create_election, create_position
        from voters.models import AcademicGroup
        from voters.services import create_voter

        election = create_election(name="Candidate Prefill Test")
        pos = create_position(election_id=election.id, name="President")
        grp = AcademicGroup.objects.create(name="Computer Science")
        voter = create_voter(primary_registry_value="PRE001", name="Samantha Carter", academic_group_id=grp.id)

        # Create candidate with voter_id without manually providing name
        cand = create_candidate(
            position_id=pos.id,
            voter_id=voter.id,
            symbol="Compass",
        )
        self.assertEqual(cand.name, "Samantha Carter")
        self.assertEqual(cand.academic_group, "Computer Science")
        self.assertEqual(cand.voter, voter)
        self.assertEqual(cand.symbol, "Compass")

    def test_election_voters_view_and_enrollment(self):
        """Admin can access an election's voters tab and enroll voters from master registry."""
        from elections.services import create_election
        from voters.models import ElectionVoter
        from voters.services import create_voter

        election = create_election(name="Enrollment Tab Test")
        voter1 = create_voter(primary_registry_value="ENR101", name="David Bowman")
        voter2 = create_voter(primary_registry_value="ENR102", name="Frank Poole")

        # View voters page for election (redirects to authoritative setup_voters)
        response = self.client.get(reverse("elections:voters", kwargs={"election_id": election.id}), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Election voters")

        # Enroll voter1 via POST
        post_response = self.client.post(reverse("elections:enroll_voters", kwargs={"election_id": election.id}), {
            "voter_ids": [voter1.id],
        })
        self.assertEqual(post_response.status_code, 302)
        self.assertTrue(ElectionVoter.objects.filter(election=election, voter=voter1).exists())
        self.assertFalse(ElectionVoter.objects.filter(election=election, voter=voter2).exists())

    def test_direct_spreadsheet_import_into_election(self):
        """Uploading CSV in election context saves to master registry and enrolls in election."""
        import io
        from django.core.files.uploadedfile import SimpleUploadedFile
        from elections.services import create_election
        from voters.models import ElectionVoter, Voter

        election = create_election(name="Direct Import Test")

        csv_content = (
            "Student ID,Name,Department,Gender\n"
            "DIR001,John Matrix,Defense,Male\n"
            "DIR002,Sarah Connor,Operations,Female\n"
        ).encode("utf-8")
        uploaded = SimpleUploadedFile("roster.csv", csv_content, content_type="text/csv")

        response = self.client.post(reverse("elections:import_voters", kwargs={"election_id": election.id}), {
            "file": uploaded,
        })
        self.assertEqual(response.status_code, 302)

        # Both voters exist in master registry
        v1 = Voter.objects.filter(primary_registry_value="DIR001").first()
        v2 = Voter.objects.filter(primary_registry_value="DIR002").first()
        self.assertIsNotNone(v1)
        self.assertIsNotNone(v2)

        # Both voters are enrolled in this election
        self.assertTrue(ElectionVoter.objects.filter(election=election, voter=v1).exists())
        self.assertTrue(ElectionVoter.objects.filter(election=election, voter=v2).exists())

    def test_structural_readiness_and_cutoff_validation(self):
        """Structural configuration readiness passes without cutoff time; cutoff is verified upon launch."""
        from elections.selectors import get_election_details
        from elections.services import (
            create_candidate,
            create_election,
            create_position,
            start_election,
            validate_election_configuration,
        )
        from voters.models import ElectionVoter
        from voters.services import create_booth, create_voter, enroll_voters_in_election
        from accounts.services import create_device_session

        election = create_election(name="Readiness Gate Test")
        pos = create_position(election_id=election.id, name="President")
        create_candidate(position_id=pos.id, name="Alice Walker")
        voter = create_voter(primary_registry_value="RD001", name="Ready Voter")
        enroll_voters_in_election(election_id=election.id, voter_ids=[voter.id])
        booth_res = create_booth(election_id=election.id)
        ElectionVoter.objects.filter(election=election).update(booth=booth_res["booth"])
        create_device_session(booth_res["officer_device"], "sess-off-rd")
        create_device_session(booth_res["kiosk_device"], "sess-kio-rd")

        # Structural validation (check_cutoff=False) has 0 errors
        structural_errors = validate_election_configuration(election, check_cutoff=False)
        self.assertEqual(structural_errors, [])

        # Details indicates is_ready_to_start is True even before cutoff is entered
        details = get_election_details(election.id)
        self.assertTrue(details["is_ready_to_start"])
        self.assertEqual(len(details["structural_errors"]), 0)

        # Full validation (check_cutoff=True) flags missing cutoff
        full_errors = validate_election_configuration(election, check_cutoff=True)
        self.assertIn("Election cutoff time must be specified before launching live polling.", full_errors)

        # Now launch with cutoff: start time is automatically stamped to now
        cutoff = timezone.now() + timedelta(hours=3)
        active = start_election(election_id=election.id, ends_at=cutoff)
        self.assertEqual(active.status, ElectionStatus.ACTIVE)
        self.assertIsNotNone(active.starts_at)
        self.assertEqual(active.ends_at, cutoff)

    def test_booth_management_views_and_roster_export(self):
        """Admin can access booth management, create booths, allocate voters, and print sign-in roster."""
        from elections.services import create_election
        from voters.models import AcademicGroup, ElectionVoter
        from voters.services import create_booth, create_voter, enroll_voters_in_election

        election = create_election(name="Booth View Suite Election")
        voter1 = create_voter(primary_registry_value="BV001", name="Booth Voter 1")
        voter2 = create_voter(primary_registry_value="BV002", name="Booth Voter 2")
        enroll_voters_in_election(election_id=election.id, voter_ids=[voter1.id, voter2.id])

        # 1. GET booth management view (redirects to authoritative setup_booths)
        url = reverse("elections:booths", kwargs={"election_id": election.id})
        res = self.client.get(url, follow=True)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Booths &amp; allocation")

        # 2. POST create booth
        create_res = self.client.post(reverse("elections:booth_create", kwargs={"election_id": election.id}), {
            "name": "Gymnasium East",
        })
        self.assertEqual(create_res.status_code, 302)
        booth = election.booths.first()
        self.assertIsNotNone(booth)
        self.assertEqual(booth.booth_number, 1)
        self.assertEqual(booth.name, "Gymnasium East")

        # Session should contain newly provisioned station identifiers
        session_res = self.client.get(url, follow=True)
        officer_dev = booth.devices.filter(device_type="OFFICER").first()
        kiosk_dev = booth.devices.filter(device_type="KIOSK").first()
        self.assertContains(session_res, officer_dev.identifier)
        self.assertContains(session_res, kiosk_dev.identifier)

        # 3. POST allocate all unallocated voters to booth
        alloc_res = self.client.post(reverse("elections:booth_allocate", kwargs={"election_id": election.id, "booth_id": booth.id}), {
            "allocate_all_unallocated": "1",
        })
        self.assertEqual(alloc_res.status_code, 302)
        self.assertEqual(ElectionVoter.objects.filter(election=election, booth=booth).count(), 2)

        # 4. GET printable roster sheet
        roster_res = self.client.get(reverse("elections:booth_roster", kwargs={"election_id": election.id, "booth_id": booth.id}))
        self.assertEqual(roster_res.status_code, 200)
        self.assertContains(roster_res, "Official Voter Slips")
        self.assertContains(roster_res, "BV001")
        self.assertContains(roster_res, "Booth Voter 1")

        # 5. POST rotate device credentials
        officer_dev = booth.devices.filter(device_type="OFFICER").first()
        rot_res = self.client.post(reverse("elections:device_rotate_credentials", kwargs={"device_id": officer_dev.id}), {
            "next": url,
        })
        self.assertEqual(rot_res.status_code, 302)
        officer_dev.refresh_from_db()
        self.assertEqual(officer_dev.credential_version, 2)

    def test_election_results_view_across_lifecycle(self):
        """Dedicated results view reflects appropriate state across DRAFT, ACTIVE, CLOSED, and PUBLISHED."""
        from elections.services import close_election, create_candidate, create_election, create_position, start_election
        from voters.models import ElectionVoter
        from voters.services import create_booth, create_voter, enroll_voters_in_election
        from accounts.services import create_device_session

        election = create_election(name="Results Lifecycle Election")
        pos = create_position(election_id=election.id, name="President")
        cand = create_candidate(position_id=pos.id, name="Candidate One")
        voter = create_voter(primary_registry_value="RES001", name="Results Voter")
        enroll_voters_in_election(election_id=election.id, voter_ids=[voter.id])
        booth_res = create_booth(election_id=election.id)
        ElectionVoter.objects.filter(election=election).update(booth=booth_res["booth"])
        create_device_session(booth_res["officer_device"], "s-off-res")
        create_device_session(booth_res["kiosk_device"], "s-kio-res")

        results_url = reverse("elections:results", kwargs={"election_id": election.id})

        # 1. DRAFT state
        draft_res = self.client.get(results_url)
        self.assertEqual(draft_res.status_code, 200)
        self.assertContains(draft_res, "Election in Draft Setup")

        # 2. ACTIVE state
        start_election(election_id=election.id, ends_at=timezone.now() + timedelta(hours=4))
        active_res = self.client.get(results_url)
        self.assertEqual(active_res.status_code, 200)
        self.assertContains(active_res, "Live Polling in Progress")
        self.assertContains(active_res, "Enrolled Voters")

        # 3. CLOSED state
        close_election(election_id=election.id)
        closed_res = self.client.get(results_url)
        self.assertEqual(closed_res.status_code, 200)
        self.assertContains(closed_res, "Polling Closed &bull; Awaiting Publication")

        # 4. PUBLISHED state via POST publish-results
        pub_post = self.client.post(reverse("elections:publish_results", kwargs={"election_id": election.id}))
        self.assertEqual(pub_post.status_code, 302)
        self.assertEqual(pub_post.url, results_url)

        pub_res = self.client.get(results_url)
        self.assertEqual(pub_res.status_code, 200)
        self.assertContains(pub_res, "Official Certified Results")


class ElectionHistoryTests(TestCase):
    """Test suite for Election History vertical slice (references/05_election_history.png)."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="adminhistory",
            password="adminpassword123",
            role=Role.ADMIN,
        )
        self.officer_user = User.objects.create_user(
            username="officeruser",
            password="officerpass123",
            role=Role.OFFICER,
        )

    def test_history_requires_admin_authentication(self):
        """Anonymous and non-admin users cannot access the history view."""
        url = reverse("elections:history")

        # Anonymous redirected to login
        res = self.client.get(url)
        self.assertEqual(res.status_code, 302)

        # Officer denied with 403 Forbidden
        self.client.login(username="officeruser", password="officerpass123")
        res_officer = self.client.get(url)
        self.assertEqual(res_officer.status_code, 403)

        # Admin gets 200 OK
        self.client.login(username="adminhistory", password="adminpassword123")
        res_admin = self.client.get(url)
        self.assertEqual(res_admin.status_code, 200)

    def test_empty_history_renders_electra_placeholder(self):
        """When no elections exist, the view displays the Electra minimal empty placeholder."""
        self.client.login(username="adminhistory", password="adminpassword123")
        res = self.client.get(reverse("elections:history"))

        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Election history")
        self.assertContains(res, "View and manage all past elections.")
        self.assertContains(res, "No election history yet")
        self.assertContains(res, "Return to home")
        self.assertContains(res, "Search by election name or year...")
        self.assertContains(res, "All years")
        # Ensure status filter and tags are removed as specified
        self.assertNotContains(res, "All statuses")
        self.assertNotContains(res, "Completed")
        self.assertNotContains(res, "Archived")

    def test_draft_and_active_elections_excluded_from_history(self):
        """DRAFT and ACTIVE elections must NOT appear in the election history."""
        from elections.services import create_candidate, create_election, create_position, start_election
        from voters.models import ElectionVoter
        from voters.services import create_booth, create_voter, enroll_voters_in_election
        from accounts.services import create_device_session

        # 1. Create a DRAFT election
        create_election(name="Draft Council 2026")

        # 2. Create an ACTIVE election
        active_el = create_election(name="Active Union 2026")
        pos = create_position(election_id=active_el.id, name="President")
        create_candidate(position_id=pos.id, name="Active Candidate")
        voter = create_voter(primary_registry_value="HIST01", name="History Voter")
        enroll_voters_in_election(election_id=active_el.id, voter_ids=[voter.id])
        booth_res = create_booth(election_id=active_el.id)
        ElectionVoter.objects.filter(election=active_el).update(booth=booth_res["booth"])
        create_device_session(booth_res["officer_device"], "s-off-hist")
        create_device_session(booth_res["kiosk_device"], "s-kio-hist")
        start_election(election_id=active_el.id, ends_at=timezone.now() + timedelta(hours=2))

        # Check history view
        self.client.login(username="adminhistory", password="adminpassword123")
        res = self.client.get(reverse("elections:history"))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, "Draft Council 2026")
        self.assertNotContains(res, "Active Union 2026")
        # Since no past elections exist, the empty state is displayed
        self.assertContains(res, "No election history yet")

    def test_closed_and_published_elections_displayed_with_stats(self):
        """Past elections (CLOSED and RESULTS_PUBLISHED) are listed with correct turnout and position stats."""
        from elections.services import close_election, create_candidate, create_election, create_position, start_election
        from voters.models import ElectionVoter
        from voters.services import create_booth, create_voter, enroll_voters_in_election
        from accounts.services import create_device_session

        # Create past election 1 (CLOSED)
        e1 = create_election(name="Student Union Election 2025", description="Annual student union election.")
        pos1 = create_position(election_id=e1.id, name="Chairperson")
        pos2 = create_position(election_id=e1.id, name="Vice Chair", display_order=2)
        create_candidate(position_id=pos1.id, name="Cand A")
        create_candidate(position_id=pos2.id, name="Cand B")
        v1 = create_voter(primary_registry_value="V101", name="Voter One")
        v2 = create_voter(primary_registry_value="V102", name="Voter Two")
        enroll_voters_in_election(election_id=e1.id, voter_ids=[v1.id, v2.id])
        booth_res = create_booth(election_id=e1.id)
        ElectionVoter.objects.filter(election=e1).update(booth=booth_res["booth"])
        # Mark one voter as voted
        ev1 = ElectionVoter.objects.get(election=e1, voter=v1)
        ev1.has_voted = True
        ev1.save()
        create_device_session(booth_res["officer_device"], "s-off-e1")
        create_device_session(booth_res["kiosk_device"], "s-kio-e1")
        start_election(election_id=e1.id, ends_at=timezone.now() + timedelta(hours=1))
        close_election(election_id=e1.id)

        # Create past election 2 (RESULTS_PUBLISHED)
        e2 = create_election(name="Department Representative Election 2024", description="CS department rep.")
        pos3 = create_position(election_id=e2.id, name="Representative")
        create_candidate(position_id=pos3.id, name="Cand C")
        v3 = create_voter(primary_registry_value="V103", name="Voter Three")
        enroll_voters_in_election(election_id=e2.id, voter_ids=[v3.id])
        booth_res2 = create_booth(election_id=e2.id)
        ElectionVoter.objects.filter(election=e2).update(booth=booth_res2["booth"])
        ev3 = ElectionVoter.objects.get(election=e2, voter=v3)
        ev3.has_voted = True
        ev3.save()
        create_device_session(booth_res2["officer_device"], "s-off-e2")
        create_device_session(booth_res2["kiosk_device"], "s-kio-e2")
        start_election(election_id=e2.id, ends_at=timezone.now() + timedelta(hours=1))
        close_election(election_id=e2.id)
        e2.status = ElectionStatus.RESULTS_PUBLISHED
        e2.save()

        self.client.login(username="adminhistory", password="adminpassword123")
        res = self.client.get(reverse("elections:history"))
        self.assertEqual(res.status_code, 200)

        # Both elections appear
        self.assertContains(res, "Student Union Election 2025")
        self.assertContains(res, "Annual student union election.")
        self.assertContains(res, "Department Representative Election 2024")
        self.assertContains(res, "CS department rep.")

        # Total voters and turnout
        self.assertContains(res, "Total voters")
        self.assertContains(res, "Voted (50.0%)")  # 1 out of 2 for e1
        self.assertContains(res, "Voted (100.0%)") # 1 out of 1 for e2
        self.assertContains(res, "Positions")

        # Action buttons
        self.assertContains(res, "View results")
        self.assertContains(res, "View details")

        # Status badge tags removed as instructed
        self.assertNotContains(res, "All statuses")

    def test_search_and_filter_functionality(self):
        """Search query and year filter correctly narrow down the election history."""
        from elections.services import close_election, create_candidate, create_election, create_position, start_election
        from voters.models import ElectionVoter
        from voters.services import create_booth, create_voter, enroll_voters_in_election
        from accounts.services import create_device_session

        # Setup an election
        el = create_election(name="Faculty Board Election 2024")
        pos = create_position(election_id=el.id, name="Dean")
        create_candidate(position_id=pos.id, name="Prof Adams")
        v = create_voter(primary_registry_value="F001", name="Faculty Voter")
        enroll_voters_in_election(election_id=el.id, voter_ids=[v.id])
        booth_res = create_booth(election_id=el.id)
        ElectionVoter.objects.filter(election=el).update(booth=booth_res["booth"])
        create_device_session(booth_res["officer_device"], "s-off-f")
        create_device_session(booth_res["kiosk_device"], "s-kio-f")
        start_election(election_id=el.id, ends_at=timezone.now() + timedelta(hours=1))
        close_election(election_id=el.id)

        self.client.login(username="adminhistory", password="adminpassword123")

        # 1. Search matching
        res_match = self.client.get(reverse("elections:history") + "?q=Faculty")
        self.assertEqual(res_match.status_code, 200)
        self.assertContains(res_match, "Faculty Board Election 2024")

        # 2. Search not matching shows filter empty state
        res_nomatch = self.client.get(reverse("elections:history") + "?q=NonExistent")
        self.assertEqual(res_nomatch.status_code, 200)
        self.assertNotContains(res_nomatch, "Faculty Board Election 2024")
        self.assertContains(res_nomatch, "No matching elections found")
        self.assertContains(res_nomatch, "Clear filters")

    def test_home_hub_card_links_to_history(self):
        """Home hub contains the link to elections:history."""
        self.client.login(username="adminhistory", password="adminpassword123")
        res = self.client.get(reverse("elections:dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, reverse("elections:history"))


class ElectionSetupWorkflowTests(TestCase):
    """Test suite for the 4-stage election setup flow (Stage 1: Election Voters)."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="setupadmin",
            password="adminpassword123",
            email="setup@test.com",
            role=Role.ADMIN,
        )

    def test_setup_start_view_creates_new_draft_when_no_draft_exists(self):
        """When no drafts exist, setup_start creates a new draft at Stage 1 and redirects."""
        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.get(reverse("elections:setup_start"))
        draft = Election.objects.filter(status=ElectionStatus.DRAFT).first()
        self.assertIsNotNone(draft)
        self.assertEqual(draft.setup_stage, 1)
        self.assertRedirects(res, reverse("elections:setup_voters", kwargs={"election_id": draft.id}))

    def test_setup_start_view_resumes_existing_draft(self):
        """Setup start view resumes an explicitly saved draft without duplicating."""
        draft = create_election(name="In Progress Saved Draft")
        draft.setup_stage = 1
        draft.is_saved_draft = True
        draft.save()

        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.get(reverse("elections:setup_start"))
        self.assertRedirects(res, reverse("elections:setup_voters", kwargs={"election_id": draft.id}))

    def test_setup_start_view_cleans_up_unsaved_draft_to_start_fresh(self):
        """When an unsaved draft exists (is_saved_draft=False), setup_start cleans it up and creates a new one."""
        old_draft = create_election(name="Unsaved Draft")
        old_draft.is_saved_draft = False
        old_draft.save()

        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.get(reverse("elections:setup_start"))
        self.assertFalse(Election.objects.filter(id=old_draft.id).exists())
        new_draft = Election.objects.filter(status=ElectionStatus.DRAFT).first()
        self.assertIsNotNone(new_draft)
        self.assertFalse(new_draft.is_saved_draft)
        self.assertRedirects(res, reverse("elections:setup_voters", kwargs={"election_id": new_draft.id}))

    def test_home_dashboard_prompts_only_for_explicitly_saved_draft(self):
        """Home page only displays the resume draft prompt if the draft was explicitly saved."""
        draft = create_election(name="Unsaved Setup Draft")
        draft.is_saved_draft = False
        draft.save()

        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.get(reverse("elections:dashboard"))
        # Unsaved draft must not trigger the resume prompt on Home
        self.assertIsNone(res.context["active_draft_election"])
        self.assertNotContains(res, "Resume Election Setup?")

        # When explicitly saved as draft, it appears on Home
        draft.is_saved_draft = True
        draft.save()
        res = self.client.get(reverse("elections:dashboard"))
        self.assertEqual(res.context["active_draft_election"].id, draft.id)
        self.assertContains(res, "Resume Election Setup?")

    def test_setup_discard_endpoint_deletes_draft(self):
        """Setup discard endpoint deletes the draft and returns to Home."""
        draft = create_election(name="Draft To Discard")
        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.post(reverse("elections:setup_discard", kwargs={"election_id": draft.id}))
        self.assertRedirects(res, reverse("elections:dashboard"))
        self.assertFalse(Election.objects.filter(id=draft.id).exists())

    def test_setup_resume_view_routes_to_saved_stage(self):
        """Setup resume endpoint routes to the saved setup_stage."""
        draft = create_election(name="Stage 1 Resume Draft")
        draft.setup_stage = 1
        draft.save()

        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.get(reverse("elections:setup_resume", kwargs={"election_id": draft.id}))
        self.assertRedirects(res, reverse("elections:setup_voters", kwargs={"election_id": draft.id}))

    def test_setup_start_view_discard_flag_creates_fresh_draft(self):
        """With discard=1, existing draft is removed and fresh draft is created."""
        draft = create_election(name="Old Draft To Discard")
        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.get(reverse("elections:setup_start") + "?discard=1")
        self.assertFalse(Election.objects.filter(id=draft.id).exists())
        new_draft = Election.objects.filter(status=ElectionStatus.DRAFT).first()
        self.assertIsNotNone(new_draft)
        self.assertRedirects(res, reverse("elections:setup_voters", kwargs={"election_id": new_draft.id}))

    def test_setup_start_view_rejects_if_active_election_exists(self):
        """Cannot initiate a new election setup if an election is currently ACTIVE."""
        from django.utils import timezone
        active_el = create_election(name="Live Election")
        active_el.status = ElectionStatus.ACTIVE
        active_el.starts_at = timezone.now()
        active_el.ends_at = timezone.now() + timezone.timedelta(hours=2)
        active_el.save()

        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.get(reverse("elections:setup_start"))
        self.assertRedirects(res, reverse("elections:dashboard"))

    def test_setup_voters_view_renders_empty_placeholder_when_no_registries(self):
        """When no registries exist, Stage 1 renders the empty state placeholder."""
        from voters.models import AcademicGroup, Voter, VoterRegistry
        Election.objects.all().delete()
        Voter.objects.all().delete()
        AcademicGroup.objects.all().delete()
        VoterRegistry.objects.all().delete()

        draft = create_election(name="Empty Test Draft")
        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.get(reverse("elections:setup_voters", kwargs={"election_id": draft.id}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Election voters")
        self.assertContains(res, "No voter registries found")
        self.assertContains(res, "+ Create voter registry")
        self.assertContains(res, "From master registry")
        self.assertContains(res, "Import directly")

    def test_setup_voters_view_renders_only_registry_names_in_list_mode(self):
        """In list mode, each registry card displays ONLY the registry name per instructions."""
        from voters.services import create_voter_registry
        reg1 = create_voter_registry(name="Science Faculty Registry")
        reg2 = create_voter_registry(name="Engineering Faculty Registry")

        draft = create_election(name="Registry List Draft")
        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.get(reverse("elections:setup_voters", kwargs={"election_id": draft.id}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Science Faculty Registry")
        self.assertContains(res, "Engineering Faculty Registry")
        self.assertNotContains(res, "No voter registries found")

    def test_setup_voters_view_preview_registry_shows_only_groups_and_count(self):
        """Clicking a registry displays preview with ONLY its groups and count (no tables)."""
        from voters.services import create_academic_group, create_voter, create_voter_registry
        reg = create_voter_registry(name="Preview Department Registry")
        g1 = create_academic_group(name="Computer Science", registry=reg)
        g2 = create_academic_group(name="Mechanical Eng", registry=reg)
        create_voter(primary_registry_value="CS101", name="Alice CS", registry=reg, academic_group_id=g1.id)
        create_voter(primary_registry_value="ME101", name="Bob ME", registry=reg, academic_group_id=g2.id)

        draft = create_election(name="Preview Draft")
        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.get(reverse("elections:setup_voters", kwargs={"election_id": draft.id}) + f"?preview={reg.id}")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Preview Department Registry")
        self.assertContains(res, "Computer Science")
        self.assertContains(res, "Mechanical Eng")
        self.assertContains(res, "2")
        self.assertContains(res, "Change registry")
        # Ensure no table is rendered in preview per instructions
        self.assertNotContains(res, "<table")

    def test_setup_voters_shows_next_button_when_returning_from_page_2(self):
        """When user navigates back to Step 1 from Step 2, header shows Next button instead of Back button."""
        from voters.services import create_voter_registry
        reg = create_voter_registry(name="Returning Test Registry")
        
        # 1. Fresh draft: setup_stage=1, shows Back button on preview and NOT Next
        draft_fresh = create_election(name="Fresh Draft Election")
        self.client.login(username="setupadmin", password="adminpassword123")
        res_fresh = self.client.get(reverse("elections:setup_voters", kwargs={"election_id": draft_fresh.id}) + f"?preview={reg.id}")
        self.assertEqual(res_fresh.status_code, 200)
        self.assertFalse(res_fresh.context["came_from_step_2"])
        self.assertContains(res_fresh, "&larr; Back")
        self.assertNotContains(res_fresh, "Next &rarr;")

        # 2. Returning draft from page 2: shows Next button and NOT Back button
        draft_returning = create_election(name="Returning Draft Election", voter_registry=reg)
        draft_returning.setup_stage = 2
        draft_returning.save()
        res_ret = self.client.get(reverse("elections:setup_voters", kwargs={"election_id": draft_returning.id}) + "?from_step=2")
        self.assertEqual(res_ret.status_code, 200)
        self.assertTrue(res_ret.context["came_from_step_2"])
        self.assertContains(res_ret, "Next &rarr;")
        self.assertContains(res_ret, reverse("elections:setup_details", kwargs={"election_id": draft_returning.id}))

    def test_setup_voters_view_save_draft_action(self):
        """Save draft button saves current stage exit point and returns to Home."""
        from voters.services import create_voter_registry
        reg = create_voter_registry(name="Draft Save Registry")
        draft = create_election(name="Save Draft Test")

        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.post(
            reverse("elections:setup_voters", kwargs={"election_id": draft.id}),
            {"action": "save_draft", "registry_id": str(reg.id)},
        )
        self.assertRedirects(res, reverse("elections:dashboard"))
        draft.refresh_from_db()
        self.assertEqual(draft.setup_stage, 1)
        self.assertEqual(draft.voter_registry_id, reg.id)

    def test_setup_voters_view_continue_enrolls_voters_and_advances_to_stage_2(self):
        """Submitting Continue enrolls all voters into ElectionVoter and advances to Stage 2."""
        from voters.models import ElectionVoter
        from voters.services import create_voter, create_voter_registry
        reg = create_voter_registry(name="Enrollment Test Registry")
        v1 = create_voter(primary_registry_value="ENR01", name="Enrolled One", registry=reg)
        v2 = create_voter(primary_registry_value="ENR02", name="Enrolled Two", registry=reg)

        draft = create_election(name="Advancing Draft")
        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.post(
            reverse("elections:setup_voters", kwargs={"election_id": draft.id}),
            {"action": "continue", "registry_id": str(reg.id)},
        )
        draft.refresh_from_db()
        self.assertEqual(draft.setup_stage, 2)
        self.assertEqual(draft.voter_registry_id, reg.id)
        # Check enrolled voters
        enrolled_ids = list(ElectionVoter.objects.filter(election=draft).values_list("voter_id", flat=True))
        self.assertIn(v1.id, enrolled_ids)
        self.assertIn(v2.id, enrolled_ids)
        self.assertEqual(len(enrolled_ids), 2)
        self.assertRedirects(res, reverse("elections:setup_details", kwargs={"election_id": draft.id}))

    def test_draft_election_does_not_lock_registry(self):
        """Only when an election starts should a registry be locked. DRAFT elections do NOT lock the registry."""
        from voters.services import create_academic_group, create_voter, create_voter_registry
        reg = create_voter_registry(name="Draft Unlocked Registry")
        draft = create_election(name="Draft Unlocking Test")
        draft.voter_registry = reg
        draft.save()

        # In DRAFT: registry.is_locked is False
        self.assertFalse(reg.is_locked)

        # In DRAFT: adding voters and groups should still succeed!
        v = create_voter(primary_registry_value="UNL01", name="Unlocked Voter", registry=reg)
        self.assertIsNotNone(v)
        grp = create_academic_group(name="New Group", registry=reg)
        self.assertIsNotNone(grp)

    def test_setup_voters_view_direct_import_creates_registry_and_previews(self):
        """Action direct_import creates VoterRegistry, imports voters, links to election, and redirects to preview."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from voters.models import Voter, VoterRegistry

        csv_content = (
            "Student ID,Full Name,Department,Semester,Gender\n"
            "DIR001,Direct Student 1,Computer Science,S1,Female\n"
            "DIR002,Direct Student 2,Mechanical Eng,S3,Male\n"
        )
        csv_file = SimpleUploadedFile("direct_voters.csv", csv_content.encode("utf-8"), content_type="text/csv")

        draft = create_election(name="Direct Import Draft")
        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.post(
            reverse("elections:setup_voters", kwargs={"election_id": draft.id}),
            {
                "action": "direct_import",
                "name": "Direct Batch Registry",
                "primary_id_source": "Student ID",
                "name_source": "Full Name",
                "group_source": "Department",
                "subgroup_source": "Semester",
                "gender_source": "Gender",
                "has_subgroups_toggle": "yes",
                "file": csv_file,
            },
        )
        reg = VoterRegistry.objects.filter(name="Direct Batch Registry").first()
        self.assertIsNotNone(reg)
        self.assertEqual(Voter.objects.filter(registry=reg).count(), 2)

        draft.refresh_from_db()
        self.assertEqual(draft.voter_registry_id, reg.id)
        self.assertEqual(draft.setup_stage, 1)

        expected_url = reverse("elections:setup_voters", kwargs={"election_id": draft.id}) + f"?preview={reg.id}&mode=direct"
        self.assertRedirects(res, expected_url)

        # Visiting the preview shows the groups and voter counts (no tables)
        preview_res = self.client.get(expected_url)
        self.assertContains(preview_res, "Direct Batch Registry")
        self.assertContains(preview_res, "Computer Science")
        self.assertContains(preview_res, "Mechanical Eng")
        self.assertNotContains(preview_res, "<table")
        self.assertContains(preview_res, "Use this registry &rarr;")

    def test_setup_voters_view_direct_import_error_retains_file_and_last_mappings(self):
        """When an error happens in direct import, pending file and last mappings are retained."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from voters.services import create_voter_registry

        # Create a conflicting registry with name "Duplicate Registry"
        create_voter_registry(name="Duplicate Registry")

        csv_content = (
            "Student ID,Full Name,Department,Semester,Gender\n"
            "DIR001,Direct Student 1,Computer Science,S1,Female\n"
        )
        csv_file = SimpleUploadedFile("direct_voters.csv", csv_content.encode("utf-8"), content_type="text/csv")

        draft = create_election(name="Direct Import Error Draft")
        self.client.login(username="setupadmin", password="adminpassword123")
        res = self.client.post(
            reverse("elections:setup_voters", kwargs={"election_id": draft.id}),
            {
                "action": "direct_import",
                "name": "Duplicate Registry",  # will fail validation because it already exists
                "primary_id_source": "Student ID",
                "name_source": "Full Name",
                "group_source": "Department",
                "subgroup_source": "Semester",
                "gender_source": "Gender",
                "has_subgroups_toggle": "yes",
                "file": csv_file,
            },
            follow=True,
        )
        # Should redirect back to mode=direct with error message
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "already exists")

        # Must retain file and last mappings in context
        self.assertIsNotNone(res.context["pending_file"])
        self.assertEqual(res.context["pending_file"]["filename"], "direct_voters.csv")
        self.assertIsNotNone(res.context["direct_form_data"])
        self.assertEqual(res.context["direct_form_data"]["name"], "Duplicate Registry")
        self.assertEqual(res.context["direct_form_data"]["primary_id_source"], "Student ID")

        # HTML must retain the typed registry name
        self.assertContains(res, 'value="Duplicate Registry"')


class ElectionSetupDetailsWorkflowTests(TestCase):
    """End-to-end tests for Stage 2: Election Details & Candidates (references/06.2)."""

    def setUp(self):
        from accounts.models import User, Role
        from voters.models import VoterRegistry, AcademicGroup, Voter
        from voters.services import enroll_voters_in_election

        self.admin = User.objects.create_user(
            username="setup_details_admin",
            password="adminpassword123",
            role=Role.ADMIN,
        )
        self.client.login(username="setup_details_admin", password="adminpassword123")

        # Setup registry with 2 groups and 3 voters
        self.registry = VoterRegistry.objects.create(name="Engineering Registry 2026")
        self.grp_cs = AcademicGroup.objects.create(registry=self.registry, name="Computer Science")
        self.grp_ds = AcademicGroup.objects.create(registry=self.registry, name="Data Science")

        self.voter_cs1 = Voter.objects.create(registry=self.registry, primary_registry_value="CS001", name="Aarav Nair", academic_group=self.grp_cs)
        self.voter_cs2 = Voter.objects.create(registry=self.registry, primary_registry_value="CS002", name="Priya Sharma", academic_group=self.grp_cs)
        self.voter_ds1 = Voter.objects.create(registry=self.registry, primary_registry_value="DS001", name="Sara Mohammed", academic_group=self.grp_ds)

        # Create draft election enrolled with these voters
        self.election = create_election(name="Draft Details Election", voter_registry=self.registry)
        enroll_voters_in_election(
            election_id=self.election.id,
            voter_ids=[self.voter_cs1.id, self.voter_cs2.id, self.voter_ds1.id],
        )

    def test_setup_details_get_renders_sections_and_stepper(self):
        res = self.client.get(reverse("elections:setup_details", kwargs={"election_id": self.election.id}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Positions and candidates")
        self.assertContains(res, "Election name")
        self.assertContains(res, "Save as draft")
        self.assertContains(res, "Continue &rarr;")
        self.assertEqual(res.context["active_step"], 2)

    def test_save_draft_action_persists_name_and_desc(self):
        res = self.client.post(
            reverse("elections:setup_details", kwargs={"election_id": self.election.id}),
            {
                "action": "save_draft",
                "name": "Updated Election Title 2026",
                "description": "Updated Election Description",
            },
            follow=True,
        )
        self.assertRedirects(res, reverse("elections:dashboard"))
        self.election.refresh_from_db()
        self.assertEqual(self.election.name, "Updated Election Title 2026")
        self.assertEqual(self.election.description, "Updated Election Description")
        self.assertTrue(self.election.is_saved_draft)

    def test_add_position_universal_and_specific_groups(self):
        # Universal position
        res = self.client.post(
            reverse("elections:setup_details", kwargs={"election_id": self.election.id}),
            {
                "action": "add_position",
                "position_name": "President",
                "eligibility_mode": "all",
            },
            follow=True,
        )
        self.assertEqual(res.status_code, 200)
        pos = Position.objects.get(election=self.election, name="President")
        self.assertEqual(pos.eligible_groups.count(), 0)

        # Position for CS only
        res2 = self.client.post(
            reverse("elections:setup_details", kwargs={"election_id": self.election.id}),
            {
                "action": "add_position",
                "position_name": "CS Representative",
                "eligibility_mode": "groups",
                "eligible_group_ids": [self.grp_cs.id],
            },
            follow=True,
        )
        self.assertEqual(res2.status_code, 200)
        pos_cs = Position.objects.get(election=self.election, name="CS Representative")
        self.assertIn(self.grp_cs, pos_cs.eligible_groups.all())
        self.assertNotIn(self.grp_ds, pos_cs.eligible_groups.all())

    def test_add_and_delete_candidate(self):
        pos = create_position(election_id=self.election.id, name="General Secretary")
        # Add candidate
        res = self.client.post(
            reverse("elections:setup_details", kwargs={"election_id": self.election.id}),
            {
                "action": "add_candidate",
                "position_id": pos.id,
                "voter_id": self.voter_cs1.id,
                "candidate_name": self.voter_cs1.name,
                "academic_group": self.voter_cs1.academic_group.name,
                "symbol": "Crown",
            },
            follow=True,
        )
        self.assertEqual(res.status_code, 200)
        cand = Candidate.objects.get(position=pos, name=self.voter_cs1.name)
        self.assertEqual(cand.symbol, "Crown")
        self.assertEqual(cand.voter, self.voter_cs1)

        # Delete candidate
        res_del = self.client.post(
            reverse("elections:setup_details", kwargs={"election_id": self.election.id}),
            {
                "action": "delete_candidate",
                "candidate_id": cand.id,
            },
            follow=True,
        )
        self.assertEqual(res_del.status_code, 200)
        self.assertFalse(Candidate.objects.filter(id=cand.id).exists())

    def test_continue_validation_requires_positions_and_candidates(self):
        # Empty positions
        res = self.client.post(
            reverse("elections:setup_details", kwargs={"election_id": self.election.id}),
            {
                "action": "continue",
                "name": "Final Election",
            },
            follow=True,
        )
        self.assertContains(res, "Please add at least one position to continue")

        # Position exists but has no candidates
        pos = create_position(election_id=self.election.id, name="President")
        res2 = self.client.post(
            reverse("elections:setup_details", kwargs={"election_id": self.election.id}),
            {
                "action": "continue",
                "name": "Final Election",
            },
            follow=True,
        )
        self.assertContains(res2, "Every position must have at least one candidate")

        # Add candidate to position
        create_candidate(position_id=pos.id, voter_id=self.voter_cs1.id, name="Aarav Nair", symbol="Crown")

        # Now continue succeeds and advances to Stage 3 (setup_booths)
        res3 = self.client.post(
            reverse("elections:setup_details", kwargs={"election_id": self.election.id}),
            {
                "action": "continue",
                "name": "Final Election",
            },
            follow=False,
        )
        self.assertEqual(res3.status_code, 302)
        self.assertEqual(res3.url, reverse("elections:setup_booths", kwargs={"election_id": self.election.id}))
        self.election.refresh_from_db()
        self.assertEqual(self.election.setup_stage, 3)

    def test_voter_search_ajax_endpoint_and_eligibility_filter(self):
        pos_cs = create_position(
            election_id=self.election.id,
            name="CS Head",
            eligible_group_ids=[self.grp_cs.id],
        )

        # Search all
        res = self.client.get(
            reverse("elections:setup_voters_search", kwargs={"election_id": self.election.id}),
            {"q": "Aarav"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["identifier"], "CS001")

        # Search restricted to CS group
        res_cs = self.client.get(
            reverse("elections:setup_voters_search", kwargs={"election_id": self.election.id}),
            {"position_id": pos_cs.id},
        )
        self.assertEqual(res_cs.status_code, 200)
        results = res_cs.json()["results"]
        # Only CS001 and CS002 should be returned, DS001 excluded!
        identifiers = [r["identifier"] for r in results]
        self.assertIn("CS001", identifiers)
        self.assertIn("CS002", identifiers)
        self.assertNotIn("DS001", identifiers)


class ElectionSetupBoothsWorkflowTests(TestCase):
    """Test suite for Stage 3 Booths & Paired Device Setup Workflow (references/06.3)."""

    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="admin_booth_tester",
            password="TestPassword@123",
            role=Role.ADMIN,
        )
        self.client = Client()
        self.client.force_login(self.admin_user)

        self.election = create_election(name="Stage 3 Test Election")

    def test_setup_booths_view_get_empty_state(self):
        """When no booths exist, view renders empty state with + Add booth button."""
        res = self.client.get(reverse("elections:setup_booths", kwargs={"election_id": self.election.id}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Booths and devices")
        self.assertContains(res, "No polling booths configured yet")
        self.assertEqual(res.context["next_booth_number"], 1)

    def test_add_booth_creates_paired_devices_and_stores_session_credentials(self):
        """Adding a booth automatically creates Officer Station and Voting Kiosk with random credentials."""
        res = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {
                "action": "add_booth",
                "custom_name": "Main Block - Ground Floor",
            },
            follow=True,
        )
        self.assertEqual(res.status_code, 200)
        booth = Booth.objects.get(election=self.election, booth_number=1)
        self.assertEqual(booth.name, "Main Block - Ground Floor")
        self.assertEqual(booth.display_name, "Booth 1 (Main Block - Ground Floor)")

        # Verify paired devices
        devices = list(booth.devices.all())
        self.assertEqual(len(devices), 2)
        device_types = {d.device_type for d in devices}
        self.assertEqual(device_types, {DeviceType.OFFICER, DeviceType.KIOSK})

        # Verify session flashed credentials
        self.assertIn("new_credentials", res.context)
        new_creds = res.context["new_credentials"]
        self.assertIsNotNone(new_creds)
        self.assertEqual(new_creds["booth_number"], 1)
        self.assertTrue(len(new_creds["officer_password"]) >= 12)
        self.assertTrue(len(new_creds["kiosk_password"]) >= 12)

    def test_edit_booth_name_only(self):
        """Admin can edit only the custom name/location of an existing booth."""
        b_res = create_booth(election_id=self.election.id, name="Old Location")
        booth = b_res["booth"]
        self.assertEqual(booth.name, "Old Location")

        res = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {
                "action": "edit_booth",
                "booth_id": booth.id,
                "custom_name": "Auditorium Hall - First Floor",
            },
            follow=True,
        )
        self.assertEqual(res.status_code, 200)
        booth.refresh_from_db()
        self.assertEqual(booth.name, "Auditorium Hall - First Floor")
        self.assertEqual(booth.booth_number, 1)  # Numbering is invariant

    def test_delete_booth_renumbers_remaining_booths_sequentially(self):
        """Deleting a booth renumbers remaining booths sequentially (1, 2, 3...) to fill the gap."""
        b1 = create_booth(election_id=self.election.id, name="Booth A")["booth"]
        b2 = create_booth(election_id=self.election.id, name="Booth B")["booth"]
        b3 = create_booth(election_id=self.election.id, name="Booth C")["booth"]

        self.assertEqual(b1.booth_number, 1)
        self.assertEqual(b2.booth_number, 2)
        self.assertEqual(b3.booth_number, 3)

        # Delete Booth 2
        res = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {
                "action": "delete_booth",
                "booth_id": b2.id,
            },
            follow=True,
        )
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Booth.objects.filter(id=b2.id).exists())

        remaining = list(Booth.objects.filter(election=self.election).order_by("booth_number"))
        self.assertEqual(len(remaining), 2)
        self.assertEqual(remaining[0].id, b1.id)
        self.assertEqual(remaining[0].booth_number, 1)
        self.assertEqual(remaining[1].id, b3.id)
        self.assertEqual(remaining[1].booth_number, 2)  # Old booth 3 is now booth 2!

    def test_rotate_device_credentials_updates_password(self):
        """Credential rotation increments version and generates a new random password."""
        b_res = create_booth(election_id=self.election.id, name="Rotation Booth")
        officer_dev = b_res["officer_device"]
        old_version = officer_dev.credential_version

        res = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {
                "action": "rotate_credentials",
                "device_id": officer_dev.id,
            },
            follow=True,
        )
        self.assertEqual(res.status_code, 200)
        officer_dev.refresh_from_db()
        self.assertEqual(officer_dev.credential_version, old_version + 1)
        self.assertEqual(officer_dev.credential_status, CredentialStatus.ACTIVE)

        rotated_creds = res.context["rotated_credentials"]
        self.assertIsNotNone(rotated_creds)
        self.assertEqual(rotated_creds["identifier"], officer_dev.identifier)
        self.assertTrue(len(rotated_creds["password"]) >= 12)

    def test_revoke_device_credentials_marks_status_revoked(self):
        """Revoking credentials marks status as REVOKED."""
        b_res = create_booth(election_id=self.election.id, name="Revoke Booth")
        kiosk_dev = b_res["kiosk_device"]
        self.assertEqual(kiosk_dev.credential_status, CredentialStatus.ACTIVE)

        res = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {
                "action": "revoke_credentials",
                "device_id": kiosk_dev.id,
            },
            follow=True,
        )
        self.assertEqual(res.status_code, 200)
        kiosk_dev.refresh_from_db()
        self.assertEqual(kiosk_dev.credential_status, CredentialStatus.REVOKED)

    def test_save_draft_action_marks_draft_saved(self):
        """Save draft action marks is_saved_draft=True and redirects to dashboard."""
        res = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {"action": "save_draft"},
            follow=False,
        )
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, reverse("elections:dashboard"))
        self.election.refresh_from_db()
        self.assertTrue(self.election.is_saved_draft)

    def test_continue_validation_requires_at_least_one_booth_and_slips_download(self):
        """Continue fails if no booth exists or if voter slips PDF has not been downloaded."""
        # 1. No booths
        res = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {"action": "continue"},
            follow=True,
        )
        self.assertContains(res, "Please add at least one polling booth before proceeding.")
        self.election.refresh_from_db()
        self.assertEqual(self.election.setup_stage, 1)

        # 2. Add booth
        create_booth(election_id=self.election.id, name="Booth 1")

        # 3. Continue without downloading slips fails
        res2 = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {"action": "continue"},
            follow=True,
        )
        self.assertContains(res2, "Please download the final voter slips PDF before proceeding to review.")
        self.election.refresh_from_db()
        self.assertEqual(self.election.setup_stage, 1)

        # 4. Download slips PDF
        pdf_res = self.client.get(
            reverse("elections:setup_voter_slips_pdf", kwargs={"election_id": self.election.id})
        )
        self.assertEqual(pdf_res.status_code, 200)
        self.assertEqual(pdf_res["Content-Type"], "application/pdf")

        # 5. Now continue succeeds
        res3 = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {"action": "continue"},
            follow=False,
        )
        self.assertEqual(res3.status_code, 302)
        self.assertEqual(res3.url, reverse("elections:setup_review", kwargs={"election_id": self.election.id}))
        self.election.refresh_from_db()
        self.assertEqual(self.election.setup_stage, 4)

    def test_inplace_booth_edit_ajax(self):
        """In-place booth custom name edit via AJAX returns JSON with updated name without page reload."""
        b = create_booth(election_id=self.election.id, name="Old Room 101")["booth"]
        res = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {
                "action": "edit_booth",
                "booth_id": b.id,
                "custom_name": "New Seminar Hall B",
                "is_ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["name"], "New Seminar Hall B")
        self.assertEqual(data["booth_number"], 1)

        b.refresh_from_db()
        self.assertEqual(b.name, "New Seminar Hall B")

    def test_inplace_rotate_pass_ajax_updates_password_and_cleartext(self):
        """In-place Rotate pass action via AJAX returns new readable password, updates cleartext_password, and sets credential_status to ACTIVE."""
        b_res = create_booth(election_id=self.election.id, name="Security Booth")
        kiosk_dev = b_res["kiosk_device"]
        old_pw = kiosk_dev.cleartext_password
        self.assertTrue(len(old_pw) > 0)

        # Trigger in-place Rotate pass via AJAX
        res = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {
                "action": "rotate_pass",
                "device_id": kiosk_dev.id,
                "is_ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["identifier"], kiosk_dev.identifier)
        self.assertEqual(data["status_display"], "Active")
        self.assertEqual(data["credential_status"], CredentialStatus.ACTIVE)
        self.assertTrue(len(data["new_password"]) >= 10)
        self.assertNotEqual(data["new_password"], old_pw)

        # Verify database was updated and cleartext_password is saved
        kiosk_dev.refresh_from_db()
        self.assertEqual(kiosk_dev.credential_status, CredentialStatus.ACTIVE)
        self.assertEqual(kiosk_dev.cleartext_password, data["new_password"])

    def test_setup_booths_page_renders_rotate_icon_and_cleartext_credentials(self):
        """The page renders rotate icon micro button next to password, 2-column station grid, and allocation table."""
        b_res = create_booth(election_id=self.election.id, name="Main Aud")
        res = self.client.get(reverse("elections:setup_booths", kwargs={"election_id": self.election.id}))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode("utf-8")

        # Verify 2-column station grid
        self.assertIn("booth-stations-grid", content)
        self.assertIn("Officer station", content)
        self.assertIn("Voting kiosk", content)

        # Verify rotate micro icon is present instead of text button
        self.assertIn("btn-rotate-micro", content)
        self.assertNotIn("btn-rotate-pass", content)

        # Verify cleartext credentials are shown
        self.assertIn(b_res["officer_device"].cleartext_password, content)
        self.assertIn(b_res["kiosk_device"].cleartext_password, content)

        # Verify in-place edit bar is rendered
        self.assertIn("booth-inplace-edit-bar", content)
        self.assertIn("booth-inplace-input", content)

        # Verify voter allocation section and print slips button are rendered
        self.assertIn("Voter allocation", content)
        self.assertIn("Print voter slips", content)

    def test_auto_allocation_and_clear_allocation(self):
        """Test auto-allocation balances voters across booths, and clear allocation resets allocations."""
        from voters.models import AcademicGroup, Voter, ElectionVoter
        grp1 = AcademicGroup.objects.create(name="CS Batch A")
        grp2 = AcademicGroup.objects.create(name="CS Batch B")
        v1 = Voter.objects.create(primary_registry_value="V001", name="Voter 1", academic_group=grp1)
        v2 = Voter.objects.create(primary_registry_value="V002", name="Voter 2", academic_group=grp2)
        v3 = Voter.objects.create(primary_registry_value="V003", name="Voter 3", academic_group=grp1)
        v4 = Voter.objects.create(primary_registry_value="V004", name="Voter 4", academic_group=grp2)
        ElectionVoter.objects.create(election=self.election, voter=v1)
        ElectionVoter.objects.create(election=self.election, voter=v2)
        ElectionVoter.objects.create(election=self.election, voter=v3)
        ElectionVoter.objects.create(election=self.election, voter=v4)

        b1 = create_booth(election_id=self.election.id, name="Booth 1")["booth"]
        b2 = create_booth(election_id=self.election.id, name="Booth 2")["booth"]

        # GET request triggers auto-distribution
        res = self.client.get(reverse("elections:setup_booths", kwargs={"election_id": self.election.id}))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.election.election_voters.filter(booth__isnull=False).count(), 4)

        # Clear allocation action
        res_clear = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {"action": "clear_allocation", "is_ajax": "1"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res_clear.status_code, 200)
        self.assertEqual(self.election.election_voters.filter(booth__isnull=False).count(), 0)

        # GET request after clearing must NOT revert to auto-distribution
        res_after_clear = self.client.get(reverse("elections:setup_booths", kwargs={"election_id": self.election.id}))
        self.assertEqual(res_after_clear.status_code, 200)
        self.assertEqual(self.election.election_voters.filter(booth__isnull=False).count(), 0)

    def test_customize_booth_allocation(self):
        """Admin can assign specific academic groups to a booth, and unassigned voters auto-distribute to other booths."""
        from voters.models import AcademicGroup, Voter, ElectionVoter
        grp_cs = AcademicGroup.objects.create(name="CS Dept")
        grp_ec = AcademicGroup.objects.create(name="EC Dept")
        v1 = Voter.objects.create(primary_registry_value="CS01", name="CS Voter 1", academic_group=grp_cs)
        v2 = Voter.objects.create(primary_registry_value="CS02", name="CS Voter 2", academic_group=grp_cs)
        v3 = Voter.objects.create(primary_registry_value="EC01", name="EC Voter 1", academic_group=grp_ec)
        ev1 = ElectionVoter.objects.create(election=self.election, voter=v1)
        ev2 = ElectionVoter.objects.create(election=self.election, voter=v2)
        ev3 = ElectionVoter.objects.create(election=self.election, voter=v3)

        b1 = create_booth(election_id=self.election.id, name="Booth CS Only")["booth"]
        b2 = create_booth(election_id=self.election.id, name="Booth EC/General")["booth"]

        # Customize booth 1 to only assign CS Dept
        res = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {
                "action": "customize_allocation",
                "booth_id": b1.id,
                "group_ids": [str(grp_cs.id)],
                "is_ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["allocated_count"], 2)

        ev1.refresh_from_db()
        ev2.refresh_from_db()
        ev3.refresh_from_db()
        self.assertEqual(ev1.booth_id, b1.id)
        self.assertEqual(ev2.booth_id, b1.id)
        # ev3 was unassigned from b1 and auto-distributed to the rest of the booths (b2)
        self.assertEqual(ev3.booth_id, b2.id)

    def test_allocation_invariants_and_booth_addition_removal(self):
        """Adding/removing booths auto-distributes voters, and continuing requires 100% voter allocation."""
        from voters.models import AcademicGroup, Voter, ElectionVoter
        grp = AcademicGroup.objects.create(name="General")
        v1 = Voter.objects.create(primary_registry_value="GEN01", name="Voter 1", academic_group=grp)
        v2 = Voter.objects.create(primary_registry_value="GEN02", name="Voter 2", academic_group=grp)
        ElectionVoter.objects.create(election=self.election, voter=v1)
        ElectionVoter.objects.create(election=self.election, voter=v2)

        self.election.setup_stage = 3
        self.election.save(update_fields=["setup_stage"])

        # 1. Add first booth - voters allocated to b1
        b1 = create_booth(election_id=self.election.id, name="Booth 1")["booth"]
        self.client.get(reverse("elections:setup_booths", kwargs={"election_id": self.election.id}))
        self.assertEqual(self.election.election_voters.filter(booth=b1).count(), 2)

        # 2. Add second booth via view action - voters auto-rebalanced across both booths
        self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {"action": "add_booth", "custom_name": "Booth 2"},
        )
        self.assertEqual(self.election.booths.count(), 2)
        b2 = self.election.booths.get(booth_number=2)
        self.assertEqual(self.election.election_voters.filter(booth=b1).count(), 1)
        self.assertEqual(self.election.election_voters.filter(booth=b2).count(), 1)

        # 3. Clear allocations - all unallocated
        self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {"action": "clear_allocation"},
        )
        self.assertEqual(self.election.election_voters.filter(booth__isnull=False).count(), 0)

        # 4. Attempt to continue to Stage 4 with unallocated voters - must be rejected
        res_continue = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {"action": "continue"},
        )
        self.election.refresh_from_db()
        self.assertEqual(self.election.setup_stage, 3)  # Did not advance!

        # 5. Auto-distribute action
        self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {"action": "auto_distribute"},
        )
        self.assertEqual(self.election.election_voters.filter(booth__isnull=True).count(), 0)

        # 6. Delete booth 2 - voters auto-distributed to remaining booth 1
        self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {"action": "delete_booth", "booth_id": b2.id},
        )
        self.assertEqual(self.election.booths.count(), 1)
        self.assertEqual(self.election.election_voters.filter(booth=b1).count(), 2)

    def test_voter_slips_pdf_and_print_views(self):
        """Voter slips PDF generates valid PDF bytes and print view renders A4 slips cards."""
        from voters.models import AcademicGroup, Voter, ElectionVoter
        grp = AcademicGroup.objects.create(name="Engineering")
        voter = Voter.objects.create(primary_registry_value="ENG001", name="Test Student", academic_group=grp)
        b = create_booth(election_id=self.election.id, name="Hall A")["booth"]
        ElectionVoter.objects.create(election=self.election, voter=voter, booth=b)

        # PDF endpoint
        res_pdf = self.client.get(reverse("elections:setup_voter_slips_pdf", kwargs={"election_id": self.election.id}))
        self.assertEqual(res_pdf.status_code, 200)
        self.assertEqual(res_pdf["Content-Type"], "application/pdf")
        self.assertTrue(res_pdf.content.startswith(b"%PDF"))
        self.assertIn("Stage_3_Test_Election_voterslip.pdf", res_pdf["Content-Disposition"])

        # Print endpoint
        res_print = self.client.get(reverse("elections:setup_voter_slips_print", kwargs={"election_id": self.election.id}))
        self.assertEqual(res_print.status_code, 200)
        self.assertContains(res_print, "Test Student")
        self.assertContains(res_print, "ENG001")
        self.assertContains(res_print, "Booth 1")

    def test_clear_allocation_stops_auto_allocation_until_auto_distribute(self):
        """Clear allocation stops auto-allocation for manual allocation; slips printable only after all voters allocated."""
        from voters.models import AcademicGroup, Voter, ElectionVoter
        grp1 = AcademicGroup.objects.create(name="Group Alpha")
        grp2 = AcademicGroup.objects.create(name="Group Beta")
        v1 = Voter.objects.create(primary_registry_value="A01", name="Alpha Voter 1", academic_group=grp1)
        v2 = Voter.objects.create(primary_registry_value="B01", name="Beta Voter 1", academic_group=grp2)
        ev1 = ElectionVoter.objects.create(election=self.election, voter=v1)
        ev2 = ElectionVoter.objects.create(election=self.election, voter=v2)

        b1 = create_booth(election_id=self.election.id, name="Booth 1")["booth"]
        b2 = create_booth(election_id=self.election.id, name="Booth 2")["booth"]

        # Initially, loading the page auto-distributes all voters
        self.client.get(reverse("elections:setup_booths", kwargs={"election_id": self.election.id}))
        ev1.refresh_from_db()
        ev2.refresh_from_db()
        self.assertIsNotNone(ev1.booth)
        self.assertIsNotNone(ev2.booth)

        # 1. Clear allocation
        res_clear = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {"action": "clear_allocation"},
        )
        self.assertEqual(res_clear.status_code, 302)
        self.assertEqual(self.election.election_voters.filter(booth__isnull=False).count(), 0)

        # 2. Loading the page does NOT auto-allocate (auto-allocation is stopped)
        self.client.get(reverse("elections:setup_booths", kwargs={"election_id": self.election.id}))
        self.assertEqual(self.election.election_voters.filter(booth__isnull=False).count(), 0)

        # 3. Voter slips cannot be printed while unallocated voters exist
        res_pdf_blocked = self.client.get(reverse("elections:setup_voter_slips_pdf", kwargs={"election_id": self.election.id}))
        self.assertEqual(res_pdf_blocked.status_code, 302)
        self.assertIn("setup/booths", res_pdf_blocked.url)

        res_print_blocked = self.client.get(reverse("elections:setup_voter_slips_print", kwargs={"election_id": self.election.id}))
        self.assertEqual(res_print_blocked.status_code, 302)
        self.assertIn("setup/booths", res_print_blocked.url)

        # 4. Manually allocate Group Alpha to Booth 1
        self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {
                "action": "customize_allocation",
                "booth_id": b1.id,
                "group_ids": [str(grp1.id)],
            },
        )
        ev1.refresh_from_db()
        ev2.refresh_from_db()
        self.assertEqual(ev1.booth_id, b1.id)
        # ev2 (Group Beta) must REMAIN UNALLOCATED during manual allocation
        self.assertIsNone(ev2.booth_id)

        # Adding a booth during manual mode does NOT auto-distribute unallocated voters
        create_booth(election_id=self.election.id, name="Booth 3")
        self.client.get(reverse("elections:setup_booths", kwargs={"election_id": self.election.id}))
        ev2.refresh_from_db()
        self.assertIsNone(ev2.booth_id)

        # 5. User clicks "auto distribute"
        res_dist = self.client.post(
            reverse("elections:setup_booths", kwargs={"election_id": self.election.id}),
            {"action": "auto_distribute"},
        )
        self.assertEqual(res_dist.status_code, 302)
        ev2.refresh_from_db()
        self.assertIsNotNone(ev2.booth_id)
        self.assertEqual(self.election.election_voters.filter(booth__isnull=True).count(), 0)

        # 6. Now slips CAN be printed
        res_pdf_ok = self.client.get(reverse("elections:setup_voter_slips_pdf", kwargs={"election_id": self.election.id}))
        self.assertEqual(res_pdf_ok.status_code, 200)
        self.assertEqual(res_pdf_ok["Content-Type"], "application/pdf")


class ElectionHomeDashboardHubStatsTests(TestCase):
    """Test suite verifying hub statistics on the election home dashboard."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="adminhubstats",
            password="adminpassword123",
            role=Role.ADMIN,
        )
        self.client.force_login(self.admin)

    def test_dashboard_election_history_card_stats(self):
        """Dashboard hub card displays completed elections count and total votes registered across completed elections."""
        from voters.models import ElectionVoter, Voter, VoterRegistry

        reg = VoterRegistry.objects.create(name="Main Registry")
        v1 = Voter.objects.create(registry=reg, primary_registry_value="REG001")
        v2 = Voter.objects.create(registry=reg, primary_registry_value="REG002")
        v3 = Voter.objects.create(registry=reg, primary_registry_value="REG003")

        # 1. Closed past election with 2 votes recorded
        el_closed = Election.objects.create(
            name="Past Closed Election",
            status=ElectionStatus.CLOSED,
            closed_at=timezone.now(),
        )
        ElectionVoter.objects.create(election=el_closed, voter=v1, has_voted=True)
        ElectionVoter.objects.create(election=el_closed, voter=v2, has_voted=True)
        ElectionVoter.objects.create(election=el_closed, voter=v3, has_voted=False)

        # 2. Results published past election with 1 vote recorded
        el_published = Election.objects.create(
            name="Past Published Election",
            status=ElectionStatus.RESULTS_PUBLISHED,
            closed_at=timezone.now(),
        )
        ElectionVoter.objects.create(election=el_published, voter=v1, has_voted=True)
        ElectionVoter.objects.create(election=el_published, voter=v2, has_voted=False)

        # 3. Active ongoing election (should NOT be counted in completed elections or completed votes)
        el_active = Election.objects.create(
            name="Ongoing Active Election",
            status=ElectionStatus.ACTIVE,
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(hours=2),
        )
        ElectionVoter.objects.create(election=el_active, voter=v1, has_voted=True)

        # 4. Draft election (should NOT be counted)
        el_draft = Election.objects.create(
            name="Draft Election",
            status=ElectionStatus.DRAFT,
        )
        ElectionVoter.objects.create(election=el_draft, voter=v2, has_voted=False)

        # Request dashboard
        res = self.client.get(reverse("elections:dashboard"))
        self.assertEqual(res.status_code, 200)

        # Verify context variables
        self.assertEqual(res.context["completed_elections"], 2)
        self.assertEqual(res.context["total_votes_registered"], 3)  # 2 from el_closed + 1 from el_published

        # Verify template rendering
        content = res.content.decode("utf-8")
        self.assertIn("Completed elections", content)
        self.assertIn("Votes registered", content)


class ElectionBoothCredentialsAndVoterSlipTests(TestCase):
    """Test suite for past credential cleanup, 3x11 voter slips, and booth allocation UI."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="adminboothcleanup",
            password="adminpassword123",
            role=Role.ADMIN,
        )
        self.client.force_login(self.admin)

    def test_cleanup_past_credentials_removes_stale_station_users(self):
        """Past credentials not in use by draft or active elections are purged automatically."""
        from accounts.models import Device, Role, User
        from accounts.services import cleanup_past_credentials
        from voters.services import create_booth

        # 1. Past election with booth & devices that was subsequently closed
        past_election = Election.objects.create(
            name="Past Closed Election",
            status=ElectionStatus.DRAFT,
        )
        b_past = create_booth(election_id=past_election.id, name="Old Hall")
        past_election.status = ElectionStatus.CLOSED
        past_election.closed_at = timezone.now()
        past_election.save(update_fields=["status", "closed_at"])
        officer_user = b_past["officer_device"].user
        kiosk_user = b_past["kiosk_device"].user

        # 2. Draft election with booth & devices
        draft_election = Election.objects.create(
            name="Draft Election",
            status=ElectionStatus.DRAFT,
        )
        b_draft = create_booth(election_id=draft_election.id, name="Active Hall")
        draft_officer_user = b_draft["officer_device"].user

        # Run cleanup
        cleanup_past_credentials()

        # Stale past devices/users must be deleted
        self.assertFalse(User.objects.filter(id=officer_user.id).exists())
        self.assertFalse(User.objects.filter(id=kiosk_user.id).exists())

        # Active draft devices/users must be preserved
        self.assertTrue(User.objects.filter(id=draft_officer_user.id).exists())

    def test_booth_creation_recycles_or_avoids_duplicate_station_username(self):
        """Adding booths automatically cleans past credentials and guarantees unique usernames without IntegrityError."""
        from accounts.models import Device, Role, User
        from voters.services import create_booth

        # 1. Completed election creates a booth with identifier officer-meadow-1
        past_election = Election.objects.create(
            name="Past Election",
            status=ElectionStatus.CLOSED,
            closed_at=timezone.now(),
        )
        # Create an orphan officer user intentionally mimicking past credentials
        User.objects.create_user(username="officer-meadow-1", role=Role.OFFICER)

        # 2. In a new draft election, creating a booth should automatically purge unused credentials
        draft_election = Election.objects.create(
            name="New Draft Election",
            status=ElectionStatus.DRAFT,
        )
        # Should not raise IntegrityError
        res = create_booth(election_id=draft_election.id, name="Room 101")
        self.assertIsNotNone(res["booth"])
        self.assertIsNotNone(res["officer_device"])
        self.assertIsNotNone(res["kiosk_device"])

    def test_voter_slip_named_as_electionname_voterslip(self):
        """Voter slips PDF filename is formatted as <electionname>_voterslip.pdf."""
        from voters.models import ElectionVoter, Voter, VoterRegistry
        from voters.services import create_booth

        election = Election.objects.create(
            name="College Union Election 2026",
            status=ElectionStatus.DRAFT,
        )
        booth_res = create_booth(election_id=election.id, name="Main Booth")
        reg = VoterRegistry.objects.create(name="Student Reg")
        voter = Voter.objects.create(registry=reg, primary_registry_value="STU101", name="Jane Doe")
        ElectionVoter.objects.create(election=election, voter=voter, booth=booth_res["booth"])

        res = self.client.get(reverse("elections:setup_voter_slips_pdf", kwargs={"election_id": election.id}))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertIn("College_Union_Election_2026_voterslip.pdf", res["Content-Disposition"])

    def test_booth_allocation_page_no_bottom_back_continue(self):
        """Stage 3 booth allocation page has Back/Continue removed from bottom row."""
        election = Election.objects.create(
            name="Stage 3 Test Page",
            status=ElectionStatus.DRAFT,
        )
        res = self.client.get(reverse("elections:setup_booths", kwargs={"election_id": election.id}))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode("utf-8")

        # Bottom row must not contain Continue button or Back link
        self.assertNotIn('id="btn-bottom-continue"', content)
        self.assertNotIn('setup-bottom-actions-row">\n                    <div class="bottom-actions-left">', content)
        # Top row continues to have Back & Continue
        self.assertIn('id="btn-top-continue"', content)


class ElectionSetupReviewWorkflowTests(TestCase):
    """Tests for Stage 4: Review & start election (references/06.4)."""

    def setUp(self):
        from accounts.models import User, Role
        from voters.models import VoterRegistry, AcademicGroup, Voter, ElectionVoter
        from voters.services import enroll_voters_in_election, create_booth

        self.admin = User.objects.create_user(
            username="review_admin",
            password="adminpassword123",
            role=Role.ADMIN,
        )
        self.client.login(username="review_admin", password="adminpassword123")

        # Setup registry, groups, voters
        self.registry = VoterRegistry.objects.create(name="Review Campus Registry")
        self.grp1 = AcademicGroup.objects.create(registry=self.registry, name="Computer Science")
        self.grp2 = AcademicGroup.objects.create(registry=self.registry, name="Data Science")
        self.voter1 = Voter.objects.create(registry=self.registry, primary_registry_value="CS01", name="Dev CS", academic_group=self.grp1)
        self.voter2 = Voter.objects.create(registry=self.registry, primary_registry_value="DS01", name="Sam DS", academic_group=self.grp2)

        # Setup draft election
        self.election = create_election(
            name="Campus Union 2026",
            description="Official union elections for 2026.",
            voter_registry=self.registry,
        )
        enroll_voters_in_election(election_id=self.election.id, voter_ids=[self.voter1.id, self.voter2.id])

        # Positions and candidate
        pos = create_position(election_id=self.election.id, name="President")
        create_candidate(position_id=pos.id, voter_id=self.voter1.id, name="Dev CS", symbol="Star")

        # Booths and allocation
        b_res = create_booth(election_id=self.election.id, name="Main Auditorium")
        self.booth = b_res["booth"]
        ev1 = ElectionVoter.objects.get(election=self.election, voter=self.voter1)
        ev1.booth = self.booth
        ev1.save()
        ev2 = ElectionVoter.objects.get(election=self.election, voter=self.voter2)
        ev2.booth = self.booth
        ev2.save()

    def test_setup_review_get_renders_accordions_and_stored_data(self):
        res = self.client.get(reverse("elections:setup_review", kwargs={"election_id": self.election.id}))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["active_step"], 4)
        
        # Header assertions
        self.assertContains(res, "Review and start election")
        self.assertContains(res, "Exit")
        self.assertContains(res, reverse("elections:setup_booths", kwargs={"election_id": self.election.id}))
        
        # Sections assertions
        self.assertContains(res, "Election information")
        self.assertContains(res, "Campus Union 2026")
        self.assertContains(res, "Official union elections for 2026.")
        
        self.assertContains(res, "Election voters")
        self.assertContains(res, "Review Campus Registry")
        self.assertContains(res, "Computer Science")
        self.assertContains(res, "Data Science")
        
        self.assertContains(res, "Positions and candidates")
        self.assertContains(res, "President")
        self.assertContains(res, "Dev CS")
        self.assertContains(res, "Star")
        
        self.assertContains(res, "Booths and allocation")
        self.assertContains(res, "Booth 1")
        self.assertContains(res, "Main Auditorium")
        self.assertContains(res, "Officer: Not in session")
        self.assertContains(res, "Kiosk: Not in session")

        # "Change" links replacing "Edit"
        self.assertContains(res, "Change")
        self.assertContains(res, reverse("elections:setup_details", kwargs={"election_id": self.election.id}))
        self.assertContains(res, reverse("elections:setup_voters", kwargs={"election_id": self.election.id}))

        # Validation status section assertions
        self.assertContains(res, "Validation status")
        self.assertContains(res, "All required settings and system checks.")
        self.assertContains(res, "Election details provided")
        self.assertContains(res, "Voters selected")
        self.assertContains(res, "At least one position added")
        self.assertContains(res, "Candidates added for all positions")
        self.assertContains(res, "Booths created")
        self.assertContains(res, "All voters allocated to booths")
        self.assertContains(res, "All booths paired with devices")
        self.assertContains(res, "No duplicate candidates")

    def test_setup_review_save_draft_action(self):
        res = self.client.post(
            reverse("elections:setup_review", kwargs={"election_id": self.election.id}),
            {"action": "save_draft"},
            follow=True,
        )
        self.assertRedirects(res, reverse("elections:dashboard"))
        self.election.refresh_from_db()
        self.assertTrue(self.election.is_saved_draft)

    def test_save_draft_does_not_open_change_password_popover(self):
        """Saving draft redirects to dashboard with a flash message; settings popover must NOT auto-open."""
        res = self.client.post(
            reverse("elections:setup_review", kwargs={"election_id": self.election.id}),
            {"action": "save_draft"},
            follow=True,
        )
        self.assertRedirects(res, reverse("elections:dashboard"))
        # Settings popover must be hidden
        self.assertContains(res, '<div id="settings-popover" class="home-settings-popover" role="dialog" aria-label="Change Credentials" hidden>')
        # The message should NOT trigger openPopover() in the script
        content = res.content.decode()
        self.assertNotIn("openPopover();", content)
        # Popover messages container should NOT contain the election draft saved message
        self.assertNotIn("popover-msg", content)

    def test_past_device_credentials_and_sessions_cleaned_up_on_close_election(self):
        """When an election is closed, non-active/draft device credentials and active sessions are purged."""
        from accounts.services import create_device_session

        booth_res = create_booth(election_id=self.election.id, name="Booth Clean")
        off_dev = booth_res["officer_device"]
        kiosk_dev = booth_res["kiosk_device"]

        # Create active sessions
        create_device_session(off_dev, "sess_key_off_01")
        create_device_session(kiosk_dev, "sess_key_kiosk_01")

        self.assertEqual(DeviceSession.objects.filter(device=off_dev).count(), 1)
        self.assertEqual(DeviceSession.objects.filter(device=kiosk_dev).count(), 1)

        # Transition to ACTIVE so close_election can be called
        self.election.status = ElectionStatus.ACTIVE
        self.election.save()

        close_election(election_id=self.election.id)

        # Non-active/draft credentials and sessions are purged
        self.assertFalse(DeviceSession.objects.filter(device=off_dev).exists())
        self.assertFalse(DeviceSession.objects.filter(device=kiosk_dev).exists())
        self.assertFalse(Device.objects.filter(id=off_dev.id).exists())
        self.assertFalse(Device.objects.filter(id=kiosk_dev.id).exists())
        self.assertFalse(User.objects.filter(username=off_dev.identifier).exists())
        self.assertFalse(User.objects.filter(username=kiosk_dev.identifier).exists())














