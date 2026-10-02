"""Views for elections application.

Owns:
- Admin election dashboard (matching references/homepage_ref.jpg layout and landing typography)
- Election configuration and lifecycle transitions (start, close, publish results)
- Ballot positions and candidate management
- Configuration freeze error handling
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache

from accounts.permissions import admin_required
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
    update_election,
)


@never_cache
@admin_required
def dashboard_view(request):
    """Primary administrator election management dashboard."""
    all_elections = list_elections()
    
    # Determine selected election
    election_id = request.GET.get("election_id")
    current_election = None
    if election_id:
        try:
            current_election = get_election(int(election_id))
        except (ValueError, TypeError):
            current_election = None

    if not current_election:
        current_election = get_active_election() or all_elections.first()

    # Check for automatic expiry if active
    if current_election and current_election.is_active:
        if close_if_expired(election_id=current_election.id):
            current_election.refresh_from_db()

    details = get_election_details(current_election.id) if current_election else None

    return render(request, "elections/dashboard.html", {
        "all_elections": all_elections,
        "election": current_election,
        "details": details,
        "active_nav": "leaderboard",  # matched to sidebar
    })


@never_cache
@admin_required
def election_create_view(request):
    """Create a new draft election for the installation."""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()
        starts_at_str = request.POST.get("starts_at", "").strip()
        ends_at_str = request.POST.get("ends_at", "").strip()

        starts_at = parse_datetime(starts_at_str)
        ends_at = parse_datetime(ends_at_str)

        if not name or not starts_at or not ends_at:
            messages.error(request, "Name, start time, and cutoff time are required.")
            return redirect("elections:dashboard")

        if timezone.is_naive(starts_at):
            starts_at = timezone.make_aware(starts_at)
        if timezone.is_naive(ends_at):
            ends_at = timezone.make_aware(ends_at)

        try:
            election = create_election(
                name=name,
                starts_at=starts_at,
                ends_at=ends_at,
                description=description,
            )
            messages.success(request, f"Election '{election.name}' created as Draft.")
            return redirect(f"/elections/?election_id={election.id}")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Creation failed: {msg}")
            return redirect("elections:dashboard")

    return redirect("elections:dashboard")


@never_cache
@admin_required
def election_edit_view(request, election_id: int):
    """Edit draft election properties."""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()
        starts_at_str = request.POST.get("starts_at", "").strip()
        ends_at_str = request.POST.get("ends_at", "").strip()

        starts_at = parse_datetime(starts_at_str)
        ends_at = parse_datetime(ends_at_str)

        if not name or not starts_at or not ends_at:
            messages.error(request, "Title, start time, and cutoff time are required.")
            return redirect(f"/elections/?election_id={election_id}")

        if timezone.is_naive(starts_at):
            starts_at = timezone.make_aware(starts_at)
        if timezone.is_naive(ends_at):
            ends_at = timezone.make_aware(ends_at)

        try:
            election = update_election(
                election_id=election_id,
                name=name,
                starts_at=starts_at,
                ends_at=ends_at,
                description=description,
            )
            messages.success(request, f"Election '{election.name}' updated successfully.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Update failed: {msg}")

    return redirect(f"/elections/?election_id={election_id}")


@never_cache
@admin_required
def election_delete_view(request, election_id: int):
    """Delete a draft election."""
    if request.method == "POST":
        try:
            delete_election(election_id=election_id)
            messages.success(request, "Election deleted.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Delete failed: {msg}")
    return redirect("elections:dashboard")


@never_cache
@admin_required
def election_start_view(request, election_id: int):
    """Atomically start the election (DRAFT -> ACTIVE)."""
    if request.method == "POST":
        try:
            election = start_election(election_id=election_id)
            messages.success(request, f"Election '{election.name}' is now ACTIVE. Configuration is frozen.")
        except ValidationError as exc:
            if hasattr(exc, "message_dict") and "configuration" in exc.message_dict:
                errors = exc.message_dict["configuration"]
                messages.error(request, f"Cannot start election: {' '.join(errors)}")
            else:
                msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
                messages.error(request, f"Cannot start election: {msg}")
    return redirect(f"/elections/?election_id={election_id}")


@never_cache
@admin_required
def election_close_view(request, election_id: int):
    """Atomically close the election (ACTIVE -> CLOSED)."""
    if request.method == "POST":
        try:
            election = close_election(election_id=election_id)
            messages.success(request, f"Election '{election.name}' is now CLOSED.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Cannot close election: {msg}")
    return redirect(f"/elections/?election_id={election_id}")


@never_cache
@admin_required
def election_publish_results_view(request, election_id: int):
    """Atomically publish results (CLOSED -> RESULTS_PUBLISHED)."""
    if request.method == "POST":
        try:
            election = publish_results(election_id=election_id)
            messages.success(request, f"Results for '{election.name}' are now PUBLISHED.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Cannot publish results: {msg}")
    return redirect(f"/elections/?election_id={election_id}")


@never_cache
@admin_required
def position_create_view(request, election_id: int):
    """Add a ballot position to a draft election."""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        display_order_str = request.POST.get("display_order", "1").strip()
        try:
            display_order = int(display_order_str)
        except ValueError:
            display_order = 1

        if not name:
            messages.error(request, "Position name is required.")
            return redirect(f"/elections/?election_id={election_id}")

        try:
            create_position(election_id=election_id, name=name, display_order=display_order)
            messages.success(request, f"Position '{name}' added.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Could not add position: {msg}")

    return redirect(f"/elections/?election_id={election_id}")


@never_cache
@admin_required
def position_delete_view(request, position_id: int):
    """Delete a position from a draft election."""
    position = get_object_or_404(Position, id=position_id)
    election_id = position.election_id
    if request.method == "POST":
        try:
            delete_position(position_id=position_id)
            messages.success(request, f"Position '{position.name}' deleted.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Could not delete position: {msg}")
    return redirect(f"/elections/?election_id={election_id}")


@never_cache
@admin_required
def candidate_create_view(request, position_id: int):
    """Register a candidate contesting a position."""
    position = get_object_or_404(Position, id=position_id)
    election_id = position.election_id
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        academic_group = request.POST.get("academic_group", "").strip()
        symbol = request.POST.get("symbol", "").strip()
        photo = request.FILES.get("photo")

        if not name:
            messages.error(request, "Candidate name is required.")
            return redirect(f"/elections/?election_id={election_id}")

        try:
            create_candidate(
                position_id=position_id,
                name=name,
                academic_group=academic_group,
                symbol=symbol,
                photo=photo,
            )
            messages.success(request, f"Candidate '{name}' registered.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Could not register candidate: {msg}")

    return redirect(f"/elections/?election_id={election_id}")


@never_cache
@admin_required
def candidate_delete_view(request, candidate_id: int):
    """Remove a candidate contesting a position."""
    candidate = get_object_or_404(Candidate, id=candidate_id)
    election_id = candidate.position.election_id
    if request.method == "POST":
        try:
            delete_candidate(candidate_id=candidate_id)
            messages.success(request, f"Candidate '{candidate.name}' removed.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Could not remove candidate: {msg}")
    return redirect(f"/elections/?election_id={election_id}")
