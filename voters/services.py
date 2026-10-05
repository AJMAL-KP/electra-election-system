"""Domain services for voters application.

Owns:
- Voter registry CRUD operations
- AcademicGroup hierarchy management
- Voter enrollment into elections with configuration freeze checks
- Duplicate registry prevention
"""
from typing import List, Optional, Tuple
from django.core.exceptions import ValidationError
from django.db import models, transaction

from elections.models import Election, ElectionStatus
from voters.models import AcademicGroup, AcademicGroupType, ElectionVoter, Voter, VoterRegistry


def create_voter_registry(
    *,
    name: str,
    primary_id_source: str = "Student ID",
    name_source: str = "Name",
    group_source: str = "Department",
    subgroup_source: str = "Semester",
    gender_source: str = "Gender",
    **extra,
) -> VoterRegistry:
    """Create a new independent voter registry with fixed schema mapping."""
    clean_name = name.strip()
    if not clean_name:
        raise ValidationError("Please enter a name for the voter registry.")

    if VoterRegistry.objects.filter(name__iexact=clean_name).exists():
        raise ValidationError(f"A voter registry named '{clean_name}' already exists. Please choose a different name.")

    clean_subgroup = subgroup_source.strip() if subgroup_source else ""
    if clean_subgroup.lower() in ["(none)", "none", "—", "-"]:
        clean_subgroup = ""

    registry = VoterRegistry(
        name=clean_name,
        primary_id_source=primary_id_source.strip() or "Primary Identifier",
        name_source=name_source.strip() or "Name",
        group_source=group_source.strip() or "Group",
        subgroup_source=clean_subgroup,
        gender_source=gender_source.strip() or "Gender",
    )
    registry.full_clean()
    registry.save()
    return registry


def delete_voter_registry(*, registry_id: int) -> None:
    """Delete a voter registry and all its groups and voters, enforcing election safety invariants.

    Cannot delete if:
    - Any election (draft, active, closed, results published) is linked to this registry.
    - Any voter in this registry has cast a ballot in any election.
    """
    with transaction.atomic():
        try:
            registry = VoterRegistry.objects.select_for_update().get(id=registry_id)
        except VoterRegistry.DoesNotExist:
            raise ValidationError(f"Voter registry #{registry_id} does not exist.")

        # Check if any started election is linked to this registry
        linked_elections = Election.objects.filter(voter_registry=registry).exclude(status=ElectionStatus.DRAFT)
        if linked_elections.exists():
            election_names = ", ".join(e.name for e in linked_elections[:3])
            more = f" and {linked_elections.count() - 3} more" if linked_elections.count() > 3 else ""
            raise ValidationError(
                f"Cannot delete registry '{registry.name}': it is linked to started election(s): {election_names}{more}."
            )

        # Invariant check: no cast ballots from voters in this registry
        if ElectionVoter.objects.filter(voter__registry=registry, has_voted=True).exists():
            raise ValidationError(
                f"Cannot delete registry '{registry.name}': voters in this registry have already cast ballots."
            )

        # Unlink any unstarted draft elections so they don't block deletion
        Election.objects.filter(voter_registry=registry, status=ElectionStatus.DRAFT).update(voter_registry=None)

        registry.delete()

        # If all registries have been deleted, sweep any dangling unlinked voters or groups
        if not VoterRegistry.objects.exists():
            Voter.objects.filter(registry__isnull=True).delete()
            AcademicGroup.objects.filter(registry__isnull=True).delete()


