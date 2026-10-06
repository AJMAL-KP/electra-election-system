"""Domain services for voting application.

Owns:
- Authorization creation, verification, and cancellation
- Multi-position ballot validation
- Atomic ballot submission with transaction isolation
- Turnout aggregation and results computation
"""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from django.db.models import Count, Q
from accounts.models import Device, DeviceSession, DeviceType
from elections.models import Candidate, Election, ElectionStatus, Position
from voters.models import ElectionVoter
from voting.models import AuthorizationStatus, VoterAuthorization, Vote


def get_channel():
    return get_channel_layer()


def notify_kiosk_unlock(kiosk_device_id: int, authorization_id: int, eligible_position_ids: list = None):
    """Post-commit WebSocket broadcast to unlock the target Voting Kiosk."""
    channel_layer = get_channel()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            f"kiosk_{kiosk_device_id}",
            {
                "type": "kiosk.unlock",
                "authorization_id": authorization_id,
                "eligible_position_ids": eligible_position_ids or [],
            }
        )


def notify_kiosk_lock(kiosk_device_id: int):
    """Post-commit WebSocket broadcast to lock the target Voting Kiosk."""
    channel_layer = get_channel()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            f"kiosk_{kiosk_device_id}",
            {
                "type": "kiosk.lock",
            }
        )


def notify_officer_voter_status(booth_id: int, voter_id: str, status: str):
    """Post-commit WebSocket broadcast to update voter authorization state on Officer Station."""
    channel_layer = get_channel()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            f"booth_{booth_id}",
            {
                "type": "authorization.updated",
                "voter_id": voter_id,
                "status": status,
            }
        )


def create_authorization(officer_device: Device, voter_identifier: str) -> VoterAuthorization:
    """Creates a single-use ACTIVE authorization for an enrolled voter.
    
    Enforces strict row lock ordering:
        Election -> ElectionVoter -> Target Kiosk Device -> VoterAuthorization
    
    Validates:
    - Election is ACTIVE.
    - Voting window is valid.
    - Voter is enrolled in this election.
    - Voter belongs to the Officer's booth (Booth Isolation).
    - Voter has not already voted.
    - Paired Kiosk has an active DeviceSession.
    - No simultaneous ACTIVE authorization exists for this voter.
    - No simultaneous ACTIVE authorization exists for this Kiosk.
    
    Emits `kiosk.unlock` ONLY after atomic database commit.
    """
    if not officer_device or officer_device.device_type != DeviceType.OFFICER or not officer_device.booth:
        raise ValidationError("Only an authenticated Officer Station Device bound to a booth can issue voting authorizations.")

    booth = officer_device.booth

    with transaction.atomic():
        # 1. Lock Election row
        try:
            election = Election.objects.select_for_update().get(id=booth.election_id)
        except Election.DoesNotExist:
            raise ValidationError("Election record not found.")

        if election.status != ElectionStatus.ACTIVE:
            raise ValidationError("Authorizations can only be issued when the election is ACTIVE.")

        now = timezone.now()
        if election.starts_at and now < election.starts_at:
            raise ValidationError("Voting has not yet started for this election.")
        if election.ends_at and now >= election.ends_at:
            raise ValidationError("Voting has concluded for this election.")

        # 2. Lock ElectionVoter row
        if str(voter_identifier).isdigit():
            election_voter = (
                ElectionVoter.objects.select_for_update()
                .filter(election=election)
                .filter(
                    Q(id=int(voter_identifier)) | Q(voter__primary_registry_value=voter_identifier)
                )
                .select_related("voter")
                .first()
            )
        else:
            election_voter = (
                ElectionVoter.objects.select_for_update()
                .filter(election=election, voter__primary_registry_value=voter_identifier)
                .select_related("voter")
                .first()
            )

        if not election_voter:
            raise ValidationError(f"Voter '{voter_identifier}' is not enrolled in this election.")

        # Invariant: Booth Isolation
        if election_voter.booth_id != booth.id:
            raise ValidationError("Cross-booth authorization rejected: Voter is assigned to a different polling booth.")

        # Invariant: Duplicate Prevention
        if election_voter.has_voted:
            raise ValidationError("Voter has already cast their ballot in this election.")

        # 3. Lock Target Kiosk Device
        kiosk_device = (
            Device.objects.select_for_update()
            .filter(booth=booth, device_type=DeviceType.KIOSK)
            .first()
        )
        if not kiosk_device:
            raise ValidationError("No Voting Kiosk paired with this booth.")

        is_kiosk_connected = DeviceSession.objects.filter(
            device=kiosk_device,
            is_active=True
        ).exists()
        if not is_kiosk_connected:
            raise ValidationError("Voting Kiosk is offline or not logged in. Cannot issue authorization.")

        # 4. Lock and check existing ACTIVE authorizations
        active_voter_auth = (
            VoterAuthorization.objects.select_for_update()
            .filter(election_voter=election_voter, status=AuthorizationStatus.ACTIVE)
            .first()
        )
        if active_voter_auth:
            raise ValidationError("An active voting authorization already exists for this voter.")

        active_kiosk_auth = (
            VoterAuthorization.objects.select_for_update()
            .filter(kiosk=kiosk_device, status=AuthorizationStatus.ACTIVE)
            .first()
        )
        if active_kiosk_auth:
            raise ValidationError("Voting Kiosk is currently in use with an active authorization.")

        # 5. Create the ACTIVE authorization record
        auth = VoterAuthorization.objects.create(
            election_voter=election_voter,
            booth=booth,
            kiosk=kiosk_device,
            status=AuthorizationStatus.ACTIVE,
        )

        # Determine which contested positions this voter is eligible for
        voter = election_voter.voter
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

        # 6. Post-commit event emission (NEVER emits before commit)
        voter_id_val = election_voter.voter.primary_registry_value
        kiosk_id_val = kiosk_device.id
        auth_id_val = auth.id
        booth_id_val = booth.id

        transaction.on_commit(lambda: notify_kiosk_unlock(kiosk_id_val, auth_id_val, eligible_pos_ids))
        transaction.on_commit(lambda: notify_officer_voter_status(booth_id_val, voter_id_val, "in_progress"))

        return auth


