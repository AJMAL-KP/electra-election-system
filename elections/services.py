"""Domain services for elections application.

Owns:
- Election lifecycle: create, update, delete (DRAFT only)
- Position & Candidate management with configuration freeze enforcement
- Configuration validation (readiness rules before activation)
- Start, close, and publish_results operations (atomic with select_for_update)
- Server-side deadline expiry enforcement
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from elections.models import Candidate, Election, ElectionStatus, Position


def create_election(*, name: str, starts_at, ends_at, description: str = "") -> Election:
    """Create a new election in DRAFT status for the installation.
    
    Installation-scoped: no owner or tenant scoping.
    """
    if ends_at <= starts_at:
        raise ValidationError("Election cutoff time (ends_at) must be strictly after start time (starts_at).")

    election = Election(
        name=name.strip(),
        description=description.strip(),
        starts_at=starts_at,
        ends_at=ends_at,
        status=ElectionStatus.DRAFT,
    )
    election.full_clean()
    election.save()
    return election


def update_election(
    *,
    election_id: int,
    name: str,
    starts_at,
    ends_at,
    description: str = "",
) -> Election:
    """Update election parameters. Permitted ONLY when election is in DRAFT."""
    with transaction.atomic():
        election = Election.objects.select_for_update().get(id=election_id)
        if not election.is_draft:
            raise ValidationError("Configuration is frozen. Only DRAFT elections may be edited.")

        if ends_at <= starts_at:
            raise ValidationError("Election cutoff time must be strictly after start time.")

        election.name = name.strip()
        election.description = description.strip()
        election.starts_at = starts_at
        election.ends_at = ends_at
        election.full_clean()
        election.save()
        return election


def delete_election(*, election_id: int) -> None:
    """Delete an election. Permitted ONLY when election is in DRAFT."""
    with transaction.atomic():
        election = Election.objects.select_for_update().get(id=election_id)
        if not election.is_draft:
            raise ValidationError("Cannot delete election: configuration is frozen once activated.")
        election.delete()


def create_position(*, election_id: int, name: str, display_order: int = 1) -> Position:
    """Add an elected position to an election. Permitted ONLY when election is in DRAFT."""
    with transaction.atomic():
        election = Election.objects.select_for_update().get(id=election_id)
        if not election.is_draft:
            raise ValidationError("Configuration is frozen. Positions cannot be added to an active or closed election.")

        cleaned_name = name.strip()
        if Position.objects.filter(election=election, name__iexact=cleaned_name).exists():
            raise ValidationError(f"A position named '{cleaned_name}' already exists in this election.")

        if Position.objects.filter(election=election, display_order=display_order).exists():
            raise ValidationError(f"Ballot order #{display_order} is already assigned to another position in this election.")

        position = Position(
            election=election,
            name=cleaned_name,
            display_order=display_order,
        )
        position.full_clean()
        position.save()
        return position


def update_position(*, position_id: int, name: str, display_order: int) -> Position:
    """Update a position. Permitted ONLY when parent election is in DRAFT."""
    with transaction.atomic():
        position = Position.objects.select_for_update().select_related("election").get(id=position_id)
        if not position.election.is_draft:
            raise ValidationError("Configuration is frozen. Positions cannot be modified once election is activated.")

        cleaned_name = name.strip()
        if Position.objects.filter(election=position.election, name__iexact=cleaned_name).exclude(id=position.id).exists():
            raise ValidationError(f"Another position named '{cleaned_name}' already exists in this election.")

        if Position.objects.filter(election=position.election, display_order=display_order).exclude(id=position.id).exists():
            raise ValidationError(f"Ballot order #{display_order} is already assigned to another position in this election.")

        position.name = cleaned_name
        position.display_order = display_order
        position.full_clean()
        position.save()
        return position


def delete_position(*, position_id: int) -> None:
    """Delete a position. Permitted ONLY when parent election is in DRAFT."""
    with transaction.atomic():
        position = Position.objects.select_for_update().select_related("election").get(id=position_id)
        if not position.election.is_draft:
            raise ValidationError("Configuration is frozen. Positions cannot be deleted once election is activated.")
        position.delete()


def create_candidate(*, position_id: int, name: str, academic_group: str = "", symbol: str = "", photo=None) -> Candidate:
    """Register a candidate contesting a position. Permitted ONLY when election is in DRAFT."""
    with transaction.atomic():
        position = Position.objects.select_for_update().select_related("election").get(id=position_id)
        if not position.election.is_draft:
            raise ValidationError("Configuration is frozen. Candidates cannot be added once election is activated.")

        cleaned_name = name.strip()
        cleaned_symbol = symbol.strip()
        if Candidate.objects.filter(position=position, name__iexact=cleaned_name).exists():
            raise ValidationError(f"Candidate '{cleaned_name}' is already registered for this position.")

        if cleaned_symbol and Candidate.objects.filter(position__election=position.election, symbol__iexact=cleaned_symbol).exists():
            raise ValidationError(f"Ballot symbol '{cleaned_symbol}' is already assigned to another candidate in this election.")

        candidate = Candidate(
            position=position,
            name=cleaned_name,
            academic_group=academic_group.strip(),
            symbol=cleaned_symbol,
            photo=photo,
        )
        candidate.full_clean()
        candidate.save()
        return candidate


def update_candidate(*, candidate_id: int, name: str, academic_group: str = "", symbol: str = "", photo=None) -> Candidate:
    """Update candidate details. Permitted ONLY when parent election is in DRAFT."""
    with transaction.atomic():
        candidate = Candidate.objects.select_for_update().select_related("position__election").get(id=candidate_id)
        if not candidate.position.election.is_draft:
            raise ValidationError("Configuration is frozen. Candidates cannot be modified once election is activated.")

        cleaned_name = name.strip()
        cleaned_symbol = symbol.strip()
        if Candidate.objects.filter(position=candidate.position, name__iexact=cleaned_name).exclude(id=candidate.id).exists():
            raise ValidationError(f"Candidate '{cleaned_name}' already exists for this position.")

        if cleaned_symbol and Candidate.objects.filter(position__election=candidate.position.election, symbol__iexact=cleaned_symbol).exclude(id=candidate.id).exists():
            raise ValidationError(f"Ballot symbol '{cleaned_symbol}' is already assigned to another candidate in this election.")

        candidate.name = cleaned_name
        candidate.academic_group = academic_group.strip()
        candidate.symbol = cleaned_symbol
        if photo is not None:
            candidate.photo = photo
        candidate.full_clean()
        candidate.save()
        return candidate


def delete_candidate(*, candidate_id: int) -> None:
    """Delete a candidate. Permitted ONLY when parent election is in DRAFT."""
    with transaction.atomic():
        candidate = Candidate.objects.select_for_update().select_related("position__election").get(id=candidate_id)
        if not candidate.position.election.is_draft:
            raise ValidationError("Configuration is frozen. Candidates cannot be deleted once election is activated.")
        candidate.delete()


def validate_election_configuration(election: Election) -> list[str]:
    """Validate election configuration readiness.
    
    Returns a list of error strings if invalid, or empty list if valid.
    Rules:
    - Election must be in DRAFT status.
    - ends_at must be strictly greater than starts_at.
    - At least one position must exist.
    - Every position must have at least one candidate.
    - No other election is currently ACTIVE.
    """
    errors: list[str] = []

    if not election.is_draft:
        errors.append(f"Election is in {election.status} state; only DRAFT elections can be validated for activation.")

    if election.ends_at <= election.starts_at:
        errors.append("Election cutoff time must be strictly after start time.")

    # Check for another active election in the installation
    active_exists = Election.objects.filter(status=ElectionStatus.ACTIVE).exclude(id=election.id).exists()
    if active_exists:
        errors.append("Another election is currently ACTIVE. Only one election may be active at a time.")

    positions = list(election.positions.prefetch_related("candidates").all())
    if not positions:
        errors.append("Election has no ballot positions configured. At least one position is required.")
    else:
        for pos in positions:
            if pos.candidates.count() == 0:
                errors.append(f"Position '{pos.name}' has no registered candidates. Every position requires at least one candidate.")

    return errors


def start_election(*, election_id: int) -> Election:
    """Atomically start the election, transitioning DRAFT -> ACTIVE.
    
    Locks the election row and enforces:
    - Exact transition from DRAFT -> ACTIVE.
    - Full configuration readiness checks.
    - Configuration is frozen upon activation.
    """
    with transaction.atomic():
        election = Election.objects.select_for_update().get(id=election_id)
        
        errors = validate_election_configuration(election)
        if errors:
            raise ValidationError({"configuration": errors})

        election.status = ElectionStatus.ACTIVE
        election.save(update_fields=["status", "updated_at"])
        return election


def close_election(*, election_id: int) -> Election:
    """Atomically close the election, transitioning ACTIVE -> CLOSED.
    
    Locks the election row so in-flight voting and closure have a deterministic race boundary.
    """
    with transaction.atomic():
        election = Election.objects.select_for_update().get(id=election_id)
        if not election.is_active:
            raise ValidationError(f"Cannot close election: current status is {election.status}, expected ACTIVE.")

        election.status = ElectionStatus.CLOSED
        election.closed_at = timezone.now()
        election.save(update_fields=["status", "closed_at", "updated_at"])
        return election


def publish_results(*, election_id: int) -> Election:
    """Atomically transition CLOSED -> RESULTS_PUBLISHED.
    
    Enforces invariant: Results cannot be published before the election is CLOSED.
    """
    with transaction.atomic():
        election = Election.objects.select_for_update().get(id=election_id)
        if not election.is_closed:
            raise ValidationError(
                f"Cannot publish results: election status is {election.status}. Election must be CLOSED before publishing results."
            )

        election.status = ElectionStatus.RESULTS_PUBLISHED
        election.results_published_at = timezone.now()
        election.save(update_fields=["status", "results_published_at", "updated_at"])
        return election


def close_if_expired(*, election_id: int) -> bool:
    """Server-side deadline check.
    
    If the election is ACTIVE and current server time >= ends_at, atomically closes the election.
    Returns True if closed, False otherwise.
    """
    with transaction.atomic():
        try:
            election = Election.objects.select_for_update().get(id=election_id)
        except Election.DoesNotExist:
            return False

        if election.is_active and timezone.now() >= election.ends_at:
            election.status = ElectionStatus.CLOSED
            election.closed_at = timezone.now()
            election.save(update_fields=["status", "closed_at", "updated_at"])
            return True

    return False
