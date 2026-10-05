"""Views for voters application.

Owns:
- Central voter registry browsing, search, and filtering (grouped accordion by Group/Sub Group)
- Single voter creation, editing, and deletion
- Academic Group and Sub Group CRUD
- CSV and Excel batch imports with dry-run validation and atomic commitment
- Election voter enrollment with configuration freeze enforcement
"""
from typing import Any, Dict, List, Optional, Tuple
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from accounts.permissions import admin_required
from elections.models import Election, ElectionStatus
from elections.selectors import list_elections
from voters.importers import (
    parse_csv_content,
    parse_excel_content,
    process_voter_import,
    resolve_column_indices,
)
from voters.models import AcademicGroup, AcademicGroupType, ElectionVoter, Voter, VoterRegistry
from voters.selectors import (
    get_registry_summary,
    get_voter_registry,
    list_academic_groups,
    list_top_level_groups,
    list_voter_registries,
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
    update_academic_group,
    update_voter,
)


def _redirect_registry(registry_id: Optional[int] = None):
    if registry_id:
        return redirect("voters:registry_detail", registry_id=registry_id)
    return redirect("voters:registry")


@never_cache
@admin_required
def registries_list_view(request):
    """Voter Registries Home — displays all configured voter registries.
    
    Faithfully reproduces references/04_voter_registry_home.png:
    - Light white background
    - Center-focused composition
    - Editorial serif typography
    - Minimal borderless / subtle cards
    - Schema mapping display: Name | Primary ID | Group | Subgroup | Gender
    """
    registries = list_voter_registries()
    return render(request, "voters/registries.html", {
        "registries": registries,
        "active_nav": "voters",
    })


@never_cache
@admin_required
def registry_create_view(request):
    """Initial Setup with Import: Create a new voter registry with fixed schema mapping.
    
    Faithfully reproduces references/04_voter_registry_initial.png:
    - Step 1: Centered half-screen file dropzone for CSV or Excel files.
    - Step 2: Transitions to Initial import configuration with:
      - Registry details: Name and Description
      - Map columns from file: Voter ID, Name, Group, Sub-group, Gender
      - File preview: Live 5-row table reflecting chosen column mappings
      - Submit commit: Creates VoterRegistry and imports voters atomically.
    """
    if request.GET.get("reset") == "1":
        request.session.pop("pending_file", None)
        return redirect("voters:registry_create")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        primary_id_source = request.POST.get("primary_id_source", "").strip()
        name_source = request.POST.get("name_source", "").strip()
        group_source = request.POST.get("group_source", "").strip()
        subgroup_source = request.POST.get("subgroup_source", "").strip()
        gender_source = request.POST.get("gender_source", "").strip()
        has_subgroups_toggle = request.POST.get("has_subgroups_toggle", "yes").strip().lower()

        # Check if file was uploaded directly in this POST or already parsed in session
        uploaded_file = request.FILES.get("file")
        headers = None
        data_rows = None
        filename = ""
        filesize_str = ""

        if uploaded_file:
            filename = uploaded_file.name
            fname_lower = filename.lower()
            try:
                if fname_lower.endswith(".csv"):
                    headers, data_rows = parse_csv_content(uploaded_file)
                elif fname_lower.endswith((".xlsx", ".xls")):
                    headers, data_rows = parse_excel_content(uploaded_file)
                else:
                    messages.error(request, "Please upload a valid CSV (.csv) or Excel (.xlsx, .xls) file.")
                    return render(request, "voters/registry_create.html", {"active_nav": "voters"})
            except Exception as exc:
                messages.error(request, f"Failed to read file: {str(exc)}")
                return render(request, "voters/registry_create.html", {"active_nav": "voters"})

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
                "filename": filename,
                "filesize": filesize_str,
            }
        elif "pending_file" in request.session:
            pending_f = request.session["pending_file"]
            headers = pending_f.get("headers")
            data_rows = pending_f.get("data_rows")
            filename = pending_f.get("filename", "Uploaded file")
            filesize_str = pending_f.get("filesize", "")

        if has_subgroups_toggle == "no":
            clean_subgroup = ""
        else:
            clean_subgroup = subgroup_source if subgroup_source and subgroup_source.lower() not in ["(none)", "(none - optional)", "none", "—", "-"] else ""

        def _render_error(msg: str):
            messages.error(request, msg)
            pending_f = request.session.get("pending_file") or {
                "filename": filename or "Uploaded file",
                "filesize": filesize_str or "",
                "headers": headers,
                "data_rows": data_rows,
            }
            initial_parse_data = {
                "filename": filename or pending_f.get("filename", "Uploaded file"),
                "filesize": filesize_str or pending_f.get("filesize", ""),
                "headers": headers,
                "total_rows": len(data_rows) if data_rows else 0,
                "preview_rows": data_rows[:5] if data_rows else [],
                "detected": {
                    "primary_id": primary_id_source,
                    "name": name_source,
                    "group": group_source,
                    "subgroup": clean_subgroup,
                    "gender": gender_source,
                },
                "has_subgroups_toggle": "no" if (has_subgroups_toggle == "no" or not clean_subgroup) else "yes",
            }
            return render(request, "voters/registry_create.html", {
                "active_nav": "voters",
                "name": name,
                "pending_file": pending_f,
                "initial_parse_data": initial_parse_data,
            })

        if not headers or not data_rows:
            return _render_error("Please upload a CSV or Excel file containing voter records.")

        if not name:
            return _render_error("Please enter a name for the voter registry.")

        if not primary_id_source:
            return _render_error("Please select a column from your file to use as the Voter ID.")

        if not name_source:
            return _render_error("Please select a column from your file to use as the Name.")

        if not group_source:
            return _render_error("Please select a column from your file to use as the Department or Group.")

        # Atomic registry creation and voter import
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
                    first_err = import_result.errors[0]
                    first_msg = first_err['errors'][0]
                    raise ValidationError(first_msg)

            # Clean up pending session state
            request.session.pop("pending_file", None)
            request.session.pop("pending_import", None)

            messages.success(
                request,
                f"Voter registry '{reg.name}' created with {import_result.created_count} voters imported successfully."
            )
            return redirect("voters:registry_detail", registry_id=reg.id)

        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            return _render_error(msg)
        except Exception as exc:
            return _render_error(f"Failed to create registry and import records: {str(exc)}")

    # GET request - each registry creation starts with a fresh file upload and configuration
    request.session.pop("pending_file", None)
    request.session.pop("pending_import", None)
    return render(request, "voters/registry_create.html", {
        "pending_file": None,
        "initial_parse_data": None,
        "name": "",
        "active_nav": "voters",
    })


