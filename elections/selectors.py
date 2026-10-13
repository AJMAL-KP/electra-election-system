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
    """Retrieve archived past elections with turnout and position stats.

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

    qs = qs.prefetch_related(
        "positions__candidates",
        "booths"
    ).annotate(
        total_voters=Count("election_voters__id", distinct=True),
        voted_count=Count("election_voters__id", filter=Q(election_voters__has_voted=True), distinct=True),
        positions_count=Count("positions__id", distinct=True),
    ).order_by("-closed_at", "-starts_at", "-created_at")

    items = []
    for el in qs:
        t_pct = round((el.voted_count / el.total_voters * 100), 1) if el.total_voters > 0 else 0.0
        primary_dt = el.closed_at or el.ends_at or el.starts_at or el.created_at
        status_disp = "Completed" if el.status == ElectionStatus.CLOSED else "Results published"
        
        positions_detail = []
        for pos in el.positions.all():
            candidates_list = [c.name for c in pos.candidates.all()]
            positions_detail.append({
                "id": pos.id,
                "name": pos.name,
                "candidates_count": len(candidates_list),
                "candidates": candidates_list,
            })

        items.append({
            "election": el,
            "total_voters": el.total_voters,
            "voted_count": el.voted_count,
            "turnout_pct": t_pct,
            "positions_count": el.positions_count,
            "booths_count": el.booths.count(),
            "date": primary_dt,
            "starts_at": el.starts_at,
            "closed_at": el.closed_at,
            "status_display": status_disp,
            "positions_detail": positions_detail,
        })

    return {
        "items": items,
        "available_years": available_years,
        "total_past_elections": total_past_elections,
    }


def get_election_review_status(election_id: int) -> dict:
    """Compute complete validation checks and live status for an election setup review."""
    from accounts.models import DeviceSession, DeviceType
    from elections.models import Election, Position

    election = Election.objects.filter(id=election_id).first()
    if not election:
        return {}

    total_enrolled_voters = election.election_voters.count()
    positions = list(election.positions.prefetch_related("candidates").order_by("display_order", "id"))
    booths = list(election.booths.prefetch_related("devices__user", "allocated_voters").order_by("booth_number", "id"))

    total_allocated = election.election_voters.filter(booth__isnull=False).count()
    unallocated_count = election.election_voters.filter(booth__isnull=True).count()
    allocated_percentage = int((total_allocated / total_enrolled_voters * 100)) if total_enrolled_voters > 0 else 0

    active_device_ids = set(
        DeviceSession.objects.filter(
            device__booth__election=election,
            is_active=True
        ).values_list("device_id", flat=True)
    )

    booth_review_data = []
    for b in booths:
        officer_dev = next((d for d in b.devices.all() if d.device_type == DeviceType.OFFICER), None)
        kiosk_dev = next((d for d in b.devices.all() if d.device_type == DeviceType.KIOSK), None)

        officer_online = (officer_dev.id in active_device_ids) if officer_dev else False
        kiosk_online = (kiosk_dev.id in active_device_ids) if kiosk_dev else False

        booth_review_data.append({
            "booth_id": b.id,
            "booth_number": b.booth_number,
            "name": b.name,
            "display_name": f"Booth {b.booth_number}" + (f" ({b.name})" if b.name else ""),
            "voter_count": b.allocated_voters.count(),
            "officer_online": officer_online,
            "kiosk_online": kiosk_online,
        })

    # Validation Column 1
    c1_details = {
        "id": "c1_details",
        "label": "Election details provided",
        "passed": bool(election.name.strip()),
    }
    c1_voters = {
        "id": "c1_voters",
        "label": f"Voters selected ({total_enrolled_voters:,})" if total_enrolled_voters > 0 else "No voters selected",
        "passed": total_enrolled_voters > 0,
    }
    c1_positions = {
        "id": "c1_positions",
        "label": f"At least one position added ({len(positions)})" if len(positions) > 0 else "At least one position added",
        "passed": len(positions) > 0,
    }
    c1_candidates = {
        "id": "c1_candidates",
        "label": "Candidates added for all positions",
        "passed": len(positions) > 0 and all(p.candidates.count() > 0 for p in positions),
    }
    has_contested = any(p.candidates.count() > 1 for p in positions)
    c1_contested = {
        "id": "c1_contested",
        "label": "At least one contested position (>1 candidate)",
        "passed": has_contested,
    }
    c1_booths = {
        "id": "c1_booths",
        "label": f"Booths created ({len(booths)})" if len(booths) > 0 else "Booths created",
        "passed": len(booths) > 0,
    }
    c1_allocation = {
        "id": "c1_allocation",
        "label": f"All voters allocated to booths ({allocated_percentage}%)",
        "passed": total_enrolled_voters > 0 and unallocated_count == 0,
    }

    # Validation Column 2
    all_paired = len(booths) > 0 and all(
        b.devices.filter(device_type=DeviceType.OFFICER).exists() and
        b.devices.filter(device_type=DeviceType.KIOSK).exists()
        for b in booths
    )
    c2_paired = {
        "id": "c2_paired",
        "label": "All booths paired with devices",
        "passed": all_paired,
    }

    online_officers = sum(1 for b in booth_review_data if b["officer_online"])
    c2_officers = {
        "id": "c2_officers",
        "label": f"Officer stations online ({online_officers}/{len(booths)})",
        "passed": len(booths) > 0 and online_officers == len(booths),
        "is_warning": len(booths) > 0 and online_officers < len(booths),
    }

    online_kiosks = sum(1 for b in booth_review_data if b["kiosk_online"])
    c2_kiosks = {
        "id": "c2_kiosks",
        "label": f"Kiosk devices online ({online_kiosks}/{len(booths)})",
        "passed": len(booths) > 0 and online_kiosks == len(booths),
        "is_warning": len(booths) > 0 and online_kiosks < len(booths),
    }

    has_duplicates = False
    for p in positions:
        names = [c.name.strip().lower() for c in p.candidates.all()]
        if len(names) != len(set(names)):
            has_duplicates = True
            break
    c2_duplicates = {
        "id": "c2_duplicates",
        "label": "No duplicate candidates",
        "passed": not has_duplicates,
    }

    critical_passed = (
        c1_details["passed"] and
        c1_voters["passed"] and
        c1_positions["passed"] and
        c1_candidates["passed"] and
        c1_contested["passed"] and
        c1_booths["passed"] and
        c1_allocation["passed"] and
        c2_paired["passed"] and
        c2_duplicates["passed"]
    )

    c2_validation = {
        "id": "c2_validation",
        "label": "Validation passed" if critical_passed else "Validation issues found",
        "passed": critical_passed,
    }

    ready_to_start = critical_passed and c2_officers["passed"] and c2_kiosks["passed"]
    c2_ready = {
        "id": "c2_ready",
        "label": "Ready to start election" if ready_to_start else ("Pending station logins" if critical_passed else "Configuration incomplete"),
        "passed": ready_to_start,
        "is_warning": critical_passed and not ready_to_start,
    }

    validation_col1 = [c1_details, c1_voters, c1_positions, c1_candidates, c1_contested, c1_booths, c1_allocation]
    validation_col2 = [c2_paired, c2_officers, c2_kiosks, c2_duplicates, c2_validation, c2_ready]
    all_validations_ok = all(item["passed"] for item in validation_col1 + validation_col2)

    if ready_to_start:
        summary_text = "All validation checks passed • Ready to start"
        summary_type = "success"
    elif critical_passed:
        summary_text = "Validation passed • Station logins pending"
        summary_type = "warning"
    else:
        summary_text = "Setup requirements incomplete"
        summary_type = "error"

    return {
        "election_id": election.id,
        "total_enrolled_voters": total_enrolled_voters,
        "total_booths": len(booths),
        "total_allocated": total_allocated,
        "unallocated_count": unallocated_count,
        "allocated_percentage": allocated_percentage,
        "booth_review_data": booth_review_data,
        "validation_col1": validation_col1,
        "validation_col2": validation_col2,
        "critical_passed": critical_passed,
        "ready_to_start": ready_to_start,
        "all_validations_ok": all_validations_ok,
        "summary_text": summary_text,
        "summary_type": summary_type,
    }