def create_voter(
    *,
    primary_registry_value: str,
    name: str,
    gender: str = "",
    academic_group_id: Optional[int] = None,
    registry_id: Optional[int] = None,
    registry: Optional[VoterRegistry] = None,
) -> Voter:
    """Create a new voter in a voter registry."""
    clean_id = primary_registry_value.strip()
    clean_name = name.strip()

    if not clean_id:
        raise ValidationError("Primary registry identifier is required.")
    if not clean_name:
        raise ValidationError("Voter full name is required.")

    group = None
    if academic_group_id:
        try:
            group = AcademicGroup.objects.get(id=academic_group_id)
        except AcademicGroup.DoesNotExist:
            raise ValidationError("Specified academic group does not exist.")

    if registry is None:
        if registry_id:
            try:
                registry = VoterRegistry.objects.get(id=registry_id)
            except VoterRegistry.DoesNotExist:
                raise ValidationError("Specified voter registry does not exist.")
        elif group and group.registry:
            registry = group.registry
        else:
            registry = VoterRegistry.objects.first()

    if registry and registry.is_locked:
        raise ValidationError("Cannot add voters: this registry is linked to one or more elections.")

    # Scope primary registry identifier uniqueness to this registry
    dup_query = Voter.objects.filter(primary_registry_value__iexact=clean_id)
    if registry:
        dup_query = dup_query.filter(registry=registry)
    if dup_query.exists():
        raise ValidationError(f"A voter with registry identifier '{clean_id}' already exists in this registry.")

    voter = Voter(
        registry=registry,
        primary_registry_value=clean_id,
        name=clean_name,
        gender=gender.strip(),
        academic_group=group,
    )
    voter.full_clean()
    voter.save()
    return voter


def update_voter(
    *,
    voter_id: int,
    name: str,
    gender: str = "",
    academic_group_id: Optional[int] = None,
) -> Voter:
    """Update details of an existing voter."""
    with transaction.atomic():
        try:
            voter = Voter.objects.select_for_update().get(id=voter_id)
        except Voter.DoesNotExist:
            raise ValidationError(f"Voter #{voter_id} does not exist.")

        if voter.registry and voter.registry.is_locked:
            raise ValidationError("Cannot modify voter: this registry is linked to one or more elections.")

        clean_name = name.strip()
        if not clean_name:
            raise ValidationError("Voter full name is required.")

        group = None
        if academic_group_id:
            try:
                group = AcademicGroup.objects.get(id=academic_group_id)
            except AcademicGroup.DoesNotExist:
                raise ValidationError("Specified academic group does not exist.")

        voter.name = clean_name
        voter.gender = gender.strip()
        voter.academic_group = group
        voter.full_clean()
        voter.save()
        return voter


def delete_voter(*, voter_id: int) -> None:
    """Delete a voter from the central registry, enforcing election linking and participation invariants."""
    with transaction.atomic():
        try:
            voter = Voter.objects.select_for_update().get(id=voter_id)
        except Voter.DoesNotExist:
            raise ValidationError(f"Voter #{voter_id} does not exist.")

        # Prevent deletion if voter belongs to a registry linked to active/completed election(s)
        if voter.registry and voter.registry.is_locked:
            linked_elections = voter.registry.active_elections
            election_names = ", ".join(e.name for e in linked_elections[:3])
            more = f" and {linked_elections.count() - 3} more" if linked_elections.count() > 3 else ""
            raise ValidationError(
                f"Cannot delete voter: this registry is linked to one or more elections ({election_names}{more}). "
                "Voters cannot be deleted while linked to an active or completed election."
            )

        # Prevent deletion if voter is enrolled in any election
        if voter.election_enrollments.exists():
            raise ValidationError("Cannot delete voter: this voter is enrolled in an election.")

        # Prevent deletion if voter has cast a ballot in any election
        if voter.election_enrollments.filter(has_voted=True).exists():
            raise ValidationError("Cannot delete voter: participation records indicate they have cast a ballot.")

        voter.delete()