@never_cache
@admin_required
def registry_view(request, registry_id: Optional[int] = None):
    """Primary central voter registry — grouped accordion view for a specific registry."""
    if registry_id is None:
        registry = VoterRegistry.objects.first()
        if not registry:
            return redirect("voters:registries")
    else:
        registry = get_object_or_404(VoterRegistry, id=registry_id)

    search_query = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "").strip()

    # Grouped accordion data (top-level groups + their sub-groups) for this registry
    top_level_groups = list_top_level_groups(registry_id=registry.id)

    if sort in ("count", "count_desc"):
        top_level_groups = sorted(top_level_groups, key=lambda g: getattr(g, "voter_count", 0), reverse=True)
    elif sort == "count_asc":
        top_level_groups = sorted(top_level_groups, key=lambda g: getattr(g, "voter_count", 0), reverse=False)
    elif sort == "time":
        top_level_groups = sorted(top_level_groups, key=lambda g: g.id, reverse=True)

    # Flat voter list for search/filter result display
    is_filtering = bool(search_query)
    filtered_voters = None
    total_results = 0
    if is_filtering:
        filtered_voters = list_voters(
            search_query=search_query or None,
            registry_id=registry.id,
        )
        if sort == "time":
            filtered_voters = filtered_voters.order_by("-created_at")
        total_results = filtered_voters.count()

    summary = get_registry_summary(registry_id=registry.id)
    draft_elections = list_elections().filter(status=ElectionStatus.DRAFT)
    is_linked_to_election = bool(registry and registry.is_locked)

    linked_elections = list(registry.active_elections) if registry else []

    return render(request, "voters/registry.html", {
        "registry": registry,
        "is_linked_to_election": is_linked_to_election,
        "linked_elections": linked_elections,
        "top_level_groups": top_level_groups,
        "is_filtering": is_filtering,
        "filtered_voters": filtered_voters,
        "total_results": total_results,
        "summary": summary,
        "draft_elections": draft_elections,
        "search_query": search_query,
        "current_sort": sort,
        "active_nav": "voters",
    })


