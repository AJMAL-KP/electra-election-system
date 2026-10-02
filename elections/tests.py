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

    def test_initial_state_is_draft(self):
        """Created election is in DRAFT status."""
        self.assertEqual(self.election.status, ElectionStatus.DRAFT)
        self.assertTrue(self.election.is_draft)
        self.assertFalse(self.election.is_active)

    def test_cannot_start_without_positions(self):
        """Configuration validation prevents starting an election with 0 positions."""
        errors = validate_election_configuration(self.election)
        self.assertTrue(any("no ballot positions" in err.lower() for err in errors))
        with self.assertRaises(ValidationError):
            start_election(election_id=self.election.id)

    def test_cannot_start_if_position_has_no_candidates(self):
        """Configuration validation prevents starting if any position has 0 candidates."""
        pos = create_position(election_id=self.election.id, name="President", display_order=1)
        errors = validate_election_configuration(self.election)
        self.assertTrue(any("no registered candidates" in err.lower() for err in errors))
        with self.assertRaises(ValidationError):
            start_election(election_id=self.election.id)

    def test_successful_activation_when_valid(self):
        """Valid configuration allows transition DRAFT -> ACTIVE."""
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
        pos1 = create_position(election_id=self.election.id, name="President", display_order=1)
        create_candidate(position_id=pos1.id, name="Alice")
        start_election(election_id=self.election.id)

        # Create second election
        election2 = create_election(
            name="Second Election",
            starts_at=self.starts_at,
            ends_at=self.ends_at,
        )
        pos2 = create_position(election_id=election2.id, name="Secretary", display_order=1)
        create_candidate(position_id=pos2.id, name="Dave")

        with self.assertRaises(ValidationError):
            start_election(election_id=election2.id)

    def test_configuration_freeze_after_activation(self):
        """Once ACTIVE, configuration (positions, candidates, election parameters) cannot be modified."""
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
        self.assertContains(response, "Leaderboard")

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