def cancel_authorization(officer_device: Device, authorization_id: int) -> VoterAuthorization:
    """Cancels an existing ACTIVE authorization if a voter leaves or an officer aborts."""
    if not officer_device or officer_device.device_type != DeviceType.OFFICER or not officer_device.booth:
        raise ValidationError("Only the bound Officer Station can cancel an authorization.")

    with transaction.atomic():
        auth = (
            VoterAuthorization.objects.select_for_update()
            .select_related("election_voter__voter", "kiosk")
            .filter(id=authorization_id, booth=officer_device.booth)
            .first()
        )
        if not auth:
            raise ValidationError("Authorization not found for this booth.")

        if auth.status != AuthorizationStatus.ACTIVE:
            raise ValidationError(f"Cannot cancel authorization with status '{auth.status}'.")

        auth.status = AuthorizationStatus.CANCELLED
        auth.cancelled_at = timezone.now()
        auth.save(update_fields=["status", "cancelled_at"])

        kiosk_id_val = auth.kiosk_id
        booth_id_val = auth.booth_id
        voter_id_val = auth.election_voter.voter.primary_registry_value

        transaction.on_commit(lambda: notify_kiosk_lock(kiosk_id_val))
        transaction.on_commit(lambda: notify_officer_voter_status(booth_id_val, voter_id_val, "not_voted"))

        return auth


def models_q_match(identifier: str):
    from django.db.models import Q
    return Q(voter__primary_registry_value=identifier) | Q(id=int(identifier))


def notify_ballot_recorded(booth_id: int):
    """Post-commit WebSocket broadcast when a ballot is recorded."""
    channel_layer = get_channel()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            f"booth_{booth_id}",
            {
                "type": "ballot.recorded",
            }
        )
        async_to_sync(channel_layer.group_send)(
            "admin_live",
            {
                "type": "ballot.recorded",
                "booth_id": booth_id,
            }
        )


def is_voter_eligible_for_position(voter, position) -> bool:
    """Checks whether a voter meets demographic and group eligibility qualifications for a position."""
    if not voter:
        return False

    # 1. Gender restriction check
    if position.eligible_gender and position.eligible_gender != 'ALL':
        voter_gender = (voter.gender or "").strip().upper()
        if voter_gender != position.eligible_gender.strip().upper():
            return False

    # 2. Academic group restriction check
    if position.eligible_groups.exists():
        if not voter.academic_group_id:
            return False
        eligible_group_ids = set(position.eligible_groups.values_list('id', flat=True))
        voter_grp = voter.academic_group
        if voter_grp.id not in eligible_group_ids and (not voter_grp.parent_id or voter_grp.parent_id not in eligible_group_ids):
            return False

    return True