@never_cache
@admin_required
@require_http_methods(["POST"])
def registry_delete_view(request, registry_id: int):
    """Delete an entire voter registry and its associated voters and groups."""
    registry = get_object_or_404(VoterRegistry, id=registry_id)
    reg_name = registry.name
    try:
        delete_voter_registry(registry_id=registry_id)
        messages.success(request, f"Registry '{reg_name}' was successfully deleted.")
    except ValidationError as exc:
        msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
        messages.error(request, f"Could not delete registry: {msg}")
    except Exception as exc:
        messages.error(request, f"Could not delete registry: {str(exc)}")

    return redirect("voters:registries")


@never_cache
@admin_required
@require_http_methods(["POST"])
def voter_create_view(request):
    """Add an individual voter to a registry."""
    primary_id = request.POST.get("primary_registry_value", "").strip()
    name = request.POST.get("name", "").strip()
    gender = request.POST.get("gender", "").strip()
    group_id_str = request.POST.get("academic_group_id", "").strip()
    group_id = int(group_id_str) if group_id_str.isdigit() else None
    registry_id_str = request.POST.get("registry_id", "").strip()
    registry_id = int(registry_id_str) if registry_id_str.isdigit() else None

    if registry_id:
        reg = VoterRegistry.objects.filter(id=registry_id).first()
        if reg and reg.is_locked:
            messages.error(request, "Cannot add voters: this registry is linked to an active or completed election.")
            return _redirect_registry(registry_id)

    try:
        voter = create_voter(
            primary_registry_value=primary_id,
            name=name,
            gender=gender,
            academic_group_id=group_id,
            registry_id=registry_id,
        )
        messages.success(request, f"Voter '{voter.name}' ({voter.primary_registry_value}) added to registry.")
    except ValidationError as exc:
        msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
        messages.error(request, f"Could not create voter: {msg}")

    return _redirect_registry(registry_id)


@never_cache
@admin_required
@require_http_methods(["POST"])
def voter_edit_view(request, voter_id: int):
    """Update a voter's details in the registry."""
    name = request.POST.get("name", "").strip()
    gender = request.POST.get("gender", "").strip()
    group_id_str = request.POST.get("academic_group_id", "").strip()
    group_id = int(group_id_str) if group_id_str.isdigit() else None
    registry_id_str = request.POST.get("registry_id", "").strip()
    registry_id = int(registry_id_str) if registry_id_str.isdigit() else None

    voter = Voter.objects.filter(id=voter_id).select_related("registry").first()
    if voter and voter.registry and voter.registry.is_locked:
        messages.error(request, "Cannot modify voter: this registry is linked to an active or completed election.")
        return _redirect_registry(voter.registry_id or registry_id)

    try:
        update_voter(
            voter_id=voter_id,
            name=name,
            gender=gender,
            academic_group_id=group_id,
        )
        messages.success(request, "Voter details updated.")
    except ValidationError as exc:
        msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
        messages.error(request, f"Could not update voter: {msg}")

    return _redirect_registry(registry_id)


@never_cache
@admin_required
@require_http_methods(["POST"])
def voter_delete_view(request, voter_id: int):
    """Delete a voter from the registry."""
    registry_id_str = request.POST.get("registry_id", "").strip()
    registry_id = int(registry_id_str) if registry_id_str.isdigit() else None

    voter = Voter.objects.filter(id=voter_id).select_related("registry").first()
    if voter and voter.registry_id and not registry_id:
        registry_id = voter.registry_id

    try:
        delete_voter(voter_id=voter_id)
        messages.success(request, "Voter removed from registry.")
    except ValidationError as exc:
        msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
        messages.error(request, f"Could not delete voter: {msg}")

    return _redirect_registry(registry_id)


