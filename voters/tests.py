"""Comprehensive test suite for voters application (Phase 4).

Covers:
- Installation boundary (no owner or tenant scoping)
- Configurable primary registry identity uniqueness
- AcademicGroup hierarchy representations
- CSV & Excel (.xlsx) importer parsing, pre-flight validation, and dry-run preview
- Duplicate detection within files and against existing registry records
- Atomic persistence and auto-group creation
- Election enrollment with configuration freeze enforcement (DRAFT only)
- RBAC: Administrator authorization on voter registry views
"""
import io
from datetime import timedelta
import openpyxl
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from elections.models import Election, ElectionStatus
from elections.services import create_election, start_election, create_position, create_candidate
from voters.importers import (
    parse_csv_content,
    parse_excel_content,
    process_voter_import,
)
from voters.models import AcademicGroup, AcademicGroupType, ElectionVoter, Voter, VoterRegistry
from voters.selectors import (
    get_voter,
    get_voter_by_registry_value,
    list_academic_groups,
    list_top_level_groups,
    list_voters,
)
from voters.services import (
    create_academic_group,
    create_voter,
    create_voter_registry,
    delete_academic_group,
    delete_voter,
    delete_voter_registry,
    enroll_voters_in_election,
    remove_voter_from_election,
    update_academic_group,
    update_voter,
)


class VoterModelAndRegistryTests(TestCase):
    def test_voter_installation_boundary_no_tenant_field(self):
        """Voter registry belongs to installation with no tenant or user ownership."""
        fields = [f.name for f in Voter._meta.get_fields()]
        self.assertNotIn("owner", fields)
        self.assertNotIn("user", fields)
        self.assertNotIn("tenant", fields)

    def test_primary_registry_value_must_be_unique(self):
        """No two voters can share the same primary registry identifier."""
        create_voter(primary_registry_value="STU001", name="Alice Smith")
        with self.assertRaises(ValidationError):
            create_voter(primary_registry_value="STU001", name="Bob Jones")

    def test_academic_group_hierarchy(self):
        """AcademicGroup supports two-level Group -> Sub Group hierarchy."""
        group = create_academic_group(name="Computer Science")
        subgroup = create_academic_group(name="Batch A", parent_id=group.id)

        self.assertEqual(subgroup.parent, group)
        self.assertIn(subgroup, group.children.all())
        self.assertEqual(group.type, AcademicGroupType.GROUP)
        self.assertEqual(subgroup.type, AcademicGroupType.SUBGROUP)

        # Duplicate under same parent rejected
        with self.assertRaises(ValidationError):
            create_academic_group(name="Batch A", parent_id=group.id)

    def test_voter_update_and_deletion(self):
        """Voters can be updated or deleted if they have not voted."""
        voter = create_voter(primary_registry_value="STU002", name="Charlie Brown")
        updated = update_voter(voter_id=voter.id, name="Charlie Modified", gender="Male")
        self.assertEqual(updated.name, "Charlie Modified")
        self.assertEqual(updated.gender, "Male")

        delete_voter(voter_id=voter.id)
        self.assertIsNone(get_voter(voter.id))

    def test_cannot_delete_voter_who_has_voted(self):
        """A voter who has committed a ballot cannot be deleted from the database."""
        now = timezone.now()
        election = create_election(
            name="Test Election",
            starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(hours=8),
        )
        voter = create_voter(primary_registry_value="STU003", name="Diana Prince")
        ev = ElectionVoter.objects.create(election=election, voter=voter, has_voted=True)

        with self.assertRaises(ValidationError):
            delete_voter(voter_id=voter.id)

    def test_cannot_delete_voter_when_registry_is_linked_to_election(self):
        """Voter cannot be deleted if the registry is directly linked to an election."""
        now = timezone.now()
        registry = VoterRegistry.objects.create(name="College Registry")
        voter = create_voter(primary_registry_value="STU004", name="Eve Clark", registry_id=registry.id)
        el = create_election(
            name="College Election",
            starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(hours=8),
            voter_registry=registry,
        )
        el.status = ElectionStatus.ACTIVE
        el.save()
        with self.assertRaises(ValidationError) as ctx:
            delete_voter(voter_id=voter.id)
        self.assertIn("linked to one or more elections", str(ctx.exception))

    def test_cannot_delete_group_when_registry_is_linked_to_election(self):
        """Group cannot be deleted if the registry is directly linked to an election."""
        now = timezone.now()
        registry = VoterRegistry.objects.create(name="Univ Registry")
        group = create_academic_group(name="Physics", registry_id=registry.id)
        el = create_election(
            name="Univ Election",
            starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(hours=8),
            voter_registry=registry,
        )
        el.status = ElectionStatus.ACTIVE
        el.save()
        from voters.services import delete_academic_group
        with self.assertRaises(ValidationError) as ctx:
            delete_academic_group(group_id=group.id)
        self.assertIn("linked to one or more elections", str(ctx.exception))

    def test_registry_has_subgroups_property(self):
        """has_subgroups is True only when subgroup_source is non-empty and not (None)."""
        reg1 = VoterRegistry.objects.create(name="Reg 1", subgroup_source="Semester")
        self.assertTrue(reg1.has_subgroups)

        reg2 = VoterRegistry.objects.create(name="Reg 2", subgroup_source="")
        self.assertFalse(reg2.has_subgroups)

        reg3 = VoterRegistry.objects.create(name="Reg 3", subgroup_source="(None)")
        self.assertFalse(reg3.has_subgroups)


