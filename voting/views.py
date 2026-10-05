"""Views for voting application.

Owns:
- Officer Station dashboard, voter lookup, voter verification, and authorization
- Voting Kiosk lifecycle, ballot presentation, and submission
- Live election monitoring dashboard
- Results presentation
"""

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from accounts.models import DeviceSession, DeviceType
from accounts.permissions import kiosk_required, officer_required
from elections.models import Election, ElectionStatus
from voters.models import AcademicGroup, ElectionVoter


@never_cache
@officer_required
def officer_dashboard_view(request):
    """Officer Station Voting Desk Dashboard (references/officer_01.png).
    
    Derives booth and election strictly from the authenticated Device.
    Enforces booth isolation: only displays voters allocated to this booth.
    """
    device = getattr(request, "device", None) or getattr(request.user, "device", None)
    booth = getattr(request, "booth", None) or (getattr(device, "booth", None) if device else None)

    if not booth or not booth.election:
        return render(request, "voting/officer/session_revoked.html", {
            "device": device,
            "error_title": "Session Inactive or Revoked",
            "error_message": "Your station identity is not active or credentials have been rotated by an administrator. Please log in again to continue.",
        }, status=403)

    election = booth.election

    if election.status not in (ElectionStatus.DRAFT, ElectionStatus.ACTIVE):
        return render(request, "voting/officer/election_closed.html", {
            "device": device,
            "booth": booth,
            "election": election,
            "error_title": "Election Concluded",
            "error_message": "Voting for this election has officially concluded. The voting desk is now closed.",
        }, status=403)

    is_draft = (election.status == ElectionStatus.DRAFT)
    is_active = (election.status == ElectionStatus.ACTIVE)

    # Derive Kiosk pairing and status for this booth
    kiosk_device = booth.devices.filter(device_type=DeviceType.KIOSK).first()
    kiosk_online = False
    if kiosk_device:
        kiosk_online = DeviceSession.objects.filter(
            device=kiosk_device,
            is_active=True
        ).exists()

    # Query allocated voters strictly for this booth (Booth Isolation)
    voter_qs = (
        ElectionVoter.objects.filter(election=election, booth=booth)
        .select_related("voter__academic_group__parent", "voter__registry")
        .order_by("voter__primary_registry_value", "voter__name")
    )

    total_voters = voter_qs.count()
    voted_count = voter_qs.filter(has_voted=True).count()
    # In-progress voters will be counted via active VoterAuthorization when introduced
    in_progress_count = 0
    not_voted_count = total_voters - voted_count - in_progress_count

    # Extract distinct groups for filtering
    group_names = sorted(list(set(
        ev.voter.academic_group.name
        for ev in voter_qs
        if ev.voter.academic_group is not None
    )))

    # Serialize voters list for initial render and client-side fast search/selection
    voters_data = []
    for ev in voter_qs:
        v = ev.voter
        reg = v.registry
        status_code = "voted" if ev.has_voted else "not_voted"
        ev_voted_at = getattr(ev, "voted_at", None)

        # Dynamic schema fields based strictly on the registry schema
        meta_fields = []
        if reg:
            if reg.primary_id_source:
                meta_fields.append({"label": reg.primary_id_source, "val": v.primary_registry_value})
            if reg.group_source:
                grp_val = "—"
                if v.academic_group:
                    grp_val = v.academic_group.parent.name if v.academic_group.parent else v.academic_group.name
                meta_fields.append({"label": reg.group_source, "val": grp_val})
            if reg.has_subgroups and reg.subgroup_source:
                sub_val = "—"
                if v.academic_group and v.academic_group.parent:
                    sub_val = v.academic_group.name
                meta_fields.append({"label": reg.subgroup_source, "val": sub_val})
            if reg.gender_source and v.gender:
                meta_fields.append({"label": reg.gender_source, "val": v.gender})
        else:
            meta_fields.append({"label": "Voter ID", "val": v.primary_registry_value})
            if v.academic_group:
                meta_fields.append({"label": "Group", "val": v.academic_group.name})
            if v.gender:
                meta_fields.append({"label": "Gender", "val": v.gender})

        # Top-level group for roster display
        group_display = "—"
        if v.academic_group:
            group_display = v.academic_group.parent.name if v.academic_group.parent else v.academic_group.name

        voters_data.append({
            "id": ev.id,
            "voter_id": v.primary_registry_value,
            "name": v.name,
            "group": group_display,
            "meta_fields": meta_fields,
            "has_voted": ev.has_voted,
            "status_code": status_code,
            "status_display": "Voted" if ev.has_voted else "Not voted",
            "voted_at": ev_voted_at.strftime("%I:%M %p") if ev_voted_at else None,
        })

    return render(request, "voting/officer/dashboard.html", {
        "booth": booth,
        "device": device,
        "election": election,
        "is_draft": is_draft,
        "is_active": is_active,
        "kiosk_device": kiosk_device,
        "kiosk_online": kiosk_online,
        "total_voters": total_voters,
        "voted_count": voted_count,
        "in_progress_count": in_progress_count,
        "not_voted_count": not_voted_count,
        "group_names": group_names,
        "voters": voters_data,
    })