@never_cache
@admin_required
@require_http_methods(["POST"])
def voter_bulk_delete_view(request):
    """Delete multiple selected voters from the registry."""
    raw_list = request.POST.getlist("voter_ids")
    registry_id_str = request.POST.get("registry_id", "").strip()
    target_reg_id = int(registry_id_str) if registry_id_str.isdigit() else None

    if target_reg_id:
        reg = VoterRegistry.objects.filter(id=target_reg_id).first()
        if reg and reg.is_locked:
            messages.error(request, "Cannot delete voters: this registry is linked to an active or completed election.")
            return _redirect_registry(target_reg_id)

    id_list = []
    for item in raw_list:
        for piece in str(item).split(","):
            piece = piece.strip()
            if piece.isdigit():
                id_list.append(int(piece))

    deleted_count = 0
    errors = []
    for vid in id_list:
        try:
            delete_voter(voter_id=vid)
            deleted_count += 1
        except Exception as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            errors.append(msg)

    if deleted_count:
        messages.success(request, f"{deleted_count} voter(s) deleted from registry.")
    if errors:
        messages.error(request, "; ".join(errors[:3]))

    return _redirect_registry(target_reg_id)


# ---------------------------------------------------------------------------
# Academic Group CRUD
# ---------------------------------------------------------------------------

@never_cache
@admin_required
@require_http_methods(["POST"])
def group_create_view(request):
    """Create a new top-level Group in a registry."""
    name = request.POST.get("name", "").strip()
    registry_id_str = request.POST.get("registry_id", "").strip()
    registry_id = int(registry_id_str) if registry_id_str.isdigit() else None

    if registry_id:
        reg = VoterRegistry.objects.filter(id=registry_id).first()
        if reg and reg.is_locked:
            messages.error(request, "Cannot add groups: this registry is linked to an active or completed election.")
            return _redirect_registry(registry_id)

    target_reg_id = registry_id
    try:
        group = create_academic_group(name=name, registry_id=registry_id)
        target_reg_id = group.registry_id or registry_id
        messages.success(request, f"Group '{group.name}' created.")
    except ValidationError as exc:
        msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
        messages.error(request, f"Could not create group: {msg}")
    return _redirect_registry(target_reg_id)


@never_cache
@admin_required
@require_http_methods(["POST"])
def group_edit_view(request, group_id: int):
    """Rename an existing Group or Sub Group."""
    name = request.POST.get("name", "").strip()
    group = get_object_or_404(AcademicGroup, id=group_id)
    target_reg_id = group.registry_id

    if group.registry and group.registry.is_locked:
        messages.error(request, "Cannot rename group: this registry is linked to an active or completed election.")
        return _redirect_registry(target_reg_id)

    try:
        update_academic_group(group_id=group_id, name=name)
        messages.success(request, "Group renamed successfully.")
    except ValidationError as exc:
        msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
        messages.error(request, f"Could not rename group: {msg}")
    return _redirect_registry(target_reg_id)


@never_cache
@admin_required
@require_http_methods(["POST"])
def group_delete_view(request, group_id: int):
    """Delete all voter rows in a Group or Sub Group (and the group if empty)."""
    group = get_object_or_404(AcademicGroup, id=group_id)
    target_reg_id = group.registry_id

    if group.registry and group.registry.is_locked:
        messages.error(request, "Cannot delete group: this registry is linked to an active or completed election.")
        return _redirect_registry(target_reg_id)

    # 1. Identify all voter rows belonging to this group (and child subgroups if top-level)
    if group.type == AcademicGroupType.GROUP:
        child_ids = list(group.children.values_list("id", flat=True))
        voters_to_delete = list(Voter.objects.filter(models.Q(academic_group=group) | models.Q(academic_group_id__in=child_ids)))
    else:
        voters_to_delete = list(Voter.objects.filter(academic_group=group))

    # 2. Check if any have already committed a ballot
    voted_count = ElectionVoter.objects.filter(voter__in=voters_to_delete, has_voted=True).count()
    if voted_count > 0:
        messages.error(request, f"Cannot delete: {voted_count} voter(s) in this group have already voted.")
        return _redirect_registry(target_reg_id)

    # 3. Delete all matching voter rows
    deleted_rows_count = 0
    with transaction.atomic():
        for v in voters_to_delete:
            delete_voter(voter_id=v.id)
            deleted_rows_count += 1

        # Also clean up the empty group/subgroup if no voters remain
        try:
            delete_academic_group(group_id=group.id)
        except Exception:
            pass

    if deleted_rows_count > 0:
        messages.success(request, f"Deleted {deleted_rows_count} voter row(s) from '{group.name}'.")
    else:
        messages.success(request, f"Group '{group.name}' deleted.")

    return _redirect_registry(target_reg_id)


