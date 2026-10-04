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

from accounts.models import Role, User
from elections.models import Candidate, Election, ElectionStatus, Position
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

        # View voters page for election
        response = self.client.get(reverse("elections:voters", kwargs={"election_id": election.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enrolled Voters: Enrollment Tab Test")

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

        # 1. GET booth management view
        url = reverse("elections:booths", kwargs={"election_id": election.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Booths &amp; Device Stations")

        # 2. POST create booth
        create_res = self.client.post(reverse("elections:booth_create", kwargs={"election_id": election.id}), {
            "name": "Gymnasium East",
        })
        self.assertEqual(create_res.status_code, 302)
        booth = election.booths.first()
        self.assertIsNotNone(booth)
        self.assertEqual(booth.booth_number, 1)
        self.assertEqual(booth.name, "Gymnasium East")

        # Session should contain new credentials notice
        session_res = self.client.get(url)
        self.assertContains(session_res, "Station Credentials Generated")
        self.assertContains(session_res, "booth-1-officer")
        self.assertContains(session_res, "booth-1-kiosk")

        # 3. POST allocate all unallocated voters to booth
        alloc_res = self.client.post(reverse("elections:booth_allocate", kwargs={"election_id": election.id, "booth_id": booth.id}), {
            "allocate_all_unallocated": "1",
        })
        self.assertEqual(alloc_res.status_code, 302)
        self.assertEqual(ElectionVoter.objects.filter(election=election, booth=booth).count(), 2)

        # 4. GET printable roster sheet
        roster_res = self.client.get(reverse("elections:booth_roster", kwargs={"election_id": election.id, "booth_id": booth.id}))
        self.assertEqual(roster_res.status_code, 200)
        self.assertContains(roster_res, "Official Voter Roster & Sign-in Sheet")
        self.assertContains(roster_res, "BV001")
        self.assertContains(roster_res, "Booth Voter 1")
        self.assertContains(roster_res, "Voter Signature")

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