def create_academic_group(
    *,
    name: str,
    parent_id: Optional[int] = None,
    registry_id: Optional[int] = None,
    registry: Optional[VoterRegistry] = None,
) -> AcademicGroup:
    """Create a Group or Sub Group in the academic hierarchy scoped to a registry.

    - If parent_id is None: creates a top-level GROUP.
    - If parent_id is provided: creates a SUBGROUP under the specified GROUP.
      The parent must itself be a GROUP (not a SUBGROUP).
    """
    clean_name = name.strip()
    if not clean_name:
        raise ValidationError("Academic group name is required.")

    parent = None
    if parent_id:
        try:
            parent = AcademicGroup.objects.get(id=parent_id)
        except AcademicGroup.DoesNotExist:
            raise ValidationError("Specified parent group does not exist.")
        if parent.type != AcademicGroupType.GROUP:
            raise ValidationError("Sub groups can only be created under a top-level Group.")

    group_type = AcademicGroupType.SUBGROUP if parent else AcademicGroupType.GROUP

    if registry is None:
        if parent and parent.registry:
            registry = parent.registry
        elif registry_id:
            try:
                registry = VoterRegistry.objects.get(id=registry_id)
            except VoterRegistry.DoesNotExist:
                raise ValidationError("Specified voter registry does not exist.")
        else:
            registry = VoterRegistry.objects.first()

    if registry and registry.is_locked:
        raise ValidationError("Cannot add groups: this registry is linked to one or more elections.")

    dup_query = AcademicGroup.objects.filter(name__iexact=clean_name, parent=parent)
    if registry:
        dup_query = dup_query.filter(registry=registry)
    if dup_query.exists():
        raise ValidationError(
            f"A {'sub group' if parent else 'group'} named '{clean_name}' already exists"
            + (f" under '{parent.name}'" if parent else " in this registry.")
        )

    group = AcademicGroup(
        registry=registry,
        name=clean_name,
        type=group_type,
        parent=parent,
    )
    group.full_clean()
    group.save()
    return group


def update_academic_group(
    *,
    group_id: int,
    name: str,
) -> AcademicGroup:
    """Rename an existing Group or Sub Group."""
    clean_name = name.strip()
    if not clean_name:
        raise ValidationError("Academic group name is required.")

    with transaction.atomic():
        try:
            group = AcademicGroup.objects.select_for_update().get(id=group_id)
        except AcademicGroup.DoesNotExist:
            raise ValidationError(f"Academic group #{group_id} does not exist.")

        if group.registry and group.registry.is_locked:
            raise ValidationError("Cannot rename group: this registry is linked to one or more elections.")

        if AcademicGroup.objects.filter(
            name__iexact=clean_name, parent=group.parent
        ).exclude(id=group_id).exists():
            raise ValidationError(
                f"A group named '{clean_name}' already exists under this parent."
            )

        group.name = clean_name
        group.full_clean()
        group.save()
        return group


def delete_academic_group(*, group_id: int) -> None:
    """Delete a Group or Sub Group.

    - A GROUP with voters assigned directly cannot be deleted.
    - A GROUP with Sub Groups that have voters cannot be deleted.
    - A SUBGROUP with voters assigned cannot be deleted.
    """
    with transaction.atomic():
        try:
            group = AcademicGroup.objects.select_for_update().get(id=group_id)
        except AcademicGroup.DoesNotExist:
            raise ValidationError(f"Academic group #{group_id} does not exist.")

        # Check if group belongs to a registry linked to active/completed elections
        if group.registry and group.registry.is_locked:
            raise ValidationError("Cannot delete group: this registry is linked to one or more elections.")

        # Check voters assigned directly to this group
        if Voter.objects.filter(academic_group=group).exists():
            raise ValidationError(
                "Cannot delete: voters are assigned to this group. Reassign them first."
            )

        # For a top-level GROUP, also check all children
        if group.type == AcademicGroupType.GROUP:
            child_ids = group.children.values_list('id', flat=True)
            if Voter.objects.filter(academic_group_id__in=child_ids).exists():
                raise ValidationError(
                    "Cannot delete: one or more sub groups still have voters. Remove them first."
                )

        group.delete()


