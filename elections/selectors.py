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
    """List all elections for the installation, ordered by created_at descending."""
    return Election.objects.annotate(
        position_count=Count("positions", distinct=True),
        enrolled_voter_count=Count("election_voters", distinct=True),
        booth_count=Count("booths", distinct=True),
    ).order_by("-created_at")


def get_election_positions(election_id: int) -> QuerySet[Position]:
    """Retrieve all ballot positions for an election, prefetching candidates ordered by display_order."""
    return Position.objects.filter(election_id=election_id).prefetch_related(
        "candidates__voter"
    ).order_by("display_order", "id")


def get_election_details(election_id: int) -> Optional[dict]:
    """Retrieve comprehensive read-only state for an election including positions, candidates, and readiness."""
    election = get_election(election_id)
    if not election:
        return None

    positions = list(get_election_positions(election_id))
    structural_errors = validate_election_configuration(election, check_cutoff=False) if election.is_draft else []
    validation_errors = validate_election_configuration(election, check_cutoff=True) if election.is_draft else []
    total_candidates = sum(len(p.candidates.all()) for p in positions)
    enrolled_count = election.election_voters.count()
    unallocated_count = election.election_voters.filter(booth__isnull=True).count()
    booths = list(election.booths.prefetch_related("devices").all())

    return {
        "election": election,
        "positions": positions,
        "position_count": len(positions),
        "candidate_count": total_candidates,
        "enrolled_voter_count": enrolled_count,
        "unallocated_voter_count": unallocated_count,
        "booth_count": len(booths),
        "booths": booths,
        "is_ready_to_start": election.is_draft and len(structural_errors) == 0,
        "structural_errors": structural_errors,
        "validation_errors": validation_errors,
    }


def list_election_history(search_query: str = "", year_filter: str = "") -> dict:
    """Retrieve archived past elections (CLOSED or RESULTS_PUBLISHED) with turnout and position stats.

    Returns a dict with:
    - 'items': list of election history item dicts
    - 'available_years': sorted list of distinct years for filtering
    - 'total_past_elections': total number of past elections in history (unfiltered)
    """
    from django.db.models import Count, Q

    base_qs = Election.objects.filter(
        status__in=[ElectionStatus.CLOSED, ElectionStatus.RESULTS_PUBLISHED]
    )
    total_past_elections = base_qs.count()

    # Determine all available years across past elections
    years_set = set()
    for el in base_qs.values_list("closed_at", "starts_at", "created_at"):
        for dt in el:
            if dt:
                years_set.add(dt.year)
    available_years = sorted(list(years_set), reverse=True)

    qs = base_qs
    if search_query:
        sq = search_query.strip()
        query_filter = Q(name__icontains=sq) | Q(description__icontains=sq)
        if sq.isdigit() and len(sq) == 4:
            yr_int = int(sq)
            query_filter |= (
                Q(closed_at__year=yr_int) |
                Q(starts_at__year=yr_int) |
                Q(created_at__year=yr_int)
            )
        qs = qs.filter(query_filter)

    if year_filter:
        try:
            yr = int(year_filter)
            qs = qs.filter(
                Q(closed_at__year=yr) |
                (Q(closed_at__isnull=True) & Q(starts_at__year=yr)) |
                (Q(closed_at__isnull=True) & Q(starts_at__isnull=True) & Q(created_at__year=yr))
            )
        except (ValueError, TypeError):
            pass

    qs = qs.annotate(
        total_voters=Count("election_voters__id", distinct=True),
        voted_count=Count("election_voters__id", filter=Q(election_voters__has_voted=True), distinct=True),
        positions_count=Count("positions__id", distinct=True),
    ).order_by("-closed_at", "-starts_at", "-created_at")

    items = []
    for el in qs:
        t_pct = round((el.voted_count / el.total_voters * 100), 1) if el.total_voters > 0 else 0.0
        primary_dt = el.closed_at or el.starts_at or el.created_at
        items.append({
            "election": el,
            "total_voters": el.total_voters,
            "voted_count": el.voted_count,
            "turnout_pct": t_pct,
            "positions_count": el.positions_count,
            "date": primary_dt,
        })

    return {
        "items": items,
        "available_years": available_years,
        "total_past_elections": total_past_elections,
    }

