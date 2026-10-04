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
    list_election_history,
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

    # Hub statistics for Authenticated Home (references/03_home.png)
    from voters.models import AcademicGroup, Voter
    total_voters = Voter.objects.count()
    total_groups = AcademicGroup.objects.count()
    total_elections = all_elections.count()
    completed_elections = all_elections.filter(
        status__in=[ElectionStatus.CLOSED, ElectionStatus.RESULTS_PUBLISHED]
    ).count()
    active_election = get_active_election()

    return render(request, "elections/home.html", {
        "all_elections": all_elections,
        "election": current_election,
        "details": details,
        "active_nav": "election_workspace",
        "total_voters": total_voters,
        "total_groups": total_groups,
        "total_elections": total_elections,
        "completed_elections": completed_elections,
        "active_election": active_election,
    })


@never_cache
@admin_required
def election_history_view(request):
    """View and manage all past elections archive (references/05_election_history.png)."""
    search_query = request.GET.get("q", "").strip()
    year_filter = request.GET.get("year", "").strip()

    history_data = list_election_history(search_query=search_query, year_filter=year_filter)

    return render(request, "elections/history.html", {
        "history_items": history_data["items"],
        "available_years": history_data["available_years"],
        "total_past_elections": history_data["total_past_elections"],
        "search_query": search_query,
        "selected_year": year_filter,
        "has_filters": bool(search_query or year_filter),
        "active_nav": "election_history",
    })