def enroll_voters_in_election(
    *,
    election_id: int,
    voter_ids: List[int],
) -> int:
    """Enroll voters from the central registry into an election.
    
    Enforces configuration freeze: enrollment is permitted ONLY when election is in DRAFT.
    """
    with transaction.atomic():
        try:
            election = Election.objects.select_for_update().get(id=election_id)
        except Election.DoesNotExist:
            raise ValidationError(f"Election #{election_id} does not exist.")

        if not election.is_draft:
            raise ValidationError("Configuration is frozen. Voters cannot be enrolled once an election is activated.")

        # Filter out voters already enrolled
        existing_enrolled_ids = set(
            ElectionVoter.objects.filter(
                election=election,
                voter_id__in=voter_ids
            ).values_list('voter_id', flat=True)
        )

        to_create = []
        for vid in voter_ids:
            if vid not in existing_enrolled_ids:
                to_create.append(
                    ElectionVoter(
                        election=election,
                        voter_id=vid,
                        has_voted=False,
                    )
                )

        if to_create:
            ElectionVoter.objects.bulk_create(to_create)

        return len(to_create)


def remove_voter_from_election(
    *,
    election_id: int,
    voter_id: int,
) -> None:
    """Remove a voter from election enrollment. Permitted ONLY when election is in DRAFT."""
    with transaction.atomic():
        try:
            election = Election.objects.select_for_update().get(id=election_id)
        except Election.DoesNotExist:
            raise ValidationError(f"Election #{election_id} does not exist.")

        if not election.is_draft:
            raise ValidationError("Configuration is frozen. Enrolled voters cannot be removed once election is activated.")

        deleted_count, _ = ElectionVoter.objects.filter(election=election, voter_id=voter_id).delete()
        if deleted_count == 0:
            raise ValidationError("Voter is not currently enrolled in this election.")


def allocate_voters_to_booth(
    *,
    election_id: int,
    booth_id: int,
    voter_ids: Optional[List[int]] = None,
    academic_group_id: Optional[int] = None,
    allocate_all_unallocated: bool = False,
) -> int:
    """Allocate enrolled ElectionVoters to a specific booth.
    
    Enforces:
    - Election must be in DRAFT (configuration freeze invariant).
    - Booth must belong to this election.
    - Operates strictly on ElectionVoter.booth, never modifying central Voter records.
    """
    from voters.models import Booth

    with transaction.atomic():
        try:
            election = Election.objects.select_for_update().get(id=election_id)
        except Election.DoesNotExist:
            raise ValidationError(f"Election #{election_id} does not exist.")

        if not election.is_draft:
            raise ValidationError("Configuration is frozen. Voter allocations cannot be modified once election is activated.")

        try:
            booth = Booth.objects.get(id=booth_id, election=election)
        except Booth.DoesNotExist:
            raise ValidationError("Specified booth does not exist in this election.")

        qs = ElectionVoter.objects.filter(election=election)

        if allocate_all_unallocated:
            qs = qs.filter(booth__isnull=True)
        elif voter_ids is not None:
            qs = qs.filter(voter_id__in=voter_ids)
        elif academic_group_id is not None:
            qs = qs.filter(voter__academic_group_id=academic_group_id)
        else:
            raise ValidationError("Must specify voter_ids, academic_group_id, or allocate_all_unallocated.")

        return qs.update(booth=booth)


def deallocate_voters_from_booth(
    *,
    election_id: int,
    voter_ids: Optional[List[int]] = None,
    booth_id: Optional[int] = None,
) -> int:
    """Deallocate enrolled ElectionVoters (sets booth=None). Permitted ONLY in DRAFT."""
    with transaction.atomic():
        try:
            election = Election.objects.select_for_update().get(id=election_id)
        except Election.DoesNotExist:
            raise ValidationError(f"Election #{election_id} does not exist.")

        if not election.is_draft:
            raise ValidationError("Configuration is frozen. Voter allocations cannot be modified once election is activated.")

        qs = ElectionVoter.objects.filter(election=election)
        if booth_id:
            qs = qs.filter(booth_id=booth_id)
        if voter_ids:
            qs = qs.filter(voter_id__in=voter_ids)

        return qs.update(booth=None)