class VoterImporterTests(TestCase):
    def test_csv_importer_dry_run_and_commit(self):
        """CSV importer dry-run identifies valid rows and commit creates records."""
        csv_data = (
            "student_id,full_name,department,gender\n"
            "STU101,John Doe,Computer Science,Male\n"
            "STU102,Jane Doe,Mechanical,Female\n"
        )
        file_obj = io.BytesIO(csv_data.encode("utf-8"))
        headers, data_rows = parse_csv_content(file_obj)

        # Dry run
        dry_result = process_voter_import(headers=headers, data_rows=data_rows, dry_run=True)
        self.assertEqual(dry_result.total_rows, 2)
        self.assertEqual(dry_result.valid_rows, 2)
        self.assertEqual(dry_result.new_voters, 2)
        self.assertEqual(Voter.objects.count(), 0)  # No DB writes in dry-run

        # Commit run
        commit_result = process_voter_import(headers=headers, data_rows=data_rows, dry_run=False)
        self.assertEqual(commit_result.created_count, 2)
        self.assertEqual(Voter.objects.count(), 2)

        voter1 = Voter.objects.get(primary_registry_value="STU101")
        self.assertEqual(voter1.name, "John Doe")
        self.assertEqual(voter1.academic_group.name, "Computer Science")

    def test_csv_importer_duplicate_detection(self):
        """Duplicate identifiers within the file are flagged as errors."""
        csv_data = (
            "id,name\n"
            "STU201,Alice\n"
            "STU201,Alice Duplicate\n"
        )
        file_obj = io.BytesIO(csv_data.encode("utf-8"))
        headers, data_rows = parse_csv_content(file_obj)
        result = process_voter_import(headers=headers, data_rows=data_rows, dry_run=True)

        self.assertTrue(result.has_errors)
        self.assertTrue(any("duplicate" in err['errors'][0].lower() for err in result.errors))

    def test_excel_importer(self):
        """Excel .xlsx file parses correctly and persists voter records."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Roll No", "Student Name", "Class", "Gender"])
        ws.append(["EX001", "Emma Watson", "Grade 12", "Female"])
        ws.append(["EX002", "Harry Potter", "Grade 12", "Male"])

        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        headers, data_rows = parse_excel_content(buffer)
        result = process_voter_import(headers=headers, data_rows=data_rows, dry_run=False)

        self.assertEqual(result.created_count, 2)
        self.assertTrue(Voter.objects.filter(primary_registry_value="EX001").exists())
        self.assertTrue(Voter.objects.filter(primary_registry_value="EX002").exists())


class ElectionEnrollmentTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.starts_at = self.now + timedelta(hours=1)
        self.ends_at = self.now + timedelta(hours=8)
        self.election = create_election(
            name="Council 2026",
            starts_at=self.starts_at,
            ends_at=self.ends_at,
        )
        self.voter1 = create_voter(primary_registry_value="ENR01", name="Voter One")
        self.voter2 = create_voter(primary_registry_value="ENR02", name="Voter Two")

    def test_enroll_voters_in_draft_election(self):
        """Voters can be enrolled into a draft election."""
        count = enroll_voters_in_election(
            election_id=self.election.id,
            voter_ids=[self.voter1.id, self.voter2.id]
        )
        self.assertEqual(count, 2)
        self.assertEqual(self.election.election_voters.count(), 2)

        # Re-enrolling does not create duplicate rows
        re_count = enroll_voters_in_election(
            election_id=self.election.id,
            voter_ids=[self.voter1.id]
        )
        self.assertEqual(re_count, 0)
        self.assertEqual(self.election.election_voters.count(), 2)

    def test_cannot_enroll_voters_after_activation(self):
        """Configuration freeze prevents enrolling voters once election is ACTIVE."""
        from accounts.services import create_device_session
        from voters.services import create_booth
        pos = create_position(election_id=self.election.id, name="President")
        create_candidate(position_id=pos.id, name="Alice", symbol="Torch")
        enroll_voters_in_election(election_id=self.election.id, voter_ids=[self.voter1.id])
        booth_res = create_booth(election_id=self.election.id)
        ElectionVoter.objects.filter(election=self.election).update(booth=booth_res['booth'])
        create_device_session(booth_res['officer_device'], "sess-off-freeze")
        create_device_session(booth_res['kiosk_device'], "sess-kio-freeze")
        start_election(election_id=self.election.id)

        voter3 = create_voter(primary_registry_value="ENR03", name="Voter Three")
        with self.assertRaises(ValidationError):
            enroll_voters_in_election(
                election_id=self.election.id,
                voter_ids=[voter3.id]
            )

    def test_remove_voter_from_election(self):
        """Enrolled voters can be removed only when election is in DRAFT."""
        enroll_voters_in_election(election_id=self.election.id, voter_ids=[self.voter1.id])
        self.assertEqual(self.election.election_voters.count(), 1)

        remove_voter_from_election(election_id=self.election.id, voter_id=self.voter1.id)
        self.assertEqual(self.election.election_voters.count(), 0)


class VoterViewsAndRBACTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="adminuser",
            password="adminpassword123",
            role=Role.ADMIN,
        )
        self.officer = User.objects.create_user(
            username="officeruser",
            password="officerpassword123",
            role=Role.OFFICER,
        )

    def test_officer_rejected_from_voter_registry(self):
        """Non-admin identities are rejected with 403 on voter views."""
        self.client.login(username="officeruser", password="officerpassword123")
        response = self.client.get(reverse("voters:registry"))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_registry_and_create_voter(self):
        """Administrator can view registry and submit voter creation form."""
        self.client.login(username="adminuser", password="adminpassword123")
        response = self.client.get(reverse("voters:registry"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Central Voter Registry")

        # Create voter via POST
        post_response = self.client.post(reverse("voters:create"), {
            "primary_registry_value": "REG999",
            "name": "Sarah Connor",
            "gender": "Female",
        })
        self.assertEqual(post_response.status_code, 302)
        self.assertTrue(Voter.objects.filter(primary_registry_value="REG999").exists())


class BoothModelAndConstraintTests(TestCase):
    def setUp(self):
        self.election1 = create_election(
            name="General Council Election 2026",
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=2),
        )
        self.election2 = create_election(
            name="Department Senate Election 2026",
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=2),
        )
        self.voter = create_voter(primary_registry_value="V001", name="Alice Voter")

    def test_booth_creation_and_display_name(self):
        from voters.models import Booth
        booth1 = Booth.objects.create(
            election=self.election1,
            booth_number=1,
        )
        self.assertEqual(booth1.display_name, "Booth 1")
        self.assertEqual(str(booth1), "Booth 1")

        booth2 = Booth.objects.create(
            election=self.election1,
            booth_number=2,
            name="Auditorium West",
        )
        self.assertEqual(booth2.display_name, "Booth 2 (Auditorium West)")
        self.assertEqual(str(booth2), "Booth 2 (Auditorium West)")

    def test_unique_booth_number_per_election(self):
        from voters.models import Booth
        from django.db import IntegrityError, transaction
        Booth.objects.create(
            election=self.election1,
            booth_number=1,
        )
        # Same booth_number in same election must raise IntegrityError
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Booth.objects.create(
                    election=self.election1,
                    booth_number=1,
                )

        # Same booth_number in different election is allowed
        booth_diff_election = Booth.objects.create(
            election=self.election2,
            booth_number=1,
        )
        self.assertIsNotNone(booth_diff_election.pk)

    def test_device_booth_binding_and_unique_per_booth_constraint(self):
        from accounts.models import Device, DeviceType, Role, User
        from voters.models import Booth
        from django.db import IntegrityError, transaction

        booth = Booth.objects.create(election=self.election1, booth_number=1)

        user_officer1 = User.objects.create_user(username="booth-1-officer", role=Role.OFFICER)
        device_officer1 = Device.objects.create(
            user=user_officer1,
            identifier="booth-1-officer",
            device_type=DeviceType.OFFICER,
            booth=booth,
        )
        self.assertEqual(device_officer1.booth, booth)

        user_kiosk = User.objects.create_user(username="booth-1-kiosk", role=Role.KIOSK)
        device_kiosk = Device.objects.create(
            user=user_kiosk,
            identifier="booth-1-kiosk",
            device_type=DeviceType.KIOSK,
            booth=booth,
        )
        self.assertEqual(device_kiosk.booth, booth)

        # Attempting to assign a second OFFICER device to the same booth must fail
        user_officer2 = User.objects.create_user(username="booth-1-officer-duplicate", role=Role.OFFICER)
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Device.objects.create(
                    user=user_officer2,
                    identifier="booth-1-officer-duplicate",
                    device_type=DeviceType.OFFICER,
                    booth=booth,
                )

    def test_election_voter_booth_allocation(self):
        from voters.models import Booth, ElectionVoter
        booth = Booth.objects.create(election=self.election1, booth_number=1)
        ev = ElectionVoter.objects.create(
            election=self.election1,
            voter=self.voter,
            booth=booth,
        )
        self.assertEqual(ev.booth, booth)
        self.assertIn(ev, booth.allocated_voters.all())


class BoothServiceTests(TestCase):
    def setUp(self):
        self.election = create_election(
            name="Student Council 2026",
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=2),
        )
        self.voter = create_voter(primary_registry_value="V100", name="Bob Tester")

    def test_create_booth_auto_provisions_paired_devices_and_credentials(self):
        from accounts.models import Device, DeviceType, Role, User
        from django.contrib.auth import authenticate
        from voters.services import create_booth

        result = create_booth(election_id=self.election.id, name="Main Auditorium")
        booth = result['booth']
        self.assertEqual(booth.booth_number, 1)
        self.assertEqual(booth.name, "Main Auditorium")
        self.assertEqual(booth.display_name, "Booth 1 (Main Auditorium)")

        # Verify Officer Device & User
        officer_dev = result['officer_device']
        self.assertTrue(officer_dev.identifier.startswith("officer-"))
        self.assertTrue(officer_dev.identifier.endswith(f"-{booth.booth_number}"))
        self.assertEqual(officer_dev.device_type, DeviceType.OFFICER)
        self.assertEqual(officer_dev.booth, booth)
        auth_officer = authenticate(username=result['officer_username'], password=result['officer_password'])
        self.assertIsNotNone(auth_officer)
        self.assertEqual(auth_officer.role, Role.OFFICER)

        # Verify Kiosk Device & User
        kiosk_dev = result['kiosk_device']
        self.assertTrue(kiosk_dev.identifier.startswith("kiosk-"))
        self.assertTrue(kiosk_dev.identifier.endswith(f"-{booth.booth_number}"))
        self.assertEqual(kiosk_dev.device_type, DeviceType.KIOSK)
        self.assertEqual(kiosk_dev.booth, booth)
        auth_kiosk = authenticate(username=result['kiosk_username'], password=result['kiosk_password'])
        self.assertIsNotNone(auth_kiosk)
        self.assertEqual(auth_kiosk.role, Role.KIOSK)

    def test_create_booth_auto_increments_number(self):
        from voters.services import create_booth
        res1 = create_booth(election_id=self.election.id)
        res2 = create_booth(election_id=self.election.id)
        res3 = create_booth(election_id=self.election.id)
        self.assertEqual(res1['booth'].booth_number, 1)
        self.assertEqual(res2['booth'].booth_number, 2)
        self.assertEqual(res3['booth'].booth_number, 3)

    def test_cannot_create_or_remove_booth_when_election_is_active(self):
        from accounts.models import User
        from elections.services import create_candidate, create_position, start_election
        from voters.services import create_booth, enroll_voters_in_election, remove_booth

        # Setup required configuration to start election
        pos = create_position(election_id=self.election.id, name="President", display_order=1)
        create_candidate(position_id=pos.id, name="Alice")
        enroll_voters_in_election(election_id=self.election.id, voter_ids=[self.voter.id])

        booth_res = create_booth(election_id=self.election.id)
        booth = booth_res['booth']

        # Allocate voter
        from voters.models import ElectionVoter
        ElectionVoter.objects.filter(election=self.election).update(booth=booth)

        # Simulate logged in stations
        from accounts.services import create_device_session
        create_device_session(booth_res['officer_device'], "session-officer-1")
        create_device_session(booth_res['kiosk_device'], "session-kiosk-1")

        start_election(election_id=self.election.id)

        # Attempting to create booth in ACTIVE election must fail
        with self.assertRaises(ValidationError):
            create_booth(election_id=self.election.id)

        # Attempting to remove booth in ACTIVE election must fail
        with self.assertRaises(ValidationError):
            remove_booth(booth_id=booth.id)

    def test_remove_booth_deallocates_voters_and_deletes_paired_devices(self):
        from accounts.models import Device, User
        from voters.models import Booth, ElectionVoter
        from voters.services import create_booth, enroll_voters_in_election, remove_booth

        enroll_voters_in_election(election_id=self.election.id, voter_ids=[self.voter.id])
        booth_res = create_booth(election_id=self.election.id)
        booth = booth_res['booth']
        officer_user_id = booth_res['officer_device'].user.id
        kiosk_user_id = booth_res['kiosk_device'].user.id

        ev = ElectionVoter.objects.get(election=self.election, voter=self.voter)
        ev.booth = booth
        ev.save()

        remove_booth(booth_id=booth.id)

        # Booth is deleted
        self.assertFalse(Booth.objects.filter(id=booth.id).exists())

        # Devices and users are deleted
        self.assertFalse(User.objects.filter(id=officer_user_id).exists())
        self.assertFalse(User.objects.filter(id=kiosk_user_id).exists())
        self.assertFalse(Device.objects.filter(identifier="booth-1-officer").exists())

        # Voter is still enrolled, but unallocated (booth=None)
        ev.refresh_from_db()
        self.assertIsNone(ev.booth)

    def test_rotate_station_credentials_even_when_active(self):
        from django.contrib.auth import authenticate
        from elections.services import create_candidate, create_position, start_election
        from voters.models import ElectionVoter
        from voters.services import create_booth, enroll_voters_in_election, rotate_booth_device_credentials

        pos = create_position(election_id=self.election.id, name="President", display_order=1)
        create_candidate(position_id=pos.id, name="Alice")
        enroll_voters_in_election(election_id=self.election.id, voter_ids=[self.voter.id])
        booth_res = create_booth(election_id=self.election.id)
        ElectionVoter.objects.filter(election=self.election).update(booth=booth_res['booth'])

        from accounts.services import create_device_session
        create_device_session(booth_res['officer_device'], "session-officer-rot")
        create_device_session(booth_res['kiosk_device'], "session-kiosk-rot")

        start_election(election_id=self.election.id)

        # Rotate officer device credentials
        officer_dev, new_pw = rotate_booth_device_credentials(device_id=booth_res['officer_device'].id)
        self.assertEqual(officer_dev.credential_version, 2)

        # Old password fails
        self.assertIsNone(authenticate(username=officer_dev.identifier, password=booth_res['officer_password']))

        # New password succeeds
        self.assertIsNotNone(authenticate(username=officer_dev.identifier, password=new_pw))

    def test_allocate_voters_to_booth_and_freeze(self):
        """Allocate voters to a booth by voter_ids and academic_group, and enforce freeze when active."""
        from elections.services import create_candidate, create_position, start_election
        from voters.models import AcademicGroup, ElectionVoter
        from voters.services import (
            allocate_voters_to_booth,
            auto_distribute_unallocated_voters,
            create_booth,
            deallocate_voters_from_booth,
            enroll_voters_in_election,
        )

        grp1 = AcademicGroup.objects.create(name="Biology")
        grp2 = AcademicGroup.objects.create(name="Physics")
        v1 = create_voter(primary_registry_value="BIO1", name="Bio Voter 1", academic_group_id=grp1.id)
        v2 = create_voter(primary_registry_value="BIO2", name="Bio Voter 2", academic_group_id=grp1.id)
        v3 = create_voter(primary_registry_value="PHY1", name="Phy Voter 1", academic_group_id=grp2.id)
        v4 = create_voter(primary_registry_value="PHY2", name="Phy Voter 2", academic_group_id=grp2.id)

        enroll_voters_in_election(election_id=self.election.id, voter_ids=[v1.id, v2.id, v3.id, v4.id])
        booth1_res = create_booth(election_id=self.election.id, name="Hall A")
        booth2_res = create_booth(election_id=self.election.id, name="Hall B")
        b1 = booth1_res["booth"]
        b2 = booth2_res["booth"]

        # Allocate by academic group
        count = allocate_voters_to_booth(election_id=self.election.id, booth_id=b1.id, academic_group_id=grp1.id)
        self.assertEqual(count, 2)
        self.assertEqual(ElectionVoter.objects.filter(election=self.election, booth=b1).count(), 2)

        # Allocate remaining by voter_ids
        count2 = allocate_voters_to_booth(election_id=self.election.id, booth_id=b2.id, voter_ids=[v3.id, v4.id])
        self.assertEqual(count2, 2)
        self.assertEqual(ElectionVoter.objects.filter(election=self.election, booth=b2).count(), 2)

        # Deallocate
        deallocated = deallocate_voters_from_booth(election_id=self.election.id, voter_ids=[v3.id])
        self.assertEqual(deallocated, 1)
        self.assertEqual(ElectionVoter.objects.filter(election=self.election, booth__isnull=True).count(), 1)

        # Auto-distribute unallocated
        dist_res = auto_distribute_unallocated_voters(election_id=self.election.id)
        self.assertEqual(dist_res["distributed"], 1)
        self.assertEqual(ElectionVoter.objects.filter(election=self.election, booth__isnull=True).count(), 0)

        # Setup stations and start election
        pos = create_position(election_id=self.election.id, name="President", display_order=1)
        create_candidate(position_id=pos.id, name="Candidate X")
        from accounts.services import create_device_session
        create_device_session(booth1_res["officer_device"], "s-off-b1")
        create_device_session(booth1_res["kiosk_device"], "s-kio-b1")
        create_device_session(booth2_res["officer_device"], "s-off-b2")
        create_device_session(booth2_res["kiosk_device"], "s-kio-b2")

        start_election(election_id=self.election.id)

        # Allocation changes now rejected due to configuration freeze
        with self.assertRaises(ValidationError):
            allocate_voters_to_booth(election_id=self.election.id, booth_id=b1.id, voter_ids=[v3.id])


class VoterAutoCategorizationImportTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="test_admin",
            password="adminpassword123",
            role=Role.ADMIN,
        )
        self.client = Client()

    def test_csv_with_group_and_subgroup_columns_creates_hierarchy(self):
        """CSV with group and subgroup columns automatically creates 2-level AcademicGroup hierarchy."""
        headers = ["student_id", "name", "department", "batch", "gender"]
        data_rows = [
            ["CS01", "John Doe", "Computer Science", "Batch A", "Male"],
            ["CS02", "Jane Smith", "Computer Science", "Batch B", "Female"],
            ["EC01", "Alex Brown", "Electronics", "Batch A", "Male"],
        ]

        result = process_voter_import(
            headers=headers,
            data_rows=data_rows,
            dry_run=False,
        )

        self.assertEqual(result.created_count, 3)
        self.assertEqual(result.unique_groups_count, 2)
        self.assertEqual(result.unique_subgroups_count, 3)

        # Verify AcademicGroup hierarchy
        top_groups = AcademicGroup.objects.filter(type=AcademicGroupType.GROUP)
        self.assertEqual(top_groups.count(), 2)
        self.assertTrue(top_groups.filter(name="Computer Science").exists())
        self.assertTrue(top_groups.filter(name="Electronics").exists())

        cs = top_groups.get(name="Computer Science")
        self.assertEqual(cs.children.count(), 2)

        voter_cs01 = Voter.objects.get(primary_registry_value="CS01")
        self.assertEqual(voter_cs01.academic_group.name, "Batch A")
        self.assertEqual(voter_cs01.academic_group.parent, cs)

    def test_csv_with_fallback_default_group_and_subgroup(self):
        """Voters without group columns are auto-categorized into specified default group & subgroup."""
        headers = ["roll_no", "student_name", "gender"]
        data_rows = [
            ["ME01", "Mike Tyson", "Male"],
            ["ME02", "Sara Connor", "Female"],
        ]

        result = process_voter_import(
            headers=headers,
            data_rows=data_rows,
            default_group="Mechanical Engineering",
            default_subgroup="Section 1",
            dry_run=False,
        )

        self.assertEqual(result.created_count, 2)
        voter = Voter.objects.get(primary_registry_value="ME01")
        self.assertIsNotNone(voter.academic_group)
        self.assertEqual(voter.academic_group.name, "Section 1")
        self.assertEqual(voter.academic_group.parent.name, "Mechanical Engineering")

    def test_csv_with_default_group_without_subgroup(self):
        """A registry can exist without subgroups: voters are assigned directly to the default group."""
        headers = ["roll_no", "student_name", "gender"]
        data_rows = [
            ["CV01", "Bob The Builder", "Male"],
            ["CV02", "Wendy Helper", "Female"],
        ]

        result = process_voter_import(
            headers=headers,
            data_rows=data_rows,
            default_group="Civil Engineering",
            default_subgroup="",
            dry_run=False,
        )

        self.assertEqual(result.created_count, 2)
        voter = Voter.objects.get(primary_registry_value="CV01")
        self.assertIsNotNone(voter.academic_group)
        self.assertEqual(voter.academic_group.name, "Civil Engineering")
        self.assertIsNone(voter.academic_group.parent)

    def test_import_view_requires_group_when_missing(self):
        """voter_import_view requires a group value when the uploaded file is missing a group column."""
        self.client.login(username="test_admin", password="adminpassword123")
        csv_content = b"roll_no,student_name,semester,gender\nNG01,No Group,S1,Male\n"
        csv_file = io.BytesIO(csv_content)
        csv_file.name = "no_group.csv"

        response = self.client.post(
            reverse("voters:import"),
            {
                "file": csv_file,
                "action": "commit",
                "default_group": "",
            },
        )
        # Should stay on page and not commit
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Voter.objects.filter(primary_registry_value="NG01").exists())

    def test_hierarchical_selectors_aggregate_voter_counts(self):
        """list_top_level_groups aggregates voter counts from all child subgroups."""
        from voters.selectors import list_top_level_groups, list_voters

        headers = ["student_id", "name", "department", "batch", "gender"]
        data_rows = [
            ["CS01", "John Doe", "Computer Science", "Batch A", "Male"],
            ["CS02", "Jane Smith", "Computer Science", "Batch B", "Female"],
            ["CS03", "Sam Lee", "Computer Science", "Batch B", "Male"],
        ]
        process_voter_import(headers=headers, data_rows=data_rows, dry_run=False)

        top_groups = list_top_level_groups()
        self.assertEqual(len(top_groups), 1)
        cs_group = top_groups[0]
        self.assertEqual(cs_group.voter_count, 3)
        self.assertEqual(len(cs_group.sample_voters), 3)

        # Filtering by top-level group returns voters in child subgroups
        voters_in_cs = list_voters(academic_group_id=cs_group.id)
        self.assertEqual(voters_in_cs.count(), 3)

    def test_import_view_preview_and_session_commit(self):
        """Admin can upload file for preview and commit atomically via session."""
        self.client.login(username="test_admin", password="adminpassword123")
        csv_content = b"student_id,name,department,batch,gender\nSTU10,Alice Wonderland,Arts,Literature,Female\n"
        csv_file = io.BytesIO(csv_content)
        csv_file.name = "students.csv"

        # 1. Preview
        response = self.client.post(
            reverse("voters:import"),
            {"file": csv_file, "action": "preview"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("pending_import", self.client.session)

        # 2. Commit
        response_commit = self.client.post(
            reverse("voters:import"),
            {"action": "commit"},
        )
        self.assertEqual(response_commit.status_code, 302)
        self.assertRedirects(response_commit, reverse("voters:registry"))

        # Verify voter created
        voter = Voter.objects.get(primary_registry_value="STU10")
        self.assertEqual(voter.name, "Alice Wonderland")
        self.assertEqual(voter.academic_group.name, "Literature")
        self.assertEqual(voter.academic_group.parent.name, "Arts")

    def test_list_top_level_groups_dot_color_and_subgroup_rosters(self):
        """Top level groups include dot colors, direct voters, and full subgroup voter rosters."""
        cs = create_academic_group(name="Computer Science")
        batch_a = create_academic_group(name="Batch A", parent_id=cs.id)
        
        # Direct voter under CS
        create_voter(primary_registry_value="CS_DIR_01", name="Direct Voter", academic_group_id=cs.id)
        # Subgroup voter under Batch A
        create_voter(primary_registry_value="CS_SUB_01", name="Sub Voter", academic_group_id=batch_a.id)

        groups = list_top_level_groups()
        cs_group = next(g for g in groups if g.id == cs.id)

        self.assertTrue(hasattr(cs_group, "dot_color"))
        self.assertTrue(cs_group.dot_color.startswith("#"))
        self.assertEqual(len(cs_group.direct_voters), 1)
        self.assertEqual(cs_group.direct_voters[0].name, "Direct Voter")

        self.assertEqual(len(cs_group.subgroups_list), 1)
        sub = cs_group.subgroups_list[0]
        self.assertEqual(len(sub.all_voters), 1)
        self.assertEqual(sub.all_voters[0].name, "Sub Voter")

    def test_individual_list_import_workflow(self):
        """Individual list import assigns all voters to a typed group name."""
        self.client.login(username="test_admin", password="adminpassword123")
        csv_content = b"roll_no,student_name,section,gender\nIND01,Mark Taylor,Section 1,Male\nIND02,Lucy Brown,Section 2,Female\n"
        csv_file = io.BytesIO(csv_content)
        csv_file.name = "individual_cohort.csv"

        response = self.client.post(
            reverse("voters:import"),
            {
                "file": csv_file,
                "action": "preview",
                "list_type": "individual",
                "custom_group_name": "Mechanical Engineering",
                "subgroup_mode": "column",
                "subgroup_col": "section",
                "id_col": "roll_no",
                "name_col": "student_name",
                "gender_col": "gender",
            }
        )
        self.assertEqual(response.status_code, 200)

        # Commit
        response_commit = self.client.post(
            reverse("voters:import"),
            {"action": "commit"}
        )
        self.assertEqual(response_commit.status_code, 302)

        v1 = Voter.objects.get(primary_registry_value="IND01")
        self.assertEqual(v1.name, "Mark Taylor")
        self.assertEqual(v1.academic_group.name, "Section 1")
        self.assertEqual(v1.academic_group.parent.name, "Mechanical Engineering")

    def test_import_parse_ajax_endpoint(self):
        """AJAX endpoint parses spreadsheet and returns JSON headers and detected aliases."""
        self.client.login(username="test_admin", password="adminpassword123")
        csv_content = b"student_id,name,department,batch,gender\n101,A,CS,A1,M\n"
        csv_file = io.BytesIO(csv_content)
        csv_file.name = "roster.csv"

        response = self.client.post(
            reverse("voters:import_parse"),
            {"file": csv_file}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertIn("headers", data)
        self.assertEqual(data["detected"]["group"], "department")
        self.assertEqual(data["detected"]["subgroup"], "batch")
        self.assertEqual(data["detected"]["primary_id"], "student_id")

    def test_registry_view_top_right_actions_and_empty_group_state(self):
        """Registry view renders Add group in top right, removes Export, and shows Import button in empty group."""
        self.client.login(username="test_admin", password="adminpassword123")
        empty_grp = create_academic_group(name="Empty Department")

        response = self.client.get(reverse("voters:registry"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        # Top right action buttons: Add group removed, Export removed, Import present
        self.assertNotIn("Add group", content)
        self.assertIn("Import", content)
        self.assertNotIn("<span>Export</span>", content)
        self.assertNotIn("Edit group", content)
        # Uses domain keyword (Department) instead of technical term 'group'
        self.assertTrue("Delete Department" in content or "delete department" in content.lower())

        # Empty group shows import action in its body
        self.assertIn("Import voters", content)
        self.assertTrue("default_group=Empty%20Department" in content or "default_group=Empty+Department" in content)

    def test_registry_view_sort_and_columns(self):
        """Registry view sorts by count and time, and displays columns without ROLL NO."""
        self.client.login(username="test_admin", password="adminpassword123")
        grp_a = create_academic_group(name="Alpha Group")
        grp_b = create_academic_group(name="Beta Group")
        create_voter(primary_registry_value="V01", name="Voter One", academic_group_id=grp_b.id)
        create_voter(primary_registry_value="V02", name="Voter Two", academic_group_id=grp_b.id)
        create_voter(primary_registry_value="V03", name="Voter Three", academic_group_id=grp_a.id)

        # Test sort by count (grp_b has 2 voters, grp_a has 1 voter)
        response_count = self.client.get(reverse("voters:registry") + "?sort=count")
        self.assertEqual(response_count.status_code, 200)
        groups = response_count.context["top_level_groups"]
        self.assertEqual(groups[0].id, grp_b.id)

        # Test sort by time (newest group first)
        response_time = self.client.get(reverse("voters:registry") + "?sort=time")
        self.assertEqual(response_time.status_code, 200)
        groups_time = response_time.context["top_level_groups"]
        self.assertEqual(groups_time[0].id, grp_b.id)

        content = response_count.content.decode("utf-8")
        self.assertNotIn("<th>ROLL NO</th>", content)
        # Avoid technical terms like IDENTIFIER and SUB GROUP, show domain keywords
        self.assertNotIn("<th>IDENTIFIER</th>", content)
        self.assertNotIn("<th>SUB GROUP</th>", content)
        self.assertIn("<th>STUDENT ID</th>", content)
        self.assertIn("<th>SEMESTER</th>", content)
        self.assertIn("table-select-all", content)
        self.assertIn("Sort by count", content)

        # Test search view includes back button
        response_search = self.client.get(reverse("voters:registry") + "?q=Voter")
        self.assertEqual(response_search.status_code, 200)
        self.assertTrue("Back to" in response_search.content.decode("utf-8"))
        self.assertIn("department", response_search.content.decode("utf-8").lower())

    def test_voter_bulk_delete_view_success(self):
        """Bulk delete removes selected voters from registry."""
        self.client.login(username="test_admin", password="adminpassword123")
        v1 = create_voter(primary_registry_value="BD01", name="Bulk Delete 1")
        v2 = create_voter(primary_registry_value="BD02", name="Bulk Delete 2")

        response = self.client.post(
            reverse("voters:bulk_delete"),
            {"voter_ids": f"{v1.id},{v2.id}"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("voters:registry"))
        self.assertFalse(Voter.objects.filter(id__in=[v1.id, v2.id]).exists())

    def test_voter_bulk_delete_view_already_voted_preserved(self):
        """Voters who already voted cannot be deleted during bulk delete."""
        self.client.login(username="test_admin", password="adminpassword123")
        now = timezone.now()
        election = create_election(
            name="Bulk Delete Election",
            starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(hours=8),
        )
        voter = create_voter(primary_registry_value="BD03", name="Voted Voter")
        ElectionVoter.objects.create(election=election, voter=voter, has_voted=True)

        response = self.client.post(
            reverse("voters:bulk_delete"),
            {"voter_ids": str(voter.id)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Voter.objects.filter(id=voter.id).exists())

    def test_group_delete_view_deletes_all_voter_rows(self):
        """Deleting a group deletes all voter rows where column=group."""
        self.client.login(username="test_admin", password="adminpassword123")
        grp = create_academic_group(name="Robotics")
        v1 = create_voter(primary_registry_value="ROB01", name="Robot One", academic_group_id=grp.id)
        v2 = create_voter(primary_registry_value="ROB02", name="Robot Two", academic_group_id=grp.id)

        response = self.client.post(reverse("voters:group_delete", args=[grp.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Voter.objects.filter(id__in=[v1.id, v2.id]).exists())
        self.assertFalse(AcademicGroup.objects.filter(id=grp.id).exists())


class MultipleVoterRegistriesTests(TestCase):
    """Tests for Multiple Voter Registries architecture (Step 1)."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="registry_admin",
            password="adminpassword123",
            role=Role.ADMIN,
        )
        self.officer = User.objects.create_user(
            username="registry_officer",
            password="officerpassword123",
            role=Role.OFFICER,
        )

    def test_create_multiple_registries_with_different_schemas(self):
        """Electra supports multiple independent voter registries each with fixed schema."""
        from voters.models import VoterRegistry
        from voters.services import create_voter_registry

        reg_a = create_voter_registry(
            name="College Students Registry",
            primary_id_source="Student ID",
            name_source="Name",
            group_source="Department",
            subgroup_source="Semester",
            gender_source="Gender",
        )
        reg_b = create_voter_registry(
            name="Faculty & Staff",
            primary_id_source="Employee ID",
            name_source="Name",
            group_source="Department",
            subgroup_source="Designation",
            gender_source="Gender",
        )
        reg_c = create_voter_registry(
            name="Alumni Network",
            primary_id_source="Alumni ID",
            name_source="Name",
            group_source="Department",
            subgroup_source="Passout Year",
            gender_source="Gender",
        )

        self.assertEqual(reg_a.schema_display, "Name | Student ID | Department | Semester | Gender")
        self.assertEqual(reg_b.schema_display, "Name | Employee ID | Department | Designation | Gender")
        self.assertEqual(reg_c.schema_display, "Name | Alumni ID | Department | Passout Year | Gender")

        # Duplicate registry name rejected
        with self.assertRaises(ValidationError):
            create_voter_registry(name="College Students Registry")

    def test_primary_identifier_scoped_to_registry(self):
        """Primary identifier uniqueness is scoped to the specific registry."""
        from voters.services import create_voter_registry

        reg_a = create_voter_registry(
            name="Students Reg",
            primary_id_source="Student ID",
        )
        reg_b = create_voter_registry(
            name="Staff Reg",
            primary_id_source="Employee ID",
        )

        # Same primary identifier in DIFFERENT registries is allowed
        v_a = create_voter(primary_registry_value="ID100", name="Alice", registry_id=reg_a.id)
        v_b = create_voter(primary_registry_value="ID100", name="Bob", registry_id=reg_b.id)

        self.assertEqual(v_a.primary_registry_value, "ID100")
        self.assertEqual(v_b.primary_registry_value, "ID100")
        self.assertNotEqual(v_a.registry_id, v_b.registry_id)

        # Duplicate primary identifier within the SAME registry is rejected
        with self.assertRaises(ValidationError):
            create_voter(primary_registry_value="ID100", name="Charlie", registry_id=reg_a.id)

    def test_registries_home_page_view(self):
        """Registries home page renders all registries and their schema mappings."""
        from voters.services import create_voter_registry

        create_voter_registry(
            name="CET Students Test",
            primary_id_source="Student ID",
            group_source="Department",
            subgroup_source="Semester",
        )
        create_voter_registry(
            name="Faculty & Staff Test",
            primary_id_source="Employee ID",
            group_source="Department",
            subgroup_source="Designation",
        )

        self.client.login(username="registry_admin", password="adminpassword123")
        response = self.client.get(reverse("voters:registries"))
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Voter registries")
        self.assertContains(response, "CET Students Test")
        self.assertContains(response, "Faculty &amp; Staff Test")
        self.assertContains(response, "Student ID")
        self.assertContains(response, "Employee ID")
        self.assertContains(response, "Semester")
        self.assertContains(response, "Designation")

    def test_registries_home_officer_forbidden(self):
        """Officers cannot access the voter registries home page."""
        self.client.login(username="registry_officer", password="officerpassword123")
        response = self.client.get(reverse("voters:registries"))
        self.assertEqual(response.status_code, 403)

    def test_election_voter_registry_relationship(self):
        """An Election selects exactly one VoterRegistry."""
        from voters.services import create_voter_registry

        reg = create_voter_registry(name="Election Registry")
        now = timezone.now()
        election = create_election(
            name="Student Council 2026",
            starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(hours=8),
        )
        election.voter_registry = reg
        election.save()

        election.refresh_from_db()
        self.assertEqual(election.voter_registry, reg)
        self.assertIn(election, reg.elections.all())

    def test_voter_import_parse_view_returns_preview_rows_and_detected_aliases(self):
        """Parse view returns preview_rows and auto-detected educational mappings."""
        self.client.login(username="registry_admin", password="adminpassword123")

        csv_content = (
            "student_id,full_name,department,semester,gender\n"
            "CS2023-001,Aarav Nair,Computer Science,S3,Male\n"
            "CS2023-002,Diya Menon,Computer Science,S3,Female\n"
        )
        file_obj = io.BytesIO(csv_content.encode("utf-8"))
        file_obj.name = "cet_students.csv"

        response = self.client.post(reverse("voters:import_parse"), {"file": file_obj})
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["total_rows"], 2)
        self.assertEqual(len(data["preview_rows"]), 2)
        self.assertEqual(data["preview_rows"][0][0], "CS2023-001")
        self.assertEqual(data["detected"]["primary_id"], "student_id")
        self.assertEqual(data["detected"]["name"], "full_name")
        self.assertEqual(data["detected"]["group"], "department")
        self.assertEqual(data["detected"]["subgroup"], "semester")
        self.assertEqual(data["detected"]["gender"], "gender")

    def test_registry_create_view_get(self):
        """GET registry_create renders initial setup page."""
        self.client.login(username="registry_admin", password="adminpassword123")
        response = self.client.get(reverse("voters:registry_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Initial import configuration")
        self.assertContains(response, "Create voter registry")
        self.assertContains(response, "Registry details")
        self.assertContains(response, "Map columns from file")
        self.assertContains(response, "File preview")

    def test_registry_create_view_post_atomic_creation_and_import(self):
        """POST registry_create creates VoterRegistry and imports voters with AcademicGroup mapping atomically."""
        self.client.login(username="registry_admin", password="adminpassword123")

        csv_content = (
            "student_id,name,department,semester,gender\n"
            "CS2023-001,Aarav Nair,Computer Science,S3,Male\n"
            "CS2023-002,Diya Menon,Computer Science,S3,Female\n"
            "EC2023-015,Arjun Suresh,Electronics,S3,Male\n"
        )
        file_obj = io.BytesIO(csv_content.encode("utf-8"))
        file_obj.name = "cet_students.csv"

        response = self.client.post(reverse("voters:registry_create"), {
            "name": "CET 2026 Batch",
            "primary_id_source": "student_id",
            "name_source": "name",
            "group_source": "department",
            "subgroup_source": "semester",
            "gender_source": "gender",
            "file": file_obj,
        })

        reg = VoterRegistry.objects.filter(name="CET 2026 Batch").first()
        self.assertIsNotNone(reg)
        self.assertRedirects(response, reverse("voters:registry_detail", kwargs={"registry_id": reg.id}))

        # Verify registry schema configuration
        self.assertEqual(reg.primary_id_source, "student_id")
        self.assertEqual(reg.name_source, "name")
        self.assertEqual(reg.group_source, "department")
        self.assertEqual(reg.subgroup_source, "semester")
        self.assertEqual(reg.gender_source, "gender")

        # Verify voters imported and scoped to registry
        voters = Voter.objects.filter(registry=reg)
        self.assertEqual(voters.count(), 3)
        aarav = voters.get(primary_registry_value="CS2023-001")
        self.assertEqual(aarav.name, "Aarav Nair")
        self.assertEqual(aarav.gender, "Male")

        # Verify AcademicGroup hierarchy: Group (Level 1) and Subgroup (Level 2) mapped to registry
        cs_group = AcademicGroup.objects.get(registry=reg, name="Computer Science", parent=None)
        self.assertEqual(cs_group.type, AcademicGroupType.GROUP)

        s3_subgroup = AcademicGroup.objects.get(registry=reg, name="S3", parent=cs_group)
        self.assertEqual(s3_subgroup.type, AcademicGroupType.SUBGROUP)

        # Leaf assignment: aarav belongs to s3_subgroup
        self.assertEqual(aarav.academic_group, s3_subgroup)

    def test_delete_voter_registry_service(self):
        """delete_voter_registry deletes registry and cascades, but blocks if linked to elections or votes."""
        from voters.services import delete_voter_registry, create_voter_registry
        reg = create_voter_registry(name="Disposable Registry")
        grp = AcademicGroup.objects.create(registry=reg, name="Arts", type=AcademicGroupType.GROUP)
        voter = Voter.objects.create(registry=reg, primary_registry_value="ART-01", name="Art Student", academic_group=grp)

        # Deleting unlinked registry succeeds
        delete_voter_registry(registry_id=reg.id)
        self.assertFalse(VoterRegistry.objects.filter(id=reg.id).exists())
        self.assertFalse(AcademicGroup.objects.filter(id=grp.id).exists())
        self.assertFalse(Voter.objects.filter(id=voter.id).exists())

        # Cannot delete registry linked to started election
        reg2 = create_voter_registry(name="Protected Registry")
        election = Election.objects.create(name="Campus Vote", voter_registry=reg2, status=ElectionStatus.ACTIVE)
        with self.assertRaises(ValidationError) as ctx:
            delete_voter_registry(registry_id=reg2.id)
        self.assertIn("linked to started election(s)", str(ctx.exception))
        self.assertTrue(VoterRegistry.objects.filter(id=reg2.id).exists())

    def test_registry_delete_view_post(self):
        """Admin can POST to delete a registry."""
        self.client.login(username="registry_admin", password="adminpassword123")
        from voters.services import create_voter_registry
        reg = create_voter_registry(name="To Be Deleted")

        response = self.client.post(reverse("voters:registry_delete", kwargs={"registry_id": reg.id}))
        self.assertRedirects(response, reverse("voters:registries"))
        self.assertFalse(VoterRegistry.objects.filter(id=reg.id).exists())

    def test_subsequent_import_into_registry_with_missing_fields(self):
        """Subsequent import into an existing registry automatically uses schema and applies global defaults when fields are omitted."""
        self.client.login(username="registry_admin", password="adminpassword123")
        from voters.services import create_voter_registry
        reg = create_voter_registry(
            name="MCA Registry",
            primary_id_source="student_id",
            name_source="name",
            group_source="department",
            subgroup_source="semester",
            gender_source="gender",
        )

        # CSV without department column (subgroup is present; group is manually entered)
        csv_content = (
            "student_id,name,semester,gender\n"
            "MCA23-001,Aarav Nair,S3,Male\n"
            "MCA23-002,Diya Menon,S3,Female\n"
        )
        file_obj = io.BytesIO(csv_content.encode("utf-8"))
        file_obj.name = "mca_students.csv"

        response = self.client.post(
            reverse("voters:registry_import", kwargs={"registry_id": reg.id}),
            {
                "file": file_obj,
                "action": "commit",
                "default_group": "MCA",
            }
        )
        self.assertRedirects(response, reverse("voters:registry_detail", kwargs={"registry_id": reg.id}))

        # Verify voters were imported with global values applied
        v1 = Voter.objects.get(registry=reg, primary_registry_value="MCA23-001")
        self.assertEqual(v1.name, "Aarav Nair")
        self.assertEqual(v1.gender, "Male")
        self.assertEqual(v1.academic_group.name, "S3")
        self.assertEqual(v1.academic_group.parent.name, "MCA")


class VoterRegistryElectionLockAndImportEnforcementTests(TestCase):
    """Verifies that registries linked to elections are locked against any changes,
    and subsequent imports strictly enforce required columns except group."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_user",
            password="adminpassword123",
            role=Role.ADMIN,
        )
        self.client = Client()
        self.client.force_login(self.admin)

        self.registry = create_voter_registry(
            name="Engineering Registry",
            primary_id_source="Roll Number",
            name_source="Student Name",
            group_source="Branch",
            subgroup_source="Semester",
            gender_source="Gender",
        )
        self.group = create_academic_group(name="Computer Science", registry=self.registry)
        self.subgroup = create_academic_group(name="S4", parent_id=self.group.id, registry=self.registry)
        self.voter = create_voter(
            primary_registry_value="CS001",
            name="John Doe",
            academic_group_id=self.subgroup.id,
            registry=self.registry,
            gender="Male",
        )

    def test_cannot_add_or_modify_or_delete_contents_when_registry_is_linked_to_election(self):
        """Once linked to an election, registry cannot add/edit/delete voters or groups, or import."""
        from django.utils import timezone
        import datetime
        now = timezone.now()
        Election.objects.create(
            name="College Election 2026",
            voter_registry=self.registry,
            starts_at=now + datetime.timedelta(days=1),
            ends_at=now + datetime.timedelta(days=2),
            status=ElectionStatus.ACTIVE,
        )

        # 1. Cannot add voter
        with self.assertRaises(ValidationError) as ctx:
            create_voter(
                primary_registry_value="CS002",
                name="Jane Smith",
                academic_group_id=self.subgroup.id,
                registry=self.registry,
            )
        self.assertIn("linked to one or more elections", str(ctx.exception))

        # 2. Cannot edit voter
        with self.assertRaises(ValidationError) as ctx:
            update_voter(voter_id=self.voter.id, name="Johnny Doe")
        self.assertIn("linked to one or more elections", str(ctx.exception))

        # 3. Cannot delete voter
        with self.assertRaises(ValidationError) as ctx:
            delete_voter(voter_id=self.voter.id)
        self.assertIn("linked to one or more elections", str(ctx.exception))

        # 4. Cannot create group
        with self.assertRaises(ValidationError) as ctx:
            create_academic_group(name="Mechanical", registry=self.registry)
        self.assertIn("linked to one or more elections", str(ctx.exception))

        # 5. Cannot update group
        with self.assertRaises(ValidationError) as ctx:
            update_academic_group(group_id=self.group.id, name="CompSci")
        self.assertIn("linked to one or more elections", str(ctx.exception))

        # 6. Cannot delete group
        with self.assertRaises(ValidationError) as ctx:
            delete_academic_group(group_id=self.group.id)
        self.assertIn("linked to one or more elections", str(ctx.exception))

        # 7. Cannot import voters
        with self.assertRaises(ValidationError) as ctx:
            process_voter_import(
                headers=["Roll Number", "Student Name", "Branch", "Semester"],
                data_rows=[["CS003", "Alice Bob", "Computer Science", "S4"]],
                registry=self.registry,
                dry_run=False,
            )
        self.assertIn("linked to one or more elections", str(ctx.exception))

    def test_import_rejects_missing_required_columns_except_group(self):
        """Only missing group name can be manually typed; missing roll number, name, or semester fails."""
        # Missing Roll Number
        with self.assertRaises(ValidationError) as ctx:
            process_voter_import(
                headers=["Student Name", "Branch", "Semester"],
                data_rows=[["Alice Bob", "Computer Science", "S4"]],
                registry=self.registry,
            )
        self.assertIn("missing the required 'Roll Number' column", str(ctx.exception))

        # Missing Name
        with self.assertRaises(ValidationError) as ctx:
            process_voter_import(
                headers=["Roll Number", "Branch", "Semester"],
                data_rows=[["CS003", "Computer Science", "S4"]],
                registry=self.registry,
            )
        self.assertIn("missing the required 'Student Name' column", str(ctx.exception))

        # Missing Semester (subgroup configured on registry)
        with self.assertRaises(ValidationError) as ctx:
            process_voter_import(
                headers=["Roll Number", "Student Name", "Branch"],
                data_rows=[["CS003", "Alice Bob", "Computer Science"]],
                registry=self.registry,
            )
        self.assertIn("missing the required 'Semester' column", str(ctx.exception))

        # Missing Branch (group) IS PERMITTED when default_group is provided
        res = process_voter_import(
            headers=["Roll Number", "Student Name", "Semester"],
            data_rows=[["CS003", "Alice Bob", "S4"]],
            registry=self.registry,
            default_group="Computer Science",
            dry_run=False,
        )
        self.assertEqual(res.created_count, 1)
        v = Voter.objects.get(registry=self.registry, primary_registry_value="CS003")
        self.assertEqual(v.academic_group.name, "S4")
        self.assertEqual(v.academic_group.parent.name, "Computer Science")

    def test_voter_import_parse_view_enforces_columns(self):
        """Parse view rejects missing primary ID / name / subgroup, and identifies missing group."""
        # 1. Missing primary ID
        csv_no_id = "Student Name,Branch,Semester\nAlice,CS,S4\n"
        f = io.BytesIO(csv_no_id.encode("utf-8"))
        f.name = "test.csv"
        resp = self.client.post(
            reverse("voters:import_parse"),
            {"file": f, "registry_id": self.registry.id},
        )
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("missing the required 'Roll Number' column", data["error"])

        # 2. Missing Semester (Subgroup)
        csv_no_sub = "Roll Number,Student Name,Branch\nCS004,Bob,CS\n"
        f2 = io.BytesIO(csv_no_sub.encode("utf-8"))
        f2.name = "test.csv"
        resp2 = self.client.post(
            reverse("voters:import_parse"),
            {"file": f2, "registry_id": self.registry.id},
        )
        data2 = resp2.json()
        self.assertFalse(data2["success"])
        self.assertIn("missing the required 'Semester' column", data2["error"])

        # 3. Missing Branch (Group) succeeds and flags missing_group = True
        csv_no_grp = "Roll Number,Student Name,Semester\nCS005,Charlie,S4\n"
        f3 = io.BytesIO(csv_no_grp.encode("utf-8"))
        f3.name = "test.csv"
        resp3 = self.client.post(
            reverse("voters:import_parse"),
            {"file": f3, "registry_id": self.registry.id},
        )
        data3 = resp3.json()
        self.assertTrue(data3["success"])
        self.assertTrue(data3["missing_group"])

    def test_registry_create_view_starts_fresh_on_get(self):
        """Each registry creation starts with a clean slate, removing stale session files."""
        session = self.client.session
        session["pending_file"] = {"filename": "old.csv"}
        session["pending_import"] = {"filename": "old.csv"}
        session.save()

        resp = self.client.get(reverse("voters:registry_create"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("pending_file", self.client.session)
        self.assertNotIn("pending_import", self.client.session)

    def test_registry_create_subgroup_optional_allows_creation_without_subgroup(self):
        """Sub-group is optional during initial registry configuration; registry functions without subgroups."""
        csv_content = "Roll No,Full Name,Department\nME001,John Doe,Mechanical\nME002,Jane Smith,Mechanical\n"
        f = io.BytesIO(csv_content.encode("utf-8"))
        f.name = "mech_students.csv"

        resp = self.client.post(
            reverse("voters:registry_create"),
            {
                "file": f,
                "name": "Mechanical Department",
                "primary_id_source": "Roll No",
                "name_source": "Full Name",
                "group_source": "Department",
                "has_subgroups_toggle": "no",
                "subgroup_source": "",
                "gender_source": "(None)",
            },
        )
        reg = VoterRegistry.objects.get(name="Mechanical Department")
        self.assertFalse(reg.has_subgroups)
        self.assertEqual(reg.subgroup_source, "")
        self.assertEqual(Voter.objects.filter(registry=reg).count(), 2)

        voter = Voter.objects.get(registry=reg, primary_registry_value="ME001")
        self.assertIsNone(voter.academic_group.parent)
        self.assertEqual(voter.academic_group.name, "Mechanical")

    def test_registry_create_error_retains_file_info_and_preview(self):
        """When an error occurs during initial creation, file info, preview, and choices are preserved."""
        # 1. Setup session pending_file with duplicate Voter IDs
        headers = ["Roll Number", "Student Name", "Branch"]
        data_rows = [
            ["CS001", "Aarav", "Computer Science"],
            ["CS001", "Duplicate Aarav", "Computer Science"],
        ]
        session = self.client.session
        session["pending_file"] = {
            "headers": headers,
            "data_rows": data_rows,
            "filename": "students_dup.csv",
            "filesize": "1.2 KB",
        }
        session.save()

        # 2. Post with duplicate voter ID
        resp = self.client.post(
            reverse("voters:registry_create"),
            {
                "name": "New Registry",
                "primary_id_source": "Roll Number",
                "name_source": "Student Name",
                "group_source": "Branch",
                "subgroup_source": "",
            },
        )
        self.assertEqual(resp.status_code, 200)

        # 3. Assert error message is in simple English
        messages_list = list(resp.context["messages"])
        self.assertTrue(len(messages_list) > 0)
        err_text = str(messages_list[0])
        self.assertIn("duplicate values", err_text.lower())
        self.assertIn("CS001", err_text)
        self.assertIn("unique Voter ID", err_text)

        # 4. Assert page retained pending_file, initial_parse_data with preview, and typed name
        self.assertIsNotNone(resp.context.get("pending_file"))
        self.assertEqual(resp.context["pending_file"]["filename"], "students_dup.csv")

        initial_parse_data = resp.context.get("initial_parse_data")
        self.assertIsNotNone(initial_parse_data)
        self.assertEqual(initial_parse_data["filename"], "students_dup.csv")
        self.assertEqual(len(initial_parse_data["preview_rows"]), 2)
        self.assertEqual(initial_parse_data["detected"]["primary_id"], "Roll Number")
        self.assertEqual(initial_parse_data["detected"]["name"], "Student Name")

        self.assertEqual(resp.context.get("name"), "New Registry")