@never_cache
@officer_required
def officer_authorize_view(request):
    """Issues an atomic single-use VoterAuthorization for an enrolled voter.
    
    Triggered via POST from the Officer Voting Desk.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST method required."}, status=405)

    device = getattr(request, "device", None) or getattr(request.user, "device", None)
    if not device:
        return JsonResponse({"success": False, "error": "Officer Device authentication required."}, status=403)

    import json
    voter_identifier = None
    if request.content_type == "application/json" and request.body:
        try:
            body_data = json.loads(request.body)
            voter_identifier = body_data.get("voter_id")
        except json.JSONDecodeError:
            pass
    if not voter_identifier:
        voter_identifier = request.POST.get("voter_id")

    if not voter_identifier:
        return JsonResponse({"success": False, "error": "Missing voter identifier."}, status=400)

    from voting.services import create_authorization
    try:
        auth = create_authorization(device, voter_identifier)
        return JsonResponse({
            "success": True,
            "authorization_id": auth.id,
            "voter_id": voter_identifier,
            "message": "Authorization successfully issued. Kiosk unlocked.",
        })
    except ValidationError as e:
        error_msg = e.message if hasattr(e, "message") else str(e)
        return JsonResponse({"success": False, "error": error_msg}, status=400)
    except Exception as e:
        return JsonResponse({"success": False, "error": f"Authorization error: {str(e)}"}, status=500)


@never_cache
@officer_required
def officer_cancel_authorization_view(request):
    """Cancels an existing ACTIVE authorization."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST method required."}, status=405)

    device = getattr(request, "device", None) or getattr(request.user, "device", None)
    if not device:
        return JsonResponse({"success": False, "error": "Officer Device authentication required."}, status=403)

    import json
    auth_id = None
    if request.content_type == "application/json" and request.body:
        try:
            body_data = json.loads(request.body)
            auth_id = body_data.get("authorization_id")
        except json.JSONDecodeError:
            pass
    if not auth_id:
        auth_id = request.POST.get("authorization_id")

    if not auth_id:
        return JsonResponse({"success": False, "error": "Missing authorization ID."}, status=400)

    from voting.services import cancel_authorization
    try:
        auth = cancel_authorization(device, int(auth_id))
        return JsonResponse({
            "success": True,
            "message": "Authorization cancelled successfully.",
        })
    except ValidationError as e:
        error_msg = e.message if hasattr(e, "message") else str(e)
        return JsonResponse({"success": False, "error": error_msg}, status=400)
    except Exception as e:
        return JsonResponse({"success": False, "error": f"Cancellation error: {str(e)}"}, status=500)


@never_cache
@kiosk_required
def kiosk_view(request):
    """Voting Kiosk primary landing view.
    
    Derives booth and election strictly from the authenticated Kiosk Device.
    Presents locked readiness state waiting for officer authorization.
    """
    from django.db.models import Count
    from voting.models import AuthorizationStatus, VoterAuthorization

    device = getattr(request, "device", None) or getattr(request.user, "device", None)
    booth = getattr(request, "booth", None) or (getattr(device, "booth", None) if device else None)

    if not booth or not booth.election:
        return render(request, "voting/kiosk/unassigned.html", {
            "device": device,
            "error": "This Kiosk is not assigned to an active polling booth.",
        }, status=403)

    election = booth.election
    if election.status != ElectionStatus.ACTIVE:
        is_draft = (election.status == ElectionStatus.DRAFT)
        return render(request, "voting/kiosk/kiosk_landing.html", {
            "booth": booth,
            "device": device,
            "election": election,
            "is_draft": is_draft,
            "is_locked": True,
            "positions": [],
            "total_positions": 0,
        })

    # Check for an active authorization on this kiosk
    active_auth = (
        VoterAuthorization.objects
        .filter(kiosk=device, status=AuthorizationStatus.ACTIVE)
        .first()
    )
    is_locked = (active_auth is None)

    # Filter positions where candidate count > 1 (single-candidate positions win unanimously and are skipped on kiosk)
    positions = (
        election.positions
        .prefetch_related('candidates')
        .annotate(candidate_count=Count('candidates'))
        .filter(candidate_count__gt=1)
        .order_by('display_order', 'id')
    )

    return render(request, "voting/kiosk/kiosk_landing.html", {
        "booth": booth,
        "device": device,
        "election": election,
        "active_auth": active_auth,
        "is_locked": is_locked,
        "positions": positions,
        "total_positions": positions.count(),
    })