def auto_distribute_unallocated_voters(*, election_id: int) -> dict:
    """Evenly balance all unallocated ElectionVoters across available booths in the election. Permitted ONLY in DRAFT."""
    from voters.models import Booth

    with transaction.atomic():
        try:
            election = Election.objects.select_for_update().get(id=election_id)
        except Election.DoesNotExist:
            raise ValidationError(f"Election #{election_id} does not exist.")

        if not election.is_draft:
            raise ValidationError("Configuration is frozen. Voter allocations cannot be modified once election is activated.")

        booths = list(Booth.objects.filter(election=election).order_by('booth_number'))
        if not booths:
            raise ValidationError("Cannot distribute voters: no booths have been configured.")

        unallocated_voters = list(ElectionVoter.objects.filter(election=election, booth__isnull=True).order_by('voter__primary_registry_value'))
        if not unallocated_voters:
            return {"distributed": 0}

        counts = {b.id: 0 for b in booths}
        for i, ev in enumerate(unallocated_voters):
            target_booth = booths[i % len(booths)]
            ev.booth = target_booth
            counts[target_booth.id] += 1

        ElectionVoter.objects.bulk_update(unallocated_voters, ['booth'])
        return {"distributed": len(unallocated_voters), "counts": counts}


def customize_booth_allocation(
    *,
    election_id: int,
    booth_id: int,
    academic_group_ids: List[int],
    auto_distribute_remaining: bool = False,
) -> dict:
    """Assign specific academic groups to a polling booth.
    
    Enforces:
    - Election must be in DRAFT (configuration freeze).
    - Booth must belong to this election.
    - All voters in the specified academic groups in this election are assigned to this booth.
    - Any voters previously assigned to this booth who are NOT in academic_group_ids are deallocated (booth=None).
    - If auto_distribute_remaining is True, any unallocated voters in the election are evenly distributed across all other booths in the election.
    """
    from voters.models import Booth
    with transaction.atomic():
        try:
            election = Election.objects.select_for_update().get(id=election_id)
        except Election.DoesNotExist:
            raise ValidationError(f"Election #{election_id} does not exist.")

        if not election.is_draft:
            raise ValidationError("Configuration is frozen. Voter allocations cannot be modified once election is activated.")

        try:
            booth = Booth.objects.get(id=booth_id, election=election)
        except Booth.DoesNotExist:
            raise ValidationError(f"Booth #{booth_id} does not exist in this election.")

        # Deallocate voters currently assigned to this booth whose group is not in academic_group_ids
        ElectionVoter.objects.filter(
            election=election,
            booth=booth
        ).exclude(
            voter__academic_group_id__in=academic_group_ids
        ).update(booth=None)

        # Assign voters in the specified academic groups to this booth
        if academic_group_ids:
            ElectionVoter.objects.filter(
                election=election,
                voter__academic_group_id__in=academic_group_ids
            ).update(booth=booth)

        # Auto-distribute unallocated voters to other booths if requested
        if auto_distribute_remaining:
            other_booths = list(Booth.objects.filter(election=election).exclude(id=booth.id).order_by('booth_number'))
            if other_booths:
                unallocated = list(ElectionVoter.objects.filter(election=election, booth__isnull=True).order_by('voter__primary_registry_value'))
                for i, ev in enumerate(unallocated):
                    ev.booth = other_booths[i % len(other_booths)]
                if unallocated:
                    ElectionVoter.objects.bulk_update(unallocated, ['booth'])

        return {
            "booth_id": booth.id,
            "booth_number": booth.booth_number,
            "allocated_count": booth.allocated_voters.count(),
        }


