"""Query selectors for voters application.

Owns:
- Read-only queries for voter registry lookup, search/filtering, and election enrollment summaries
"""
from typing import Optional
from django.db.models import Count, Prefetch, Q, QuerySet

from voters.models import AcademicGroup, ElectionVoter, Voter, VoterRegistry


def list_voter_registries() -> QuerySet[VoterRegistry]:
    """List all voter registries with annotated voter and top-level group counts."""
    from voters.models import AcademicGroupType
    return VoterRegistry.objects.annotate(
        voter_count=Count("voters", distinct=True),
        group_count=Count(
            "groups",
            filter=Q(groups__type=AcademicGroupType.GROUP),
            distinct=True
        ),
        election_count=Count("elections", distinct=True),
    ).order_by("name", "id")


def get_voter_registry(registry_id: int) -> Optional[VoterRegistry]:
    """Retrieve a voter registry by ID."""
    try:
        return VoterRegistry.objects.get(id=registry_id)
    except VoterRegistry.DoesNotExist:
        return None


def get_voter(voter_id: int) -> Optional[Voter]:
    """Retrieve voter by primary key, or None if not found."""
    try:
        return Voter.objects.select_related("academic_group", "registry").get(id=voter_id)
    except Voter.DoesNotExist:
        return None


def get_voter_by_registry_value(primary_registry_value: str, registry_id: Optional[int] = None) -> Optional[Voter]:
    """Retrieve voter by primary registry identifier (case-insensitive), or None."""
    try:
        qs = Voter.objects.select_related("academic_group", "registry")
        if registry_id:
            qs = qs.filter(registry_id=registry_id)
        return qs.get(
            primary_registry_value__iexact=primary_registry_value.strip()
        )
    except Voter.DoesNotExist:
        return None


def list_voters(
    *,
    search_query: Optional[str] = None,
    academic_group_id: Optional[int] = None,
    gender: Optional[str] = None,
    registry_id: Optional[int] = None,
) -> QuerySet[Voter]:
    """List voters in a voter registry matching search terms and filters."""
    qs = Voter.objects.select_related("academic_group", "registry").order_by("primary_registry_value")

    if registry_id:
        qs = qs.filter(registry_id=registry_id)

    if search_query:
        term = search_query.strip()
        qs = qs.filter(
            Q(primary_registry_value__icontains=term) |
            Q(name__icontains=term)
        )

    if academic_group_id:
        qs = qs.filter(
            Q(academic_group_id=academic_group_id) |
            Q(academic_group__parent_id=academic_group_id)
        )

    if gender:
        qs = qs.filter(gender__iexact=gender.strip())

    return qs


def list_academic_groups(registry_id: Optional[int] = None) -> QuerySet[AcademicGroup]:
    """List all academic hierarchy nodes annotated with their voter count.
    
    Returns leaf nodes suitable for voter assignment dropdowns:
    - Sub Groups (they always are leaves)
    - Top-level Groups that have no children
    """
    from django.db.models import Exists, OuterRef
    has_children = AcademicGroup.objects.filter(parent=OuterRef('pk'))
    qs = AcademicGroup.objects
    if registry_id:
        qs = qs.filter(registry_id=registry_id)
    return qs.annotate(
        voter_count=Count("voters", distinct=True),
        has_subgroups=Exists(has_children),
    ).select_related("parent").order_by("parent__name", "name")


def list_top_level_groups(registry_id: Optional[int] = None):
    """List all top-level GROUPs with annotated voter counts, full subgroup rosters, and dot colors.
    
    Used for the grouped accordion display in the voter registry.
    Voter count aggregates both direct voters and voters in child subgroups.
    Subgroups include their full roster of voters.
    """
    from voters.models import AcademicGroupType
    
    DOT_COLORS = [
        '#3B82F6',  # Blue
        '#8B5CF6',  # Purple
        '#10B981',  # Green
        '#F59E0B',  # Amber
        '#EC4899',  # Pink
        '#6B7280',  # Slate/Gray
        '#6366F1',  # Indigo
        '#14B8A6',  # Teal
    ]

    base_group_qs = AcademicGroup.objects.filter(type=AcademicGroupType.GROUP)
    if registry_id:
        base_group_qs = base_group_qs.filter(registry_id=registry_id)

    subgroups_prefetch = Prefetch(
        'children',
        queryset=AcademicGroup.objects.annotate(
            voter_count=Count('voters', distinct=True)
        ).order_by('name'),
        to_attr='subgroups_list',
    )

    groups = list(base_group_qs.prefetch_related(subgroups_prefetch).order_by('name'))

    for idx, group in enumerate(groups):
        group.dot_color = DOT_COLORS[idx % len(DOT_COLORS)]

        # Voters assigned directly to this group (no subgroup)
        group.direct_voters = list(
            Voter.objects.filter(academic_group=group).select_related('academic_group').order_by('primary_registry_value')
        )

        # Full voters for each subgroup
        for sub in group.subgroups_list:
            sub.all_voters = list(
                Voter.objects.filter(academic_group=sub).select_related('academic_group').order_by('primary_registry_value')
            )

        # Voters in this group or any of its child subgroups
        all_voters = Voter.objects.filter(
            Q(academic_group=group) | Q(academic_group__parent=group)
        ).select_related('academic_group').order_by('primary_registry_value')

        group.voter_count = all_voters.count()
        group.all_voters = list(all_voters)
        group.sample_voters = list(all_voters[:5])
        group.remaining_voters_count = max(0, group.voter_count - len(group.sample_voters))
        group.subgroup_count = len(group.subgroups_list)

    return groups


def list_election_voters(election_id: int) -> QuerySet[ElectionVoter]:
    """Retrieve all voters enrolled in a specific election."""
    return ElectionVoter.objects.filter(
        election_id=election_id
    ).select_related("voter", "voter__academic_group").order_by("voter__primary_registry_value")


def get_enrolled_voter_count(election_id: int) -> int:
    """Return count of enrolled voters for an election."""
    return ElectionVoter.objects.filter(election_id=election_id).count()


def get_registry_summary(registry_id: Optional[int] = None) -> dict:
    """Return aggregate counts for a voter registry or overall."""
    from voters.models import AcademicGroupType
    voters_qs = Voter.objects.all()
    groups_qs = AcademicGroup.objects.filter(type=AcademicGroupType.GROUP)
    if registry_id:
        voters_qs = voters_qs.filter(registry_id=registry_id)
        groups_qs = groups_qs.filter(registry_id=registry_id)
    total_voters = voters_qs.count()
    total_groups = groups_qs.count()
    return {
        "total_voters": total_voters,
        "total_groups": total_groups,
    }


def get_booth(booth_id: int) -> Optional['Booth']:
    """Retrieve a booth by id with preloaded devices and election."""
    from voters.models import Booth
    try:
        return Booth.objects.prefetch_related("devices__user", "allocated_voters").select_related("election").get(id=booth_id)
    except Booth.DoesNotExist:
        return None


def list_election_booths(election_id: int) -> QuerySet['Booth']:
    """List all booths for an election with device and voter counts annotated."""
    from voters.models import Booth
    return Booth.objects.filter(
        election_id=election_id
    ).prefetch_related("devices__user").annotate(
        allocated_count=Count("allocated_voters", distinct=True)
    ).order_by("booth_number", "id")


def get_unallocated_voter_count(election_id: int) -> int:
    """Return the number of enrolled voters who do not yet have an assigned booth."""
    return ElectionVoter.objects.filter(
        election_id=election_id,
        booth__isnull=True
    ).count()