@never_cache
@admin_required
@require_http_methods(["POST"])
def subgroup_create_view(request, group_id: int):
    """Create a Sub Group under the specified top-level Group."""
    name = request.POST.get("name", "").strip()
    parent = get_object_or_404(AcademicGroup, id=group_id)
    target_reg_id = parent.registry_id

    if parent.registry and parent.registry.is_locked:
        messages.error(request, "Cannot add sub groups: this registry is linked to an active or completed election.")
        return _redirect_registry(target_reg_id)

    try:
        subgroup = create_academic_group(name=name, parent_id=group_id, registry_id=target_reg_id)
        messages.success(request, f"Sub Group '{subgroup.name}' created.")
    except ValidationError as exc:
        msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
        messages.error(request, f"Could not create sub group: {msg}")
    return _redirect_registry(target_reg_id)


# ---------------------------------------------------------------------------
# Bulk Import
# ---------------------------------------------------------------------------

@never_cache
@admin_required
@require_http_methods(["POST"])
def voter_import_parse_view(request):
    """AJAX endpoint to parse uploaded spreadsheet headers and return auto-detected mappings."""
    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        return JsonResponse({"success": False, "error": "No file uploaded."}, status=400)

    filename = uploaded_file.name
    fname_lower = filename.lower()
    try:
        if fname_lower.endswith(".csv"):
            headers, data_rows = parse_csv_content(uploaded_file)
        elif fname_lower.endswith((".xlsx", ".xls")):
            headers, data_rows = parse_excel_content(uploaded_file)
        else:
            return JsonResponse({"success": False, "error": "Unsupported file format. Please upload a .csv or .xlsx file."}, status=400)
    except Exception as exc:
        return JsonResponse({"success": False, "error": f"Failed to read file: {str(exc)}"}, status=400)

    # Optional registry context for subsequent imports
    registry_id_str = request.POST.get("registry_id", "").strip() or request.GET.get("registry_id", "").strip()
    registry = None
    if registry_id_str.isdigit():
        try:
            registry = VoterRegistry.objects.get(id=int(registry_id_str))
        except VoterRegistry.DoesNotExist:
            pass

    col_map = resolve_column_indices(headers, registry=registry)

    # When importing into an existing registry, enforce locked state and strict required columns:
    # "only missing group name can be manually typed. if any other column is missing , it should show that the file doesnt is missing that column and couldnot be used"
    if registry:
        if registry.elections.exists():
            return JsonResponse({
                "success": False,
                "error": "Cannot import voters: this registry is linked to an active or past election and is locked against modifications."
            }, status=400)

        if col_map['primary_id'] is None:
            return JsonResponse({
                "success": False,
                "error": f"The uploaded file is missing the required '{registry.primary_id_source}' column and cannot be used."
            }, status=400)

        if col_map['name'] is None:
            return JsonResponse({
                "success": False,
                "error": f"The uploaded file is missing the required '{registry.name_source}' column and cannot be used."
            }, status=400)

        if registry.has_subgroups and col_map['subgroup'] is None:
            return JsonResponse({
                "success": False,
                "error": f"The uploaded file is missing the required '{registry.subgroup_source}' column and cannot be used."
            }, status=400)

    # Format human-readable file size
    size_bytes = uploaded_file.size
    if size_bytes < 1024:
        filesize_str = f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        filesize_str = f"{round(size_bytes / 1024)} KB"
    else:
        filesize_str = f"{round(size_bytes / (1024 * 1024), 1)} MB"

    # Store parsed data in session so commit can use it without re-uploading
    request.session["pending_file"] = {
        "headers": headers,
        "data_rows": data_rows,
        "filename": filename,
        "filesize": filesize_str,
    }

    detected = {
        "primary_id": headers[col_map['primary_id']] if col_map['primary_id'] is not None else None,
        "name": headers[col_map['name']] if col_map['name'] is not None else None,
        "gender": headers[col_map['gender']] if col_map['gender'] is not None else None,
        "group": headers[col_map['group']] if col_map['group'] is not None else None,
        "subgroup": headers[col_map['subgroup']] if col_map['subgroup'] is not None else None,
    }

    existing_groups = []
    existing_subgroups = []
    if registry:
        existing_groups = list(
            AcademicGroup.objects.filter(registry=registry, type=AcademicGroupType.GROUP)
            .values_list("name", flat=True).distinct().order_by("name")
        )
        existing_subgroups = list(
            AcademicGroup.objects.filter(registry=registry, type=AcademicGroupType.SUBGROUP)
            .values_list("name", flat=True).distinct().order_by("name")
        )

    has_subgroups = registry.has_subgroups if registry else True
    if registry and not has_subgroups:
        detected["subgroup"] = None
        col_map["subgroup"] = None
        missing_subgroup = False
    else:
        missing_subgroup = col_map["subgroup"] is None

    return JsonResponse({
        "success": True,
        "filename": filename,
        "filesize": filesize_str,
        "headers": headers,
        "total_rows": len(data_rows),
        "preview_rows": data_rows[:5],
        "detected": detected,
        "missing_group": col_map['group'] is None,
        "missing_subgroup": missing_subgroup,
        "has_subgroups": has_subgroups,
        "existing_groups": existing_groups,
        "existing_subgroups": existing_subgroups,
        "registry": {
            "id": registry.id,
            "name": registry.name,
            "primary_id_source": registry.primary_id_source,
            "name_source": registry.name_source,
            "group_source": registry.group_source,
            "subgroup_source": registry.subgroup_source if has_subgroups else "",
            "has_subgroups": has_subgroups,
            "gender_source": registry.gender_source,
        } if registry else None,
    })