def rebalance_election_voters(*, election_id: int) -> dict:
    """Evenly rebalance all enrolled ElectionVoters across all available booths. Permitted ONLY in DRAFT."""
    from voters.models import Booth
    with transaction.atomic():
        try:
            election = Election.objects.select_for_update().get(id=election_id)
        except Election.DoesNotExist:
            raise ValidationError(f"Election #{election_id} does not exist.")

        if not election.is_draft:
            raise ValidationError("Configuration is frozen. Voter allocations cannot be modified once election is activated.")

        booths = list(Booth.objects.filter(election=election).order_by('booth_number'))
        if not booths:
            raise ValidationError("Cannot distribute voters: no booths have been configured.")

        voters = list(ElectionVoter.objects.filter(election=election).order_by('voter__primary_registry_value'))
        if not voters:
            return {"distributed": 0}

        for i, ev in enumerate(voters):
            ev.booth = booths[i % len(booths)]

        ElectionVoter.objects.bulk_update(voters, ['booth'])
        return {"distributed": len(voters)}


def create_booth(
    *,
    election_id: int,
    name: str = "",
    booth_number: Optional[int] = None,
) -> dict:
    """Create a new polling booth with paired Officer Station and Voting Kiosk technical accounts.
    
    Enforces:
    - Election must be in DRAFT (configuration freeze).
    - booth_number is automatically assigned if not provided.
    - Each booth is provisioned with exactly one Officer Device and one Kiosk Device.
    - Plaintext credentials are returned immediately for admin distribution.
    """
    from accounts.models import Device, DeviceType
    from accounts.services import create_device
    from voters.models import Booth

    with transaction.atomic():
        try:
            election = Election.objects.select_for_update().get(id=election_id)
        except Election.DoesNotExist:
            raise ValidationError(f"Election #{election_id} does not exist.")

        if not election.is_draft:
            raise ValidationError("Configuration is frozen. Booths can only be created while election is in DRAFT.")

        if booth_number is None:
            max_num = Booth.objects.filter(election=election).aggregate(models.Max('booth_number'))['booth_number__max']
            booth_number = (max_num or 0) + 1
        else:
            if booth_number <= 0:
                raise ValidationError("Booth number must be a positive integer.")
            if Booth.objects.filter(election=election, booth_number=booth_number).exists():
                raise ValidationError(f"Booth number {booth_number} already exists in this election.")

        booth = Booth(
            election=election,
            booth_number=booth_number,
            name=name.strip(),
        )
        booth.full_clean()
        booth.save()

        import secrets
        from accounts.models import User
        from accounts.services import READABLE_WORDS, cleanup_past_credentials, create_device

        # Automatically remove past credentials not in use by any draft or ongoing election
        cleanup_past_credentials()

        # Randomly generate readable station usernames (e.g. officer-river-1, kiosk-river-1)
        # Ensure guaranteed uniqueness against both User and Device
        word = secrets.choice(READABLE_WORDS)
        officer_id = f"officer-{word}-{booth.booth_number}"
        if Device.objects.filter(identifier=officer_id).exists() or User.objects.filter(username=officer_id).exists():
            officer_id = f"officer-{word}-{booth.booth_number}-{secrets.randbelow(900)+100}"
        while Device.objects.filter(identifier=officer_id).exists() or User.objects.filter(username=officer_id).exists():
            w = secrets.choice(READABLE_WORDS)
            officer_id = f"officer-{w}-{booth.booth_number}-{secrets.randbelow(900)+100}"

        kiosk_id = f"kiosk-{word}-{booth.booth_number}"
        if Device.objects.filter(identifier=kiosk_id).exists() or User.objects.filter(username=kiosk_id).exists():
            kiosk_id = f"kiosk-{word}-{booth.booth_number}-{secrets.randbelow(900)+100}"
        while Device.objects.filter(identifier=kiosk_id).exists() or User.objects.filter(username=kiosk_id).exists():
            w = secrets.choice(READABLE_WORDS)
            kiosk_id = f"kiosk-{w}-{booth.booth_number}-{secrets.randbelow(900)+100}"

        officer_device, officer_pw = create_device(
            identifier=officer_id,
            device_type=DeviceType.OFFICER,
            booth=booth,
        )
        kiosk_device, kiosk_pw = create_device(
            identifier=kiosk_id,
            device_type=DeviceType.KIOSK,
            booth=booth,
        )

        return {
            'booth': booth,
            'officer_device': officer_device,
            'officer_username': officer_id,
            'officer_password': officer_pw,
            'kiosk_device': kiosk_device,
            'kiosk_username': kiosk_id,
            'kiosk_password': kiosk_pw,
        }