def submit_ballot(kiosk_device: Device, authorization_id: int, selections: dict) -> dict:
    """Transactionally records a complete ballot and consumes authorization.
    
    Preserves strict row lock order:
        Election -> ElectionVoter -> VoterAuthorization -> Kiosk Device
    
    STRICT BALLOT SECRECY:
    Vote records contain strictly (election, candidate, created_at).
    Zero voter, booth, or authorization foreign keys exist on Vote.
    """
    if not kiosk_device or kiosk_device.device_type != DeviceType.KIOSK or not kiosk_device.booth:
        raise ValidationError("Only an authenticated Voting Kiosk Device bound to a booth can submit ballots.")

    booth = kiosk_device.booth

    with transaction.atomic():
        # First retrieve the voter ID associated with this authorization (unlocked peek to preserve lock order)
        auth_pre = (
            VoterAuthorization.objects
            .filter(id=authorization_id, kiosk=kiosk_device, booth=booth)
            .values("id", "election_voter_id", "status")
            .first()
        )
        if not auth_pre:
            raise ValidationError("Authorization not found for this Voting Kiosk.")

        if auth_pre["status"] != AuthorizationStatus.ACTIVE:
            raise ValidationError(f"Authorization is '{auth_pre['status']}' and cannot be used to submit a ballot.")

        # 1. Lock Election row
        try:
            election = Election.objects.select_for_update().get(id=booth.election_id)
        except Election.DoesNotExist:
            raise ValidationError("Election record not found.")

        if election.status != ElectionStatus.ACTIVE:
            raise ValidationError("Ballot submission rejected: Election is not currently active.")

        now = timezone.now()
        if election.ends_at and now >= election.ends_at:
            raise ValidationError("Ballot submission rejected: Voting window has concluded.")

        # 2. Lock ElectionVoter row
        try:
            election_voter = (
                ElectionVoter.objects
                .select_for_update()
                .get(id=auth_pre["election_voter_id"])
            )
        except ElectionVoter.DoesNotExist:
            raise ValidationError("Voter enrollment record not found.")

        if election_voter.has_voted:
            raise ValidationError("Voter has already cast their ballot.")

        if election_voter.booth_id != booth.id:
            raise ValidationError("Cross-booth submission rejected: Voter belongs to another booth.")

        # 3. Lock VoterAuthorization row
        auth = VoterAuthorization.objects.select_for_update().get(id=authorization_id)
        if auth.status != AuthorizationStatus.ACTIVE:
            raise ValidationError("Authorization is no longer active.")

        # 4. Lock Kiosk Device
        kiosk = Device.objects.select_for_update().get(id=kiosk_device.id)

        # 5. Validate Candidate Selections for Eligible Contested Positions
        # Positions with candidate_count > 1 require a selection. Single-candidate positions won unanimously.
        all_contested = list(
            election.positions
            .prefetch_related('eligible_groups')
            .annotate(cand_count=Count('candidates'))
            .filter(cand_count__gt=1)
            .order_by('display_order', 'id')
        )

        voter = election_voter.voter
        required_positions = [pos for pos in all_contested if is_voter_eligible_for_position(voter, pos)]
        ineligible_pos_ids = {pos.id for pos in all_contested if not is_voter_eligible_for_position(voter, pos)}

        # Reject any submitted selections for positions the voter does not qualify for
        for pos_id_str in selections.keys():
            try:
                p_id = int(pos_id_str)
            except (ValueError, TypeError):
                continue
            if p_id in ineligible_pos_ids:
                pos_obj = next((p for p in all_contested if p.id == p_id), None)
                pos_name = pos_obj.name if pos_obj else f"Position #{p_id}"
                raise ValidationError(f"Voter is not eligible to vote for position '{pos_name}'.")

        votes_to_create = []
        for pos in required_positions:
            pos_key = str(pos.id)
            cand_id = selections.get(pos_key) or selections.get(pos.id)

            if not cand_id:
                raise ValidationError(f"Missing candidate selection for position '{pos.name}'.")

            try:
                cand_id_int = int(cand_id)
            except (ValueError, TypeError):
                raise ValidationError(f"Invalid candidate identifier for position '{pos.name}'.")

            candidate = Candidate.objects.filter(id=cand_id_int, position=pos).first()
            if not candidate:
                raise ValidationError(f"Candidate not valid for position '{pos.name}'.")

            votes_to_create.append(Vote(
                election=election,
                candidate=candidate
            ))

        # 6. Bulk create anonymous Vote rows (atomic)
        Vote.objects.bulk_create(votes_to_create)

        # 7. Consume Authorization & Mark Voter as Voted
        election_voter.has_voted = True
        election_voter.save(update_fields=["has_voted"])

        auth.status = AuthorizationStatus.USED
        auth.used_at = now
        auth.save(update_fields=["status", "used_at"])

        # 8. Post-commit event emissions (never emit before commit)
        kiosk_id_val = kiosk.id
        booth_id_val = booth.id
        voter_id_val = election_voter.voter.primary_registry_value

        transaction.on_commit(lambda: notify_kiosk_lock(kiosk_id_val))
        transaction.on_commit(lambda: notify_officer_voter_status(booth_id_val, voter_id_val, "voted"))
        transaction.on_commit(lambda: notify_ballot_recorded(booth_id_val))

        return {
            "success": True,
            "message": "Ballot recorded successfully.",
            "votes_recorded": len(votes_to_create),
        }