@never_cache
@admin_required
def voter_import_view(request, registry_id: Optional[int] = None):
    """Subsequent voter import into an existing registry per references/04_voter_import.png."""
    reg_id = registry_id or (int(request.GET.get("registry_id")) if request.GET.get("registry_id", "").isdigit() else None)
    if reg_id:
        registry = get_object_or_404(VoterRegistry, id=reg_id)
    else:
        registry = VoterRegistry.objects.first()
        if not registry:
            return redirect("voters:registry_create")

    if registry.is_locked:
        messages.error(request, "Cannot import voters: this registry is linked to an active or completed election and is locked against modifications.")
        return redirect("voters:registry_detail", registry_id=registry.id)

    prefill_group = request.GET.get("default_group", "").strip()

    existing_groups = list(
        AcademicGroup.objects.filter(registry=registry, type=AcademicGroupType.GROUP)
        .values_list("name", flat=True).distinct().order_by("name")
    )
    existing_subgroups = list(
        AcademicGroup.objects.filter(registry=registry, type=AcademicGroupType.SUBGROUP)
        .values_list("name", flat=True).distinct().order_by("name")
    )

    if request.method == "POST":
        action = request.POST.get("action", "commit")
        uploaded_file = request.FILES.get("file")

        headers = None
        data_rows = None
        filename = ""
        filesize_str = ""

        session_payload = request.session.get("pending_file") or request.session.get("pending_import") or {}

        if uploaded_file:
            filename = uploaded_file.name
            fname_lower = filename.lower()
            try:
                if fname_lower.endswith(".csv"):
                    headers, data_rows = parse_csv_content(uploaded_file)
                elif fname_lower.endswith((".xlsx", ".xls")):
                    headers, data_rows = parse_excel_content(uploaded_file)
                else:
                    messages.error(request, "Unsupported file format. Please upload a .csv or .xlsx file.")
                    return redirect("voters:registry_import", registry_id=registry.id)
            except Exception as exc:
                messages.error(request, f"Failed to read file: {str(exc)}")
                return redirect("voters:registry_import", registry_id=registry.id)

            size_bytes = uploaded_file.size
            if size_bytes < 1024:
                filesize_str = f"{size_bytes} B"
            elif size_bytes < 1024 * 1024:
                filesize_str = f"{round(size_bytes / 1024)} KB"
            else:
                filesize_str = f"{round(size_bytes / (1024 * 1024), 1)} MB"
        elif session_payload:
            headers = session_payload.get("headers")
            data_rows = session_payload.get("data_rows")
            filename = session_payload.get("filename", "Uploaded file")
            filesize_str = session_payload.get("filesize", "")

        if not headers or not data_rows:
            messages.error(request, "Please select a CSV or Excel file to upload.")
            return redirect("voters:registry_import", registry_id=registry.id)

        default_group = (
            request.POST.get("default_group", "").strip()
            or request.POST.get("custom_group_name", "").strip()
            or session_payload.get("default_group")
            or None
        )
        default_subgroup = (
            request.POST.get("default_subgroup", "").strip()
            or request.POST.get("custom_subgroup_name", "").strip()
            or session_payload.get("default_subgroup")
            or None
        )
        id_col = request.POST.get("id_col", "").strip() or session_payload.get("id_col") or None
        name_col = request.POST.get("name_col", "").strip() or session_payload.get("name_col") or None
        gender_col = request.POST.get("gender_col", "").strip() or session_payload.get("gender_col") or None
        group_col = request.POST.get("group_col", "").strip() or session_payload.get("group_col") or None
        subgroup_col = request.POST.get("subgroup_col", "").strip() or session_payload.get("subgroup_col") or None

        pending_data = {
            "headers": headers,
            "data_rows": data_rows,
            "filename": filename,
            "filesize": filesize_str,
            "default_group": default_group,
            "default_subgroup": default_subgroup,
            "id_col": id_col,
            "name_col": name_col,
            "gender_col": gender_col,
            "group_col": group_col,
            "subgroup_col": subgroup_col,
        }
        request.session["pending_file"] = pending_data
        request.session["pending_import"] = pending_data

        if not registry.has_subgroups:
            default_subgroup = None
            subgroup_col = None

        col_map = resolve_column_indices(headers, registry=registry)
        if not registry.has_subgroups:
            col_map['subgroup'] = None

        if col_map['primary_id'] is None:
            messages.error(request, f"The uploaded file is missing the required '{registry.primary_id_source}' column and cannot be used.")
            return redirect("voters:registry_import", registry_id=registry.id)

        if col_map['name'] is None:
            messages.error(request, f"The uploaded file is missing the required '{registry.name_source}' column and cannot be used.")
            return redirect("voters:registry_import", registry_id=registry.id)

        if registry.has_subgroups and col_map['subgroup'] is None:
            messages.error(request, f"The uploaded file is missing the required '{registry.subgroup_source}' column and cannot be used.")
            return redirect("voters:registry_import", registry_id=registry.id)

        def _format_preview(rows_list, c_map, d_grp=""):
            res = []
            if not rows_list or not c_map:
                return res
            for r in rows_list[:5]:
                p_id = r[c_map['primary_id']] if c_map.get('primary_id') is not None and c_map['primary_id'] < len(r) else ""
                p_name = r[c_map['name']] if c_map.get('name') is not None and c_map['name'] < len(r) else ""
                p_grp = (r[c_map['group']] if c_map.get('group') is not None and c_map['group'] < len(r) else "") or d_grp or "—"
                p_sub = (r[c_map['subgroup']] if registry.has_subgroups and c_map.get('subgroup') is not None and c_map['subgroup'] < len(r) else "") or "—"
                p_gen = (r[c_map['gender']] if c_map.get('gender') is not None and c_map['gender'] < len(r) else "") or "—"
                res.append({
                    "primary_id": p_id,
                    "name": p_name,
                    "group": p_grp,
                    "subgroup": p_sub,
                    "gender": p_gen,
                })
            return res

        missing_grp = (col_map['group'] is None and not default_group)

        if missing_grp:
            messages.error(request, f"The '{registry.group_source}' column is missing from the file. Please enter a {registry.group_source} name.")
            return render(request, "voters/import.html", {
                "registry": registry,
                "file_info": {
                    "filename": filename,
                    "filesize": filesize_str,
                    "total_rows": len(data_rows),
                },
                "headers": headers,
                "col_map": col_map,
                "missing_group": True,
                "missing_subgroup": False,
                "default_group": default_group,
                "default_subgroup": "",
                "preview_rows": _format_preview(data_rows, col_map, default_group),
                "existing_groups": existing_groups,
                "existing_subgroups": existing_subgroups,
                "prefill_group": prefill_group,
                "pending_file": request.session.get("pending_file"),
                "active_nav": "voters",
            })

        is_dry_run = (action == "preview")

        try:
            result = process_voter_import(
                headers=headers,
                data_rows=data_rows,
                registry_id=registry.id,
                dry_run=is_dry_run,
                default_group=default_group,
                default_subgroup=default_subgroup,
                id_col=id_col,
                name_col=name_col,
                gender_col=gender_col,
                group_col=group_col,
                subgroup_col=subgroup_col,
            )

            if not is_dry_run:
                request.session.pop("pending_file", None)
                request.session.pop("pending_import", None)
                messages.success(
                    request,
                    f"Import completed successfully! {result.created_count} voters added, {result.updated_count} updated."
                )
                if registry_id is not None:
                    return redirect("voters:registry_detail", registry_id=registry.id)
                else:
                    return redirect("voters:registry")

            return render(request, "voters/import.html", {
                "registry": registry,
                "file_info": {
                    "filename": filename,
                    "filesize": filesize_str,
                    "total_rows": len(data_rows),
                },
                "headers": headers,
                "col_map": col_map,
                "missing_group": col_map['group'] is None and not default_group,
                "missing_subgroup": (registry.has_subgroups and col_map['subgroup'] is None and not default_subgroup),
                "default_group": default_group,
                "default_subgroup": default_subgroup,
                "preview_rows": _format_preview(data_rows, col_map, default_group, default_subgroup),
                "existing_groups": existing_groups,
                "existing_subgroups": existing_subgroups,
                "prefill_group": prefill_group,
                "pending_file": request.session.get("pending_file"),
                "active_nav": "voters",
            })

        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, msg)
        except Exception as exc:
            messages.error(request, f"Failed to process file: {str(exc)}")

    # GET request
    if request.GET.get("reset") == "1":
        request.session.pop("pending_file", None)
        request.session.pop("pending_import", None)

    pending_file = request.session.get("pending_file")
    col_map = None
    missing_group = False
    missing_subgroup = False
    preview_rows = []
    if pending_file and pending_file.get("headers"):
        col_map = resolve_column_indices(pending_file["headers"], registry=registry)
        missing_group = col_map['group'] is None
        missing_subgroup = col_map['subgroup'] is None
        p_rows = pending_file.get("data_rows", [])
        if col_map:
            for r in p_rows[:5]:
                p_id = r[col_map['primary_id']] if col_map.get('primary_id') is not None and col_map['primary_id'] < len(r) else ""
                p_name = r[col_map['name']] if col_map.get('name') is not None and col_map['name'] < len(r) else ""
                p_grp = (r[col_map['group']] if col_map.get('group') is not None and col_map['group'] < len(r) else "") or "—"
                p_sub = (r[col_map['subgroup']] if col_map.get('subgroup') is not None and col_map['subgroup'] < len(r) else "") or "—"
                p_gen = (r[col_map['gender']] if col_map.get('gender') is not None and col_map['gender'] < len(r) else "") or "—"
                preview_rows.append({
                    "primary_id": p_id,
                    "name": p_name,
                    "group": p_grp,
                    "subgroup": p_sub,
                    "gender": p_gen,
                })

    return render(request, "voters/import.html", {
        "registry": registry,
        "existing_groups": existing_groups,
        "existing_subgroups": existing_subgroups,
        "prefill_group": prefill_group,
        "pending_file": pending_file,
        "col_map": col_map,
        "missing_group": missing_group,
        "missing_subgroup": missing_subgroup,
        "preview_rows": preview_rows,
        "active_nav": "voters",
    })


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

@never_cache
@admin_required
def enroll_voters_view(request, election_id: int):
    """Enroll selected voters into an election."""
    election = get_object_or_404(Election, id=election_id)
    if request.method == "POST":
        voter_ids_raw = request.POST.getlist("voter_ids")
        voter_ids = [int(v) for v in voter_ids_raw if v.isdigit()]

        enroll_all = request.POST.get("enroll_all") == "1"
        if enroll_all:
            group_id = request.POST.get("group_id")
            qs = Voter.objects.all()
            if group_id and group_id.isdigit():
                qs = qs.filter(academic_group_id=int(group_id))
            voter_ids = list(qs.values_list("id", flat=True))

        if not voter_ids:
            messages.error(request, "No voters selected for enrollment.")
            return redirect("voters:registry")

        try:
            count = enroll_voters_in_election(election_id=election.id, voter_ids=voter_ids)
            messages.success(request, f"Enrolled {count} voter(s) into election '{election.name}'.")
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            messages.error(request, f"Enrollment failed: {msg}")

    return redirect("voters:registry")