def update_booth(
    *,
    booth_id: int,
    name: Optional[str] = None,
) -> 'Booth':
    """Update booth name/location. Permitted ONLY in DRAFT status."""
    from voters.models import Booth

    with transaction.atomic():
        try:
            booth = Booth.objects.select_for_update().get(id=booth_id)
        except Booth.DoesNotExist:
            raise ValidationError(f"Booth #{booth_id} does not exist.")

        if not booth.election.is_draft:
            raise ValidationError("Configuration is frozen. Booth cannot be modified once election is activated.")

        if name is not None:
            booth.name = name.strip()

        booth.full_clean()
        booth.save()
        return booth


def remove_booth(*, booth_id: int, auto_distribute: bool = True) -> None:
    """Remove a polling booth prior to election start.
    
    Enforces:
    - Election must be in DRAFT.
    - Sets booth=None on any previously allocated voters (requiring reallocation before start).
    - Deletes paired devices and their underlying user accounts.
    - Deletes the booth.
    - If auto_distribute is True, distributes unallocated voters across remaining booths.
    """
    from voters.models import Booth

    with transaction.atomic():
        try:
            booth = Booth.objects.select_for_update().get(id=booth_id)
        except Booth.DoesNotExist:
            raise ValidationError(f"Booth #{booth_id} does not exist.")

        if not booth.election.is_draft:
            raise ValidationError("Configuration is frozen. Booths cannot be removed once election is activated.")

        # Disassociate enrolled voters
        ElectionVoter.objects.filter(booth=booth).update(booth=None)

        # Retrieve devices and their users before deletion
        devices = list(booth.devices.select_related('user').all())
        users_to_delete = [d.user for d in devices if d.user]

        election = booth.election

        # Deleting booth cascades to devices
        booth.delete()

        # Delete associated users
        for u in users_to_delete:
            u.delete()

        # Renumber remaining booths sequentially to accommodate the loss: 1, 2, 3...
        remaining_booths = list(Booth.objects.filter(election=election).order_by('booth_number', 'id'))
        for idx, b in enumerate(remaining_booths, start=1):
            if b.booth_number != idx:
                b.booth_number = idx
                b.save(update_fields=['booth_number'])

        # Auto-distribute unallocated voters to the remaining booths if auto_distribute is True
        if auto_distribute and remaining_booths:
            unallocated = list(ElectionVoter.objects.filter(election=election, booth__isnull=True).order_by('voter__primary_registry_value'))
            for i, ev in enumerate(unallocated):
                ev.booth = remaining_booths[i % len(remaining_booths)]
            if unallocated:
                ElectionVoter.objects.bulk_update(unallocated, ['booth'])


def rotate_booth_device_credentials(*, device_id: int) -> Tuple['Device', str]:
    """Rotate credentials for a booth device.
    
    Permitted even when election is ACTIVE to support physical device replacement.
    Invalidates any active sessions for the device.
    """
    from accounts.services import rotate_device_credentials
    return rotate_device_credentials(device_id)