@never_cache
@admin_required
def election_create_view(request):
    """Create a new draft election for the installation."""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()

        if not name:
            messages.error(request, "Election title is required.")
            return redirect("elections:dashboard")

        try:
            election = create_election(
                name=name,
                description=description,
            )
            messages.success(request, f"Election '{election.name}' created as Draft. Please enroll or import voters.")
            return redirect("elections:voters", election_id=election.id)
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
        ends_at_str = request.POST.get("ends_at", "").strip()

        if not name:
            messages.error(request, "Election title is required.")
            return redirect(f"/elections/?election_id={election_id}")

        ends_at = parse_datetime(ends_at_str) if ends_at_str else None
        if ends_at and timezone.is_naive(ends_at):
            ends_at = timezone.make_aware(ends_at)

        try:
            election = update_election(
                election_id=election_id,
                name=name,
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
    """Atomically start the election (DRAFT -> ACTIVE).
    
    Start time is automatically set to now, and voting cutoff time is validated.
    """
    if request.method == "POST":
        ends_at_str = request.POST.get("ends_at", "").strip()
        ends_at = parse_datetime(ends_at_str) if ends_at_str else None
        if ends_at and timezone.is_naive(ends_at):
            ends_at = timezone.make_aware(ends_at)

        try:
            election = start_election(election_id=election_id, ends_at=ends_at)
            messages.success(request, f"Election '{election.name}' is now ACTIVE. Configuration is frozen and stations are live.")
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
            return redirect("elections:results", election_id=election.id)
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Cannot publish results: {msg}")
    return redirect("elections:results", election_id=election_id)


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
        voter_id_raw = request.POST.get("voter_id", "").strip()
        voter_id = int(voter_id_raw) if voter_id_raw.isdigit() else None

        if not name and not voter_id:
            messages.error(request, "Candidate name or selected voter is required.")
            return redirect(f"/elections/?election_id={election_id}")

        try:
            cand = create_candidate(
                position_id=position_id,
                name=name,
                academic_group=academic_group,
                symbol=symbol,
                photo=photo,
                voter_id=voter_id,
            )
            messages.success(request, f"Candidate '{cand.name}' registered.")
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


@never_cache
@admin_required
def election_voters_view(request, election_id: int):
    """View and manage enrolled voters for a specific election."""
    from django.db.models import Q
    from voters.models import AcademicGroup, ElectionVoter, Voter
    from voters.selectors import list_academic_groups

    election = get_object_or_404(Election, id=election_id)
    search_query = request.GET.get("q", "").strip()
    group_id_str = request.GET.get("group", "").strip()
    booth_id_str = request.GET.get("booth", "").strip()

    group_id = int(group_id_str) if group_id_str.isdigit() else None
    booth_id = int(booth_id_str) if booth_id_str.isdigit() else None

    qs = election.election_voters.select_related("voter", "voter__academic_group", "booth").order_by("voter__primary_registry_value")
    if search_query:
        qs = qs.filter(
            Q(voter__primary_registry_value__icontains=search_query) |
            Q(voter__name__icontains=search_query)
        )
    if group_id:
        qs = qs.filter(voter__academic_group_id=group_id)
    if booth_id_str == "unallocated":
        qs = qs.filter(booth__isnull=True)
    elif booth_id:
        qs = qs.filter(booth_id=booth_id)

    total_enrolled = election.election_voters.count()
    unallocated_count = election.election_voters.filter(booth__isnull=True).count()
    academic_groups = list_academic_groups()
    booths = election.booths.all()

    # Registry voters available to enroll (excluding already enrolled)
    available_voters = Voter.objects.exclude(
        election_enrollments__election=election
    ).select_related("academic_group").order_by("primary_registry_value")[:100]

    return render(request, "elections/voters.html", {
        "election": election,
        "enrolled_voters": qs[:200],
        "total_results": qs.count(),
        "total_enrolled": total_enrolled,
        "unallocated_count": unallocated_count,
        "academic_groups": academic_groups,
        "booths": booths,
        "available_voters": available_voters,
        "search_query": search_query,
        "selected_group": group_id,
        "selected_booth": booth_id_str,
        "active_nav": "election_voters",
    })


@never_cache
@admin_required
def election_enroll_voters_view(request, election_id: int):
    """Enroll selected voters or an entire academic group from the master registry into this election."""
    from voters.models import Voter
    from voters.services import enroll_voters_in_election

    election = get_object_or_404(Election, id=election_id)
    if request.method == "POST":
        voter_ids_raw = request.POST.getlist("voter_ids")
        voter_ids = [int(v) for v in voter_ids_raw if v.isdigit()]
        enroll_all_group = request.POST.get("enroll_group") == "1"
        group_id_str = request.POST.get("group_id", "").strip()

        if enroll_all_group and group_id_str.isdigit():
            voter_ids = list(Voter.objects.filter(academic_group_id=int(group_id_str)).values_list("id", flat=True))

        if not voter_ids:
            messages.error(request, "No voters selected for enrollment.")
            return redirect("elections:voters", election_id=election.id)

        try:
            count = enroll_voters_in_election(election_id=election.id, voter_ids=voter_ids)
            messages.success(request, f"Successfully enrolled {count} voter(s) into '{election.name}'.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Enrollment failed: {msg}")

    return redirect("elections:voters", election_id=election.id)


@never_cache
@admin_required
def election_remove_voter_view(request, election_id: int, voter_id: int):
    """Remove a voter from election enrollment (DRAFT only)."""
    from voters.services import remove_voter_from_election

    if request.method == "POST":
        try:
            remove_voter_from_election(election_id=election_id, voter_id=voter_id)
            messages.success(request, "Voter removed from election enrollment.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Could not remove voter: {msg}")

    return redirect("elections:voters", election_id=election_id)


@never_cache
@admin_required
def election_import_voters_view(request, election_id: int):
    """Directly import a spreadsheet roster into this election (also stores in master registry)."""
    from voters.importers import parse_csv_content, parse_excel_content, process_voter_import

    election = get_object_or_404(Election, id=election_id)
    if request.method == "POST":
        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            messages.error(request, "Please select a CSV or Excel file to upload.")
            return redirect("elections:voters", election_id=election.id)

        filename = uploaded_file.name.lower()
        try:
            if filename.endswith(".csv"):
                headers, data_rows = parse_csv_content(uploaded_file)
            elif filename.endswith((".xlsx", ".xls")):
                headers, data_rows = parse_excel_content(uploaded_file)
            else:
                messages.error(request, "Unsupported file format. Please upload a .csv or .xlsx file.")
                return redirect("elections:voters", election_id=election.id)

            result = process_voter_import(
                headers=headers,
                data_rows=data_rows,
                dry_run=False,
                election_id=election.id,
            )
            messages.success(
                request,
                f"Import complete: {result.created_count + result.updated_count} voters added/updated in registry and enrolled into this election."
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Import validation error: {msg}")
        except Exception as exc:
            messages.error(request, f"Failed to process file: {str(exc)}")

    return redirect("elections:voters", election_id=election.id)


@never_cache
@admin_required
def election_booths_view(request, election_id: int):
    """Booth management and voter allocation dashboard for an election."""
    from accounts.selectors import get_active_device_session
    from voters.models import AcademicGroup, ElectionVoter
    from voters.selectors import list_election_booths

    election = get_object_or_404(Election, id=election_id)
    raw_booths = list_election_booths(election.id)

    booth_data = []
    online_count = 0
    total_devices = 0

    for b in raw_booths:
        officer = b.devices.filter(device_type="OFFICER").first()
        kiosk = b.devices.filter(device_type="KIOSK").first()

        officer_active = bool(officer and get_active_device_session(officer.id))
        kiosk_active = bool(kiosk and get_active_device_session(kiosk.id))

        if officer:
            total_devices += 1
            if officer_active:
                online_count += 1
        if kiosk:
            total_devices += 1
            if kiosk_active:
                online_count += 1

        booth_data.append({
            "booth": b,
            "officer_device": officer,
            "officer_active": officer_active,
            "kiosk_device": kiosk,
            "kiosk_active": kiosk_active,
            "allocated_count": b.allocated_count,
        })

    enrolled_count = election.election_voters.count()
    unallocated_count = election.election_voters.filter(booth__isnull=True).count()
    academic_groups = AcademicGroup.objects.all().order_by("name")

    new_credentials = request.session.pop("new_booth_credentials", None)
    rotated_credentials = request.session.pop("rotated_device_credentials", None)

    return render(request, "elections/booths.html", {
        "election": election,
        "booth_data": booth_data,
        "total_booths": len(booth_data),
        "enrolled_count": enrolled_count,
        "unallocated_count": unallocated_count,
        "online_count": online_count,
        "total_devices": total_devices,
        "academic_groups": academic_groups,
        "new_credentials": new_credentials,
        "rotated_credentials": rotated_credentials,
        "active_nav": "devices",
    })


@never_cache
@admin_required
def election_booth_create_view(request, election_id: int):
    """Create a new booth in draft election and generate station credentials."""
    from voters.services import create_booth

    election = get_object_or_404(Election, id=election_id)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        try:
            res = create_booth(election_id=election.id, name=name)
            request.session["new_booth_credentials"] = {
                "booth_number": res["booth"].booth_number,
                "booth_name": res["booth"].name,
                "officer_username": res["officer_username"],
                "officer_password": res["officer_password"],
                "kiosk_username": res["kiosk_username"],
                "kiosk_password": res["kiosk_password"],
            }
            messages.success(request, f"Booth {res['booth'].booth_number} created with paired station credentials.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Could not create booth: {msg}")

    return redirect("elections:booths", election_id=election.id)


@never_cache
@admin_required
def election_booth_delete_view(request, election_id: int, booth_id: int):
    """Delete a polling booth (DRAFT only)."""
    from voters.services import remove_booth

    election = get_object_or_404(Election, id=election_id)
    if request.method == "POST":
        try:
            remove_booth(booth_id=booth_id)
            messages.success(request, "Booth removed.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Could not remove booth: {msg}")

    return redirect("elections:booths", election_id=election.id)


@never_cache
@admin_required
def election_booth_allocate_view(request, election_id: int, booth_id: int):
    """Allocate voters to a booth by academic group or unallocated batch."""
    from voters.services import allocate_voters_to_booth

    election = get_object_or_404(Election, id=election_id)
    if request.method == "POST":
        academic_group_id_str = request.POST.get("academic_group_id", "").strip()
        academic_group_id = int(academic_group_id_str) if academic_group_id_str else None
        allocate_all = request.POST.get("allocate_all_unallocated") == "1"

        try:
            count = allocate_voters_to_booth(
                election_id=election.id,
                booth_id=booth_id,
                academic_group_id=academic_group_id,
                allocate_all_unallocated=allocate_all,
            )
            messages.success(request, f"Allocated {count} voter(s) to Booth.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Allocation failed: {msg}")

    return redirect("elections:booths", election_id=election.id)


@never_cache
@admin_required
def election_booths_auto_distribute_view(request, election_id: int):
    """Evenly balance unallocated voters across booths."""
    from voters.services import auto_distribute_unallocated_voters

    election = get_object_or_404(Election, id=election_id)
    if request.method == "POST":
        try:
            res = auto_distribute_unallocated_voters(election_id=election.id)
            messages.success(request, f"Balanced {res['distributed']} unallocated voter(s) across booths.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Distribution failed: {msg}")

    return redirect("elections:booths", election_id=election.id)


@never_cache
@admin_required
def election_booth_roster_export_view(request, election_id: int, booth_id: int):
    """Printable sign-in roster for polling station officers."""
    from voters.models import Booth, ElectionVoter

    election = get_object_or_404(Election, id=election_id)
    booth = get_object_or_404(Booth, id=booth_id, election=election)
    voters = ElectionVoter.objects.filter(
        election=election,
        booth=booth
    ).select_related("voter", "voter__academic_group").order_by("voter__primary_registry_value")

    return render(request, "elections/booth_roster.html", {
        "election": election,
        "booth": booth,
        "voters": voters,
        "total_allocated": voters.count(),
    })


@never_cache
@admin_required
def device_rotate_credentials_view(request, device_id: int):
    """Rotate credentials for an officer or kiosk station device."""
    from voters.services import rotate_booth_device_credentials

    if request.method == "POST":
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/elections/"
        try:
            device, new_pw = rotate_booth_device_credentials(device_id=device_id)
            request.session["rotated_device_credentials"] = {
                "identifier": device.identifier,
                "password": new_pw,
            }
            messages.success(request, f"Credentials rotated for {device.identifier}.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Rotation failed: {msg}")

        return redirect(next_url)

    return redirect("elections:dashboard")


@never_cache
@admin_required
def election_results_view(request, election_id: int):
    """Dedicated election results and official turnout dashboard."""
    from elections.selectors import get_election_positions
    from django.db import connection

    election = get_object_or_404(Election, id=election_id)
    enrolled_count = election.election_voters.count()
    ballots_cast = election.election_voters.filter(has_voted=True).count()
    turnout_pct = round((ballots_cast / enrolled_count * 100), 1) if enrolled_count > 0 else 0.0

    turnout = {
        "enrolled_count": enrolled_count,
        "ballots_cast": ballots_cast,
        "turnout_pct": turnout_pct,
    }

    # Position-by-position results
    positions = list(get_election_positions(election.id))
    position_results = []

    has_vote_table = False
    try:
        has_vote_table = "voting_vote" in connection.introspection.table_names()
    except Exception:
        has_vote_table = False

    for pos in positions:
        cand_list = []
        total_pos_votes = 0
        for cand in pos.candidates.all():
            cand_votes = 0
            if has_vote_table and (election.is_closed or election.is_results_published):
                try:
                    from voting.models import Vote
                    cand_votes = Vote.objects.filter(candidate=cand).count()
                except Exception:
                    cand_votes = 0
            total_pos_votes += cand_votes
            cand_list.append({
                "candidate": cand,
                "votes": cand_votes,
            })

        for c in cand_list:
            c["percentage"] = round((c["votes"] / total_pos_votes * 100), 1) if total_pos_votes > 0 else 0.0

        cand_list.sort(key=lambda x: x["votes"], reverse=True)

        position_results.append({
            "position": pos,
            "candidates": cand_list,
            "total_votes": total_pos_votes,
        })

    return render(request, "elections/results.html", {
        "election": election,
        "turnout": turnout,
        "position_results": position_results,
        "active_nav": "results",
    })



