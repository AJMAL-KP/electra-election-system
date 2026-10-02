"""Query selectors for elections application.

Owns:
- Read-only queries for elections, positions, candidates, and configuration readiness reports
"""
from typing import Optional
from django.db.models import Count, QuerySet

from elections.models import Election, ElectionStatus, Position
from elections.services import validate_election_configuration


def get_election(election_id: int) -> Optional[Election]:
    """Retrieve an election by primary key, or None if not found."""
    try:
        return Election.objects.get(id=election_id)
    except Election.DoesNotExist:
        return None


def get_active_election() -> Optional[Election]:
    """Retrieve the currently ACTIVE election for the installation, or None."""
    return Election.objects.filter(status=ElectionStatus.ACTIVE).first()


def list_elections() -> QuerySet[Election]:
    """List all elections for the installation, ordered by starts_at descending."""
    return Election.objects.annotate(
        position_count=Count("positions", distinct=True),
    ).order_by("-starts_at")


def get_election_positions(election_id: int) -> QuerySet[Position]:
    """Retrieve all ballot positions for an election, prefetching candidates ordered by display_order."""
    return Position.objects.filter(election_id=election_id).prefetch_related(
        "candidates"
    ).order_by("display_order", "id")


def get_election_details(election_id: int) -> Optional[dict]:
    """Retrieve comprehensive read-only state for an election including positions, candidates, and readiness."""
    election = get_election(election_id)
    if not election:
        return None

    positions = list(get_election_positions(election_id))
    validation_errors = validate_election_configuration(election) if election.is_draft else []
    total_candidates = sum(len(p.candidates.all()) for p in positions)

    return {
        "election": election,
        "positions": positions,
        "position_count": len(positions),
        "candidate_count": total_candidates,
        "is_ready_to_start": election.is_draft and len(validation_errors) == 0,
        "validation_errors": validation_errors,
    }
