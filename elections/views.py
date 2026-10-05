"""Views for elections application.

Owns:
- Admin election dashboard (matching references/homepage_ref.jpg layout and landing typography)
- Election configuration and lifecycle transitions (start, close, publish results)
- Ballot positions and candidate management
- Configuration freeze error handling
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from accounts.models import CredentialStatus, Device, DeviceType
from accounts.permissions import admin_required
from elections.models import Candidate, Election, ElectionStatus, Position
from elections.reports import generate_voter_slips_pdf
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
from accounts.models import DeviceSession
from accounts.services import cleanup_past_credentials, revoke_device_credentials, rotate_device_credentials
import re
from voters.importers import (
    parse_csv_content,
    parse_excel_content,
    process_voter_import,
)
from voters.models import AcademicGroup, AcademicGroupType, Booth, ElectionVoter, Voter, VoterRegistry
from voters.selectors import get_voter_registry, list_voter_registries
from voters.services import (
    auto_distribute_unallocated_voters,
    create_booth,
    create_voter_registry,
    customize_booth_allocation,
    deallocate_voters_from_booth,
    enroll_voters_in_election,
    rebalance_election_voters,
    remove_booth,
    update_booth,
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
    total_voters = Voter.objects.filter(registry__isnull=False).count()
    total_groups = AcademicGroup.objects.filter(registry__isnull=False).count()
    total_elections = all_elections.count()
    completed_elections_qs = all_elections.filter(
        status__in=[ElectionStatus.CLOSED, ElectionStatus.RESULTS_PUBLISHED]
    )
    completed_elections = completed_elections_qs.count()
    total_votes_registered = ElectionVoter.objects.filter(
        election__in=completed_elections_qs,
        has_voted=True,
    ).count()
    active_election = get_active_election()
    # Prompt on Home only if an election draft was explicitly saved as draft just before
    active_draft_election = all_elections.filter(status=ElectionStatus.DRAFT, is_saved_draft=True).order_by("-created_at").first()

    return render(request, "elections/home.html", {
        "all_elections": all_elections,
        "election": current_election,
        "details": details,
        "active_nav": "election_workspace",
        "total_voters": total_voters,
        "total_groups": total_groups,
        "total_elections": total_elections,
        "completed_elections": completed_elections,
        "total_votes_registered": total_votes_registered,
        "active_election": active_election,
        "active_draft_election": active_draft_election,
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


# ==========================================================================
# 4-Stage Election Setup Flow (references/06.1 to 06.4)
# ==========================================================================

@never_cache
@admin_required
def election_setup_start_view(request):
    """Entry point when clicking 'Start election' on the home page.
    
    - If an active election exists: rejects with error (only 1 active election allowed at a time).
    - If discard=1 requested: discards any existing draft and starts fresh.
    - If a draft was explicitly saved before (is_saved_draft=True): resumes that draft.
    - If an un-saved working draft exists (is_saved_draft=False): silently cleans it up to start fresh.
    - Creates a new un-saved draft election with setup_stage=1 and redirects to Stage 1.
    """
    active_election = get_active_election()
    if active_election:
        messages.error(
            request,
            f"Cannot start a new election setup while '{active_election.name}' is currently ACTIVE. "
            "Please close the active election before creating a new one."
        )
        return redirect("elections:dashboard")

    discard = request.GET.get("discard") == "1" or request.POST.get("discard") == "1"
    existing_draft = Election.objects.filter(status=ElectionStatus.DRAFT).order_by("-created_at").first()

    if existing_draft:
        if discard or not existing_draft.is_saved_draft:
            existing_draft.delete()
            existing_draft = None
        else:
            return redirect(existing_draft.stage_url_name, election_id=existing_draft.id)

    # Create new fresh draft (unsaved until user explicitly clicks Save draft)
    election = create_election(name="New Election")
    election.setup_stage = 1
    election.is_saved_draft = False
    election.save(update_fields=["setup_stage", "is_saved_draft"])
    return redirect("elections:setup_voters", election_id=election.id)


@never_cache
@admin_required
def election_setup_resume_view(request, election_id: int):
    """Resume an existing draft election at its saved exit point."""
    election = get_object_or_404(Election, id=election_id, status=ElectionStatus.DRAFT)
    stage_views = {
        1: "elections:setup_voters",
        2: "elections:setup_details",
        3: "elections:setup_booths",
        4: "elections:setup_review",
    }
    target = stage_views.get(election.setup_stage, "elections:setup_voters")
    return redirect(target, election_id=election.id)


@never_cache
@admin_required
@require_http_methods(["POST"])
def election_setup_save_draft_view(request, election_id: int):
    """Save draft throughout the workflow and redirect to Home."""
    election = get_object_or_404(Election, id=election_id, status=ElectionStatus.DRAFT)
    
    stage = request.POST.get("setup_stage")
    if stage and stage.isdigit():
        election.setup_stage = int(stage)

    registry_id = request.POST.get("registry_id")
    if registry_id and registry_id.isdigit():
        reg = VoterRegistry.objects.filter(id=int(registry_id)).first()
        if reg:
            election.voter_registry = reg

    election.is_saved_draft = True
    election.save()
    messages.success(request, f"Election draft '{election.name}' saved. You can resume setup at any time.")
    return redirect("elections:dashboard")


@never_cache
@admin_required
@require_http_methods(["GET", "POST"])
def election_setup_discard_view(request, election_id: int):
    """Discard an ongoing election setup and redirect to Home."""
    election = get_object_or_404(Election, id=election_id, status=ElectionStatus.DRAFT)
    election.delete()
    messages.info(request, "Election setup discarded.")
    return redirect("elections:dashboard")


@never_cache
@admin_required
def election_setup_voters_view(request, election_id: int):
    """Stage 1: Election Voters (references/06.1_voter_list.png).
    
    Displays two voter source options:
    1. From master registry: select existing registry to preview groups & count.
    2. Import directly: upload CSV/Excel, configure column mapping, and preview groups & count.
    
    Submitting with selected registry enrolls all its voters into ElectionVoter and advances to Stage 2.
    """
    election = get_object_or_404(Election, id=election_id)
    if not election.is_draft:
        messages.error(request, "This election is no longer in draft setup.")
        return redirect("elections:results", election_id=election.id)

    if request.method == "POST":
        action = request.POST.get("action", "continue")
        registry_id_str = request.POST.get("registry_id", "").strip()
        registry_id = int(registry_id_str) if registry_id_str.isdigit() else None

        if action == "save_draft":
            if registry_id:
                reg = VoterRegistry.objects.filter(id=registry_id).first()
                if reg:
                    election.voter_registry = reg
            election.setup_stage = 1
            election.is_saved_draft = True
            election.save()
            messages.success(request, "Draft saved at Stage 1 (Election Voters).")
            return redirect("elections:dashboard")

        if action == "discard":
            election.delete()
            messages.info(request, "Election setup discarded.")
            return redirect("elections:dashboard")

        if action == "direct_import":
            name = request.POST.get("name", "").strip()
            primary_id_source = request.POST.get("primary_id_source", "").strip()
            name_source = request.POST.get("name_source", "").strip()
            group_source = request.POST.get("group_source", "").strip()
            subgroup_source = request.POST.get("subgroup_source", "").strip()
            gender_source = request.POST.get("gender_source", "").strip()
            has_subgroups_toggle = request.POST.get("has_subgroups_toggle", "yes").strip().lower()

            direct_form_data = {
                "name": name,
                "primary_id_source": primary_id_source,
                "name_source": name_source,
                "group_source": group_source,
                "subgroup_source": subgroup_source,
                "gender_source": gender_source,
                "has_subgroups_toggle": has_subgroups_toggle,
            }

            uploaded_file = request.FILES.get("file")
            headers = None
            data_rows = None

            def _handle_direct_error(err_msg: str):
                messages.error(request, err_msg)
                request.session["direct_form_data"] = direct_form_data
                return redirect(f"{reverse('elections:setup_voters', kwargs={'election_id': election.id})}?mode=direct")

            if uploaded_file:
                fname_lower = uploaded_file.name.lower()
                try:
                    if fname_lower.endswith(".csv"):
                        headers, data_rows = parse_csv_content(uploaded_file)
                    elif fname_lower.endswith((".xlsx", ".xls")):
                        headers, data_rows = parse_excel_content(uploaded_file)
                    else:
                        return _handle_direct_error("Please upload a valid CSV (.csv) or Excel (.xlsx, .xls) file.")
                except Exception as exc:
                    return _handle_direct_error(f"Failed to read file: {str(exc)}")

                size_bytes = uploaded_file.size
                if size_bytes < 1024:
                    filesize_str = f"{size_bytes} B"
                elif size_bytes < 1024 * 1024:
                    filesize_str = f"{round(size_bytes / 1024)} KB"
                else:
                    filesize_str = f"{round(size_bytes / (1024 * 1024), 1)} MB"

                request.session["pending_file"] = {
                    "headers": headers,
                    "data_rows": data_rows,
                    "filename": uploaded_file.name,
                    "filesize": filesize_str,
                }
            elif "pending_file" in request.session:
                pending_f = request.session["pending_file"]
                headers = pending_f.get("headers")
                data_rows = pending_f.get("data_rows")

            if not headers or not data_rows:
                return _handle_direct_error("Please upload a CSV or Excel file containing voter records.")

            if not name:
                return _handle_direct_error("Please enter a name for the voter registry.")

            if not primary_id_source or not name_source or not group_source:
                return _handle_direct_error("Please map all required columns: Student ID, Name, and Department.")

            clean_subgroup = "" if has_subgroups_toggle == "no" else (subgroup_source if subgroup_source and subgroup_source.lower() not in ["(none)", "none", "—", "-"] else "")

            try:
                with transaction.atomic():
                    reg = create_voter_registry(
                        name=name,
                        primary_id_source=primary_id_source,
                        name_source=name_source,
                        group_source=group_source or "Group",
                        subgroup_source=clean_subgroup,
                        gender_source=gender_source or "Gender",
                    )
                    subgroup_col = clean_subgroup if clean_subgroup else None
                    group_col = group_source if group_source and group_source != "(None)" else None
                    gender_col = gender_source if gender_source and gender_source != "(None)" else None

                    import_result = process_voter_import(
                        headers=headers,
                        data_rows=data_rows,
                        registry_id=reg.id,
                        dry_run=False,
                        id_col=primary_id_source,
                        name_col=name_source,
                        group_col=group_col,
                        subgroup_col=subgroup_col,
                        gender_col=gender_col,
                    )
                    if import_result.has_errors:
                        raise ValidationError(import_result.errors[0]['errors'][0])

                    election.voter_registry = reg
                    election.save(update_fields=["voter_registry"])

                request.session.pop("pending_file", None)
                request.session.pop("direct_form_data", None)
                messages.success(request, f"Created registry '{reg.name}' with {import_result.created_count} voters imported.")
                return redirect(f"{reverse('elections:setup_voters', kwargs={'election_id': election.id})}?preview={reg.id}&mode=direct")
            except ValidationError as exc:
                msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
                return _handle_direct_error(f"Import failed: {msg}")
            except Exception as exc:
                return _handle_direct_error(f"Import error: {str(exc)}")

        # Action: continue
        if not registry_id:
            messages.error(request, "Please select a voter registry to continue.")
            return redirect(f"{reverse('elections:setup_voters', kwargs={'election_id': election.id})}")

        registry = get_object_or_404(VoterRegistry, id=registry_id)
        
        # Link registry to election
        election.voter_registry = registry
        election.setup_stage = 2
        election.save(update_fields=["voter_registry", "setup_stage"])

        # Reset any prior voter enrollments if user changed selection
        ElectionVoter.objects.filter(election=election).delete()

        # Enroll all voters from this registry into ElectionVoter
        voter_ids = list(Voter.objects.filter(registry=registry).values_list("id", flat=True))
        if voter_ids:
            enroll_voters_in_election(election_id=election.id, voter_ids=voter_ids)

        messages.success(request, f"Enrolled {len(voter_ids)} voter(s) from '{registry.name}'. Continue with election details.")
        return redirect("elections:setup_details", election_id=election.id)

    # GET request
    mode = request.GET.get("mode", "master")
    registries = list_voter_registries()
    
    preview_reg_id = request.GET.get("preview")
    if not preview_reg_id and election.voter_registry_id:
        preview_reg_id = str(election.voter_registry_id)

    preview_registry = None
    preview_groups = []
    total_preview_voters = 0

    if preview_reg_id and preview_reg_id.isdigit():
        preview_registry = get_voter_registry(int(preview_reg_id))
        if preview_registry:
            total_preview_voters = Voter.objects.filter(registry=preview_registry).count()
            # Top-level groups with voter counts
            preview_groups = list(
                AcademicGroup.objects.filter(registry=preview_registry, type=AcademicGroupType.GROUP)
                .annotate(voter_count=Count("voters"))
                .order_by("name")
            )

    pending_file = request.session.get("pending_file")
    direct_form_data = request.session.pop("direct_form_data", None)
    came_from_step_2 = (request.GET.get("from_step") == "2") or (election.setup_stage >= 2)

    return render(request, "elections/setup_voters.html", {
        "election": election,
        "registries": registries,
        "preview_registry": preview_registry,
        "preview_groups": preview_groups,
        "total_preview_voters": total_preview_voters,
        "mode": mode,
        "pending_file": pending_file,
        "direct_form_data": direct_form_data,
        "active_step": 1,
        "came_from_step_2": came_from_step_2,
    })


@never_cache
@admin_required
def election_setup_details_view(request, election_id: int):
    """Stage 2: Election Details & Candidates (references/06.2_election_details.png).
    
    Sections:
    1. Election name & description (no 'Election information' heading)
    2. Positions and candidates (+ Add position button on right)
       - Candidate rows: photo/symbol, student ID, name, group, symbol, red delete button
       - + Add candidate button below each position's candidates
    Header:
    - Back button on left
    - Save as draft & Continue on right
    """
    election = get_object_or_404(Election, id=election_id)
    if not election.is_draft:
        messages.error(request, "This election is no longer in draft setup.")
        return redirect("elections:results", election_id=election.id)

    if request.method == "POST":
        action = request.POST.get("action", "continue")
        name = request.POST.get("name", "").strip() or request.POST.get("current_name", "").strip()
        description = request.POST.get("description", "").strip() or request.POST.get("current_desc", "").strip()

        # Update name and description on election if provided
        if name:
            election.name = name
        if description is not None:
            election.description = description
        election.save(update_fields=["name", "description"])

        if action == "save_draft":
            election.is_saved_draft = True
            election.save(update_fields=["is_saved_draft"])
            messages.success(request, "Election details saved as draft.")
            return redirect("elections:dashboard")

        elif action == "add_position":
            pos_name = request.POST.get("position_name", "").strip()
            eligibility_mode = request.POST.get("eligibility_mode", "all")
            eligible_gender = request.POST.get("eligible_gender", "ALL").strip().upper()
            group_ids = request.POST.getlist("eligible_group_ids")
            
            if not pos_name:
                messages.error(request, "Position name is required.")
                return redirect("elections:setup_details", election_id=election.id)

            selected_group_ids = [int(gid) for gid in group_ids if gid.isdigit()] if eligibility_mode != "all" else []
            try:
                create_position(
                    election_id=election.id,
                    name=pos_name,
                    eligible_group_ids=selected_group_ids if selected_group_ids else None,
                    eligible_gender=eligible_gender if eligible_gender in ['FEMALE', 'MALE'] else 'ALL',
                )
                messages.success(request, f"Position '{pos_name}' added successfully.")
            except ValidationError as exc:
                msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
                messages.error(request, f"Could not add position: {msg}")
            return redirect("elections:setup_details", election_id=election.id)

        elif action == "delete_position":
            pos_id = request.POST.get("position_id")
            if pos_id and pos_id.isdigit():
                try:
                    delete_position(position_id=int(pos_id))
                    messages.success(request, "Position deleted.")
                except ValidationError as exc:
                    msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
                    messages.error(request, f"Could not delete position: {msg}")
            return redirect("elections:setup_details", election_id=election.id)

        elif action == "add_candidate":
            pos_id = request.POST.get("position_id")
            voter_id_raw = request.POST.get("voter_id", "").strip()
            cand_name = request.POST.get("candidate_name", "").strip()
            academic_group = request.POST.get("academic_group", "").strip()
            symbol = request.POST.get("symbol", "").strip()
            photo = request.FILES.get("photo")
            symbol_image = request.FILES.get("symbol_image")

            vid = int(voter_id_raw) if voter_id_raw.isdigit() else None
            if not pos_id or not pos_id.isdigit():
                messages.error(request, "Invalid position specified.")
                return redirect("elections:setup_details", election_id=election.id)

            try:
                create_candidate(
                    position_id=int(pos_id),
                    voter_id=vid,
                    name=cand_name,
                    academic_group=academic_group,
                    symbol=symbol,
                    symbol_image=symbol_image,
                    photo=photo,
                )
                messages.success(request, f"Candidate '{cand_name}' added successfully.")
            except ValidationError as exc:
                msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
                messages.error(request, f"Could not add candidate: {msg}")
            return redirect("elections:setup_details", election_id=election.id)

        elif action == "delete_candidate":
            cand_id = request.POST.get("candidate_id")
            if cand_id and cand_id.isdigit():
                try:
                    delete_candidate(candidate_id=int(cand_id))
                    messages.success(request, "Candidate removed.")
                except ValidationError as exc:
                    msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
                    messages.error(request, f"Could not remove candidate: {msg}")
            return redirect("elections:setup_details", election_id=election.id)

        elif action == "continue":
            if not election.name.strip():
                messages.error(request, "Election name is required.")
                return redirect("elections:setup_details", election_id=election.id)

            positions = election.positions.prefetch_related("candidates").all()
            if not positions.exists():
                messages.error(request, "Please add at least one position to continue.")
                return redirect("elections:setup_details", election_id=election.id)

            empty_positions = [p.name for p in positions if not p.candidates.exists()]
            if empty_positions:
                messages.error(
                    request,
                    f"Every position must have at least one candidate. Missing candidates for: {', '.join(empty_positions)}."
                )
                return redirect("elections:setup_details", election_id=election.id)

            election.setup_stage = 3
            election.save(update_fields=["setup_stage"])
            messages.success(request, "Election details and candidates confirmed.")
            return redirect("elections:setup_booths", election_id=election.id)

    # GET Request: Fetch positions, candidates, and groups
    positions = (
        Position.objects.filter(election=election)
        .prefetch_related("candidates__voter__academic_group", "eligible_groups")
        .order_by("display_order", "id")
    )

    total_enrolled_voters = election.election_voters.count()
    if election.voter_registry:
        available_groups = list(AcademicGroup.objects.filter(registry=election.voter_registry).order_by("name"))
    else:
        group_ids = (
            election.election_voters.filter(voter__academic_group__isnull=False)
            .values_list("voter__academic_group_id", flat=True)
            .distinct()
        )
        available_groups = list(AcademicGroup.objects.filter(id__in=group_ids).order_by("name"))

    for grp in available_groups:
        grp.voter_count = election.election_voters.filter(voter__academic_group=grp).count()

    return render(request, "elections/setup_details.html", {
        "election": election,
        "positions": positions,
        "available_groups": available_groups,
        "total_enrolled_voters": total_enrolled_voters,
        "active_step": 2,
    })


@never_cache
@admin_required
def election_setup_voters_search_view(request, election_id: int):
    """AJAX search endpoint for enrolled voters to be added as candidates."""
    election = get_object_or_404(Election, id=election_id)
    query = request.GET.get("q", "").strip()
    position_id_raw = request.GET.get("position_id", "").strip()

    qs = ElectionVoter.objects.filter(election=election, voter__isnull=False).select_related("voter__academic_group")

    if position_id_raw.isdigit():
        position = Position.objects.filter(id=int(position_id_raw), election=election).first()
        if position:
            if position.eligible_groups.exists():
                qs = qs.filter(voter__academic_group__in=position.eligible_groups.all())
            if position.eligible_gender and position.eligible_gender != "ALL":
                qs = qs.filter(voter__gender__iexact=position.eligible_gender)

    if query:
        qs = qs.filter(
            Q(voter__name__icontains=query) | Q(voter__primary_registry_value__icontains=query)
        )

    results = []
    for ev in qs[:25]:
        voter = ev.voter
        results.append({
            "id": voter.id,
            "identifier": voter.identifier,
            "name": voter.name,
            "group": voter.academic_group.name if voter.academic_group else "",
        })

    return JsonResponse({"results": results})


@never_cache
@admin_required
def election_setup_booths_view(request, election_id: int):
    """Stage 3: Booths & Device Pairing (Faithful vertical slice of references/06.3).
    
    Enforces:
    - Election must be in DRAFT.
    - Adding new booth automatically provisions paired Officer Station & Voting Kiosk devices with random credentials.
    - Sequential naming: Booth 1, 2, 3... with optional custom name/location.
    - Editing custom name only.
    - Deletion of booth disassociates voters, deletes paired devices/accounts, and renumbers remaining booths sequentially (1..N).
    - Rotating device credentials (generates fresh random password, revokes active session, returns new password).
    - Revoking device credentials (marks REVOKED, revokes active session).
    - Saving draft progress.
    - Validation before proceeding: requires at least 1 booth configured.
    """
    election = get_object_or_404(Election, id=election_id, status=ElectionStatus.DRAFT)
    # Automatically remove past credentials that are not in use by any draft or ongoing election
    cleanup_past_credentials()

    if request.method == "POST":
        action = request.POST.get("action", "").strip()

        if action == "add_booth":
            custom_name = request.POST.get("custom_name", "").strip()
            try:
                res = create_booth(election_id=election.id, name=custom_name)
                # Store newly generated plaintext credentials in session for one-time admin display
                request.session["new_booth_credentials"] = {
                    "booth_number": res["booth"].booth_number,
                    "booth_name": res["booth"].name,
                    "officer_username": res["officer_username"],
                    "officer_password": res["officer_password"],
                    "kiosk_username": res["kiosk_username"],
                    "kiosk_password": res["kiosk_password"],
                }
                # If allocations were not explicitly cleared, auto-distribute/rebalance voters across all booths
                if not request.session.get(f"allocation_cleared_{election.id}", False):
                    rebalance_election_voters(election_id=election.id)
                request.session[f"slips_downloaded_{election.id}"] = False
                messages.success(request, f"Booth {res['booth'].booth_number} created with paired station credentials.")
            except ValidationError as exc:
                msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
                messages.error(request, f"Could not create booth: {msg}")
            return redirect("elections:setup_booths", election_id=election.id)

        elif action == "edit_booth":
            booth_id = request.POST.get("booth_id")
            custom_name = request.POST.get("custom_name", "").strip()
            is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("is_ajax") == "1"
            try:
                booth = get_object_or_404(Booth, id=booth_id, election=election)
                booth = update_booth(booth_id=booth.id, name=custom_name)
                if is_ajax:
                    return JsonResponse({
                        "status": "success",
                        "booth_id": booth.id,
                        "booth_number": booth.booth_number,
                        "name": booth.name,
                        "display_name": booth.display_name,
                    })
                messages.success(request, f"Booth {booth.booth_number} updated.")
            except ValidationError as exc:
                msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
                if is_ajax:
                    return JsonResponse({"status": "error", "message": msg}, status=400)
                messages.error(request, f"Could not update booth: {msg}")
            return redirect("elections:setup_booths", election_id=election.id)

        elif action == "delete_booth":
            booth_id = request.POST.get("booth_id")
            try:
                booth = get_object_or_404(Booth, id=booth_id, election=election)
                is_cleared = request.session.get(f"allocation_cleared_{election.id}", False)
                remove_booth(booth_id=booth.id, auto_distribute=not is_cleared)
                request.session[f"slips_downloaded_{election.id}"] = False
                messages.success(request, "Booth removed, remaining booths renumbered, and voters reallocated." if not is_cleared else "Booth removed and remaining booths renumbered.")
            except ValidationError as exc:
                msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
                messages.error(request, f"Could not delete booth: {msg}")
            return redirect("elections:setup_booths", election_id=election.id)

        elif action in ("rotate_credentials", "change_pass", "rotate_pass"):
            device_id = request.POST.get("device_id")
            is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("is_ajax") == "1"
            try:
                device = get_object_or_404(Device, id=device_id, booth__election=election)
                rotated_dev, new_pw = rotate_device_credentials(device.id)
                if is_ajax:
                    return JsonResponse({
                        "status": "success",
                        "device_id": rotated_dev.id,
                        "identifier": rotated_dev.identifier,
                        "new_password": new_pw,
                        "credential_status": rotated_dev.credential_status,
                        "status_display": "Active",
                    })
                request.session["rotated_device_credentials"] = {
                    "identifier": rotated_dev.identifier,
                    "password": new_pw,
                    "device_type": rotated_dev.get_device_type_display(),
                    "booth_number": rotated_dev.booth.booth_number if rotated_dev.booth else None,
                }
                messages.success(request, f"Credentials updated for {rotated_dev.identifier}.")
            except ValidationError as exc:
                msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
                if is_ajax:
                    return JsonResponse({"status": "error", "message": msg}, status=400)
                messages.error(request, f"Credential change failed: {msg}")
            return redirect("elections:setup_booths", election_id=election.id)

        elif action == "revoke_credentials":
            device_id = request.POST.get("device_id")
            is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("is_ajax") == "1"
            try:
                device = get_object_or_404(Device, id=device_id, booth__election=election)
                revoked_dev = revoke_device_credentials(device.id)
                if is_ajax:
                    return JsonResponse({
                        "status": "success",
                        "device_id": revoked_dev.id,
                        "identifier": revoked_dev.identifier,
                        "credential_status": revoked_dev.credential_status,
                        "status_display": "Revoked",
                    })
                messages.success(request, f"Credentials revoked for {revoked_dev.identifier}.")
            except ValidationError as exc:
                msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
                if is_ajax:
                    return JsonResponse({"status": "error", "message": msg}, status=400)
                messages.error(request, f"Revocation failed: {msg}")
            return redirect("elections:setup_booths", election_id=election.id)

        elif action == "clear_allocation":
            deallocate_voters_from_booth(election_id=election.id)
            request.session[f"allocation_cleared_{election.id}"] = True
            request.session.modified = True
            request.session[f"slips_downloaded_{election.id}"] = False
            is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("is_ajax") == "1"
            if is_ajax:
                return JsonResponse({"status": "success", "message": "Voter allocations cleared. Auto-allocation stopped."})
            messages.success(request, "Voter allocations cleared. Auto-allocation stopped.")
            return redirect("elections:setup_booths", election_id=election.id)

        elif action == "auto_distribute":
            request.session[f"allocation_cleared_{election.id}"] = False
            request.session.modified = True
            auto_distribute_unallocated_voters(election_id=election.id)
            request.session[f"slips_downloaded_{election.id}"] = False
            is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("is_ajax") == "1"
            if is_ajax:
                return JsonResponse({"status": "success", "message": "Unallocated voters auto-distributed across booths."})
            messages.success(request, "Unallocated voters auto-distributed across booths.")
            return redirect("elections:setup_booths", election_id=election.id)

        elif action == "customize_allocation":
            booth_id = request.POST.get("booth_id")
            group_ids = request.POST.getlist("group_ids")
            gid_list = [int(g) for g in group_ids if str(g).isdigit()]
            is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("is_ajax") == "1"
            is_cleared = request.session.get(f"allocation_cleared_{election.id}", False)
            try:
                res = customize_booth_allocation(
                    election_id=election.id,
                    booth_id=int(booth_id),
                    academic_group_ids=gid_list,
                    auto_distribute_remaining=not is_cleared,
                )
                request.session[f"slips_downloaded_{election.id}"] = False
                if is_ajax:
                    return JsonResponse({
                        "status": "success",
                        "booth_id": res["booth_id"],
                        "booth_number": res["booth_number"],
                        "allocated_count": res["allocated_count"],
                    })
                messages.success(request, f"Booth {res['booth_number']} allocation updated.")
            except ValidationError as exc:
                msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
                if is_ajax:
                    return JsonResponse({"status": "error", "message": msg}, status=400)
                messages.error(request, f"Could not update allocation: {msg}")
            return redirect("elections:setup_booths", election_id=election.id)

        elif action == "save_draft":
            election.is_saved_draft = True
            election.save(update_fields=["is_saved_draft"])
            messages.success(request, f"Election '{election.name}' saved as draft.")
            return redirect("elections:dashboard")

        elif action == "continue":
            if not election.booths.exists():
                messages.error(request, "Please add at least one polling booth before proceeding.")
                return redirect("elections:setup_booths", election_id=election.id)

            # Invariant: 100% of voters must be allocated before proceeding
            unallocated_count = election.election_voters.filter(booth__isnull=True).count()
            if unallocated_count > 0:
                messages.error(
                    request,
                    f"All voters must be allocated to a booth before proceeding. There are {unallocated_count} unallocated voter(s)."
                )
                return redirect("elections:setup_booths", election_id=election.id)

            # Ensure admin has downloaded the final voter slips reflecting all changes
            slips_downloaded = request.session.get(f"slips_downloaded_{election.id}", False)
            if not slips_downloaded:
                messages.error(request, "Please download the final voter slips PDF before proceeding to review.")
                return redirect("elections:setup_booths", election_id=election.id)

            election.setup_stage = 4
            election.save(update_fields=["setup_stage"])
            return redirect("elections:setup_review", election_id=election.id)

        else:
            messages.error(request, "Invalid action requested.")
            return redirect("elections:setup_booths", election_id=election.id)

    # GET Request:
    booths = election.booths.prefetch_related("devices__user", "allocated_voters__voter__academic_group").order_by("booth_number", "id")
    
    # Auto-allocation: automatically balance unallocated voters across booths unless allocations are cleared
    total_enrolled = election.election_voters.count()
    is_cleared = request.session.get(f"allocation_cleared_{election.id}", False)
    if booths.exists() and total_enrolled > 0 and not is_cleared:
        unallocated_total = election.election_voters.filter(booth__isnull=True).count()
        if unallocated_total > 0:
            auto_distribute_unallocated_voters(election_id=election.id)
            # Refresh booths after distribution
            booths = election.booths.prefetch_related("devices__user", "allocated_voters__voter__academic_group").order_by("booth_number", "id")

    # Real station device session tracking (never show fake active status)
    active_device_ids = set(
        DeviceSession.objects.filter(
            device__booth__election=election,
            is_active=True
        ).values_list("device_id", flat=True)
    )

    booth_data = []
    allocation_data = []
    palette = ["#3B82F6", "#8B5CF6", "#10B981", "#F59E0B", "#EC4899", "#6366F1", "#14B8A6", "#64748B"]

    for b in booths:
        officer = next((d for d in b.devices.all() if d.device_type == DeviceType.OFFICER), None)
        kiosk = next((d for d in b.devices.all() if d.device_type == DeviceType.KIOSK), None)
        booth_data.append({
            "booth": b,
            "officer_device": officer,
            "officer_in_session": (officer.id in active_device_ids) if officer else False,
            "kiosk_device": kiosk,
            "kiosk_in_session": (kiosk.id in active_device_ids) if kiosk else False,
        })

        # Calculate allocation breakdown for this booth
        b_allocated_count = b.allocated_voters.count()
        group_counts = (
            b.allocated_voters.filter(voter__academic_group__isnull=False)
            .values("voter__academic_group__id", "voter__academic_group__name")
            .annotate(count=Count("id"))
            .order_by("voter__academic_group__name")
        )
        no_grp_count = b.allocated_voters.filter(voter__academic_group__isnull=True).count()
        
        breakdown = []
        for idx, gc in enumerate(group_counts):
            pct = round((gc["count"] / b_allocated_count * 100), 1) if b_allocated_count > 0 else 0
            breakdown.append({
                "id": gc["voter__academic_group__id"],
                "name": gc["voter__academic_group__name"],
                "count": gc["count"],
                "color": palette[idx % len(palette)],
                "pct": pct,
            })
        if no_grp_count:
            pct = round((no_grp_count / b_allocated_count * 100), 1) if b_allocated_count > 0 else 0
            breakdown.append({
                "id": 0,
                "name": "General",
                "count": no_grp_count,
                "color": "#9CA3AF",
                "pct": pct,
            })

        allocation_data.append({
            "booth": b,
            "allocated_count": b_allocated_count,
            "breakdown": breakdown,
            "assigned_group_ids": [item["id"] for item in breakdown if item["id"] != 0],
        })

    next_booth_number = (max([b.booth_number for b in booths], default=0) + 1)
    unallocated_count = election.election_voters.filter(booth__isnull=True).count()
    slips_downloaded = request.session.get(f"slips_downloaded_{election.id}", False)
    new_credentials = request.session.pop("new_booth_credentials", None)
    rotated_credentials = request.session.pop("rotated_device_credentials", None)

    # Academic groups for customize allocation modal
    group_ids = (
        election.election_voters.filter(voter__academic_group__isnull=False)
        .values_list("voter__academic_group_id", flat=True)
        .distinct()
    )
    available_groups = list(AcademicGroup.objects.filter(id__in=group_ids).order_by("name"))
    for grp in available_groups:
        grp.voter_count = election.election_voters.filter(voter__academic_group=grp).count()

    clean_name = re.sub(r'[^a-zA-Z0-9_\-]+', '_', election.name.strip()).strip('_') or f"election_{election.id}"
    voter_slip_filename = f"{clean_name}_voterslip.pdf"

    return render(request, "elections/setup_booths.html", {
        "election": election,
        "booth_data": booth_data,
        "allocation_data": allocation_data,
        "available_groups": available_groups,
        "next_booth_number": next_booth_number,
        "total_enrolled": total_enrolled,
        "unallocated_count": unallocated_count,
        "slips_downloaded": slips_downloaded,
        "new_credentials": new_credentials,
        "rotated_credentials": rotated_credentials,
        "voter_slip_filename": voter_slip_filename,
    })


@never_cache
@admin_required
def election_voter_slips_pdf_view(request, election_id: int):
    """Generate and download the official voter slips PDF for an election.
    
    Enforces:
    - All enrolled voters must be allocated to a booth before slips can be printed.
    """
    election = get_object_or_404(Election, id=election_id)
    unallocated_count = election.election_voters.filter(booth__isnull=True).count()
    if unallocated_count > 0:
        messages.error(
            request,
            f"Cannot print voter slips: all voters must be allocated to a booth first. There are {unallocated_count} unallocated voter(s)."
        )
        return redirect("elections:setup_booths", election_id=election.id)

    pdf_bytes = generate_voter_slips_pdf(election)
    
    # Mark slips as downloaded for this election
    request.session[f"slips_downloaded_{election.id}"] = True
    
    clean_name = re.sub(r'[^a-zA-Z0-9_\-]+', '_', election.name.strip()).strip('_') or f"election_{election.id}"
    filename = f"{clean_name}_voterslip.pdf"
    
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@never_cache
@admin_required
def election_voter_slips_print_view(request, election_id: int):
    """Printable HTML view of voter slips for in-browser printing.
    
    Enforces:
    - All enrolled voters must be allocated to a booth before slips can be printed.
    """
    election = get_object_or_404(Election, id=election_id)
    unallocated_count = election.election_voters.filter(booth__isnull=True).count()
    if unallocated_count > 0:
        messages.error(
            request,
            f"Cannot print voter slips: all voters must be allocated to a booth first. There are {unallocated_count} unallocated voter(s)."
        )
        return redirect("elections:setup_booths", election_id=election.id)

    election_voters = (
        ElectionVoter.objects.filter(election=election)
        .select_related("voter", "voter__academic_group", "booth")
        .order_by("booth__booth_number", "voter__name", "voter__primary_registry_value")
    )
    # Mark slips as downloaded for this election
    request.session[f"slips_downloaded_{election.id}"] = True

    clean_name = re.sub(r'[^a-zA-Z0-9_\-]+', '_', election.name.strip()).strip('_') or f"election_{election.id}"
    voter_slip_filename = f"{clean_name}_voterslip.pdf"
    
    return render(request, "elections/voter_slips_print.html", {
        "election": election,
        "election_voters": election_voters,
        "total_count": election_voters.count(),
        "voter_slip_filename": voter_slip_filename,
    })


@never_cache
@admin_required
def election_setup_review_view(request, election_id: int):
    """Stage 4: Review and start election (references/06.4_review.png).
    
    Displays full stored configuration across:
    1. Election information (name, description)
    2. Election voters (registry, total count, group breakdown)
    3. Positions and candidates (positions with badges, candidate counts & list)
    4. Booths and allocation (booths, allocated voters, live officer & kiosk sessions)
    
    All sections are collapsible with accordions, starting collapsed.
    'Change' actions navigate back to the respective setup steps with preserved state.
    """
    election = get_object_or_404(Election, id=election_id, status=ElectionStatus.DRAFT)
    
    # Ensure setup stage reflects review
    if election.setup_stage < 4:
        election.setup_stage = 4
        election.save(update_fields=["setup_stage"])

    # Handle Save as Draft post if submitted from review page
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "save_draft":
            election.is_saved_draft = True
            election.save(update_fields=["is_saved_draft"])
            messages.success(request, f"Draft election '{election.name}' saved. You can resume setup anytime.")
            return redirect("elections:dashboard")

    # 1. Voters data
    total_enrolled_voters = election.election_voters.count()
    
    group_counts = []
    if election.voter_registry:
        groups = list(AcademicGroup.objects.filter(registry=election.voter_registry).order_by("name"))
    else:
        group_ids = (
            election.election_voters.filter(voter__academic_group__isnull=False)
            .values_list("voter__academic_group_id", flat=True)
            .distinct()
        )
        groups = list(AcademicGroup.objects.filter(id__in=group_ids).order_by("name"))

    palette = ["#3B82F6", "#8B5CF6", "#10B981", "#F59E0B", "#EC4899", "#6366F1", "#14B8A6", "#64748B"]
    for idx, grp in enumerate(groups):
        cnt = election.election_voters.filter(voter__academic_group=grp).count()
        if cnt > 0 or len(groups) <= 12:
            group_counts.append({
                "name": grp.name,
                "count": cnt,
                "color": palette[idx % len(palette)],
            })

    # 2. Positions and candidates data
    positions = list(
        Position.objects.filter(election=election)
        .prefetch_related(
            "candidates__voter__academic_group",
            "eligible_groups"
        )
        .order_by("display_order", "id")
    )

    # 3. Booths and allocation data
    booths = list(
        election.booths.prefetch_related(
            "devices__user",
            "allocated_voters"
        ).order_by("booth_number", "id")
    )
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
            "booth": b,
            "booth_number": b.booth_number,
            "name": b.name,
            "display_name": f"Booth {b.booth_number}" + (f" ({b.name})" if b.name else ""),
            "voter_count": b.allocated_voters.count(),
            "officer_dev": officer_dev,
            "kiosk_dev": kiosk_dev,
            "officer_online": officer_online,
            "kiosk_online": kiosk_online,
        })

    # Assign preset badges for positions matching references/06.2 and 06.4
    presets = [
        {"type": "peach", "icon": "crown"},
        {"type": "gold", "icon": "star"},
        {"type": "pink", "icon": "book"},
        {"type": "purple", "icon": "bolt"},
        {"type": "mint", "icon": "leaf"},
        {"type": "teal", "icon": "megaphone"},
        {"type": "blue", "icon": "users"},
    ]
    for idx, pos in enumerate(positions):
        preset = presets[idx % len(presets)]
        pos.badge_type = preset["type"]
        pos.badge_icon = preset["icon"]

    # 4. Compute Comprehensive System Validation Checks
    c1_details = {
        "label": "Election details provided",
        "passed": bool(election.name.strip()),
    }
    c1_voters = {
        "label": f"Voters selected ({total_enrolled_voters:,})" if total_enrolled_voters > 0 else "No voters selected",
        "passed": total_enrolled_voters > 0,
    }
    c1_positions = {
        "label": f"At least one position added ({len(positions)})" if len(positions) > 0 else "At least one position added",
        "passed": len(positions) > 0,
    }
    c1_candidates = {
        "label": "Candidates added for all positions",
        "passed": len(positions) > 0 and all(p.candidates.count() > 0 for p in positions),
    }
    c1_booths = {
        "label": f"Booths created ({len(booths)})" if len(booths) > 0 else "Booths created",
        "passed": len(booths) > 0,
    }
    c1_allocation = {
        "label": f"All voters allocated to booths ({allocated_percentage}%)",
        "passed": total_enrolled_voters > 0 and unallocated_count == 0,
    }

    all_paired = len(booths) > 0 and all(
        b.devices.filter(device_type=DeviceType.OFFICER).exists() and
        b.devices.filter(device_type=DeviceType.KIOSK).exists()
        for b in booths
    )
    c2_paired = {
        "label": "All booths paired with devices",
        "passed": all_paired,
    }

    online_officers = sum(1 for b in booth_review_data if b["officer_online"])
    c2_officers = {
        "label": f"Officer stations online ({online_officers}/{len(booths)})",
        "passed": len(booths) > 0 and online_officers == len(booths),
        "is_warning": len(booths) > 0 and online_officers < len(booths),
    }

    online_kiosks = sum(1 for b in booth_review_data if b["kiosk_online"])
    c2_kiosks = {
        "label": f"Kiosk devices online ({online_kiosks}/{len(booths)})",
        "passed": len(booths) > 0 and online_kiosks == len(booths),
        "is_warning": len(booths) > 0 and online_kiosks < len(booths),
    }

    # Duplicate candidate name checks
    has_duplicates = False
    for p in positions:
        names = [c.name.strip().lower() for c in p.candidates.all()]
        if len(names) != len(set(names)):
            has_duplicates = True
            break
    c2_duplicates = {
        "label": "No duplicate candidates",
        "passed": not has_duplicates,
    }

    critical_passed = (
        c1_details["passed"] and
        c1_voters["passed"] and
        c1_positions["passed"] and
        c1_candidates["passed"] and
        c1_booths["passed"] and
        c1_allocation["passed"] and
        c2_paired["passed"] and
        c2_duplicates["passed"]
    )

    c2_validation = {
        "label": "Validation passed" if critical_passed else "Validation issues found",
        "passed": critical_passed,
    }

    ready_to_start = critical_passed and c2_officers["passed"] and c2_kiosks["passed"]
    c2_ready = {
        "label": "Ready to start election" if ready_to_start else ("Pending station logins" if critical_passed else "Configuration incomplete"),
        "passed": ready_to_start,
        "is_warning": critical_passed and not ready_to_start,
    }

    validation_col1 = [c1_details, c1_voters, c1_positions, c1_candidates, c1_booths, c1_allocation]
    validation_col2 = [c2_paired, c2_officers, c2_kiosks, c2_duplicates, c2_validation, c2_ready]

    all_validations_ok = all(item["passed"] for item in validation_col1 + validation_col2)

    return render(request, "elections/setup_review.html", {
        "election": election,
        "active_step": 4,
        "total_enrolled_voters": total_enrolled_voters,
        "group_counts": group_counts,
        "positions": positions,
        "booth_review_data": booth_review_data,
        "total_booths": len(booths),
        "total_allocated": total_allocated,
        "unallocated_count": unallocated_count,
        "allocated_percentage": allocated_percentage,
        "validation_col1": validation_col1,
        "validation_col2": validation_col2,
        "critical_passed": critical_passed,
        "ready_to_start": ready_to_start,
        "all_validations_ok": all_validations_ok,
    })



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
    """Authoritative Stage 1 election voter setup view."""
    return redirect("elections:setup_voters", election_id=election_id)


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
    """Authoritative Stage 3 booth & allocation setup view."""
    return redirect("elections:setup_booths", election_id=election_id)


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

    return render(request, "elections/voter_slips_print.html", {
        "election": election,
        "booth": booth,
        "election_voters": voters,
        "total_count": voters.count(),
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



