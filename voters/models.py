"""Data models for voters application.

Owns:
- AcademicGroup (two-level institutional hierarchy: Group -> optional Sub Group)
- Voter (central installation-scoped voter registry with configurable primary registry identity)
- ElectionVoter (election-specific enrollment, uniqueness, and participation record)
"""
from django.core.exceptions import ValidationError
from django.db import models


class VoterRegistry(models.Model):
    """Represents a persistent voter dataset with its own fixed schema.
    
    Electra supports multiple independent voter registries.
    Each registry has an immutable schema established during initial import.
    """
    name = models.CharField(
        max_length=150,
        unique=True,
        help_text="Name of the registry (e.g. CET Students, Faculty & Staff)."
    )
    primary_id_source = models.CharField(
        max_length=100,
        default="Student ID",
        help_text="Source column mapped to Primary Identifier (e.g. Student ID, Employee ID)."
    )
    name_source = models.CharField(
        max_length=100,
        default="Name",
        help_text="Source column mapped to Name (e.g. Name, Full Name)."
    )
    group_source = models.CharField(
        max_length=100,
        default="Department",
        help_text="Source column mapped to Group (e.g. Department, Class)."
    )
    subgroup_source = models.CharField(
        max_length=100,
        blank=True,
        default="Semester",
        help_text="Source column mapped to Sub-group (e.g. Semester, Batch, Designation)."
    )
    gender_source = models.CharField(
        max_length=100,
        blank=True,
        default="Gender",
        help_text="Source column mapped to Gender."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']

    def __str__(self):
        return self.name

    @property
    def primary_identifier_header(self) -> str:
        return self.primary_id_source

    @property
    def name_header(self) -> str:
        return self.name_source

    @property
    def group_header(self) -> str:
        return self.group_source

    @property
    def subgroup_header(self) -> str:
        return self.subgroup_source

    @property
    def gender_header(self) -> str:
        return self.gender_source

    @property
    def has_subgroups(self) -> bool:
        """True if this registry schema is configured with a sub-group level."""
        return bool(
            self.subgroup_source
            and self.subgroup_source.strip()
            and self.subgroup_source.strip().lower() not in ["(none)", "none", "—", "-"]
        )

    @property
    def schema_display(self) -> str:
        """Formatted pipe-separated schema mapping display as shown in references/04_voter_registry_home.png."""
        if self.has_subgroups:
            return f"{self.name_source} | {self.primary_id_source} | {self.group_source} | {self.subgroup_source} | {self.gender_source}"
        return f"{self.name_source} | {self.primary_id_source} | {self.group_source} | {self.gender_source}"

    @property
    def active_elections(self):
        """Elections using this registry that have actually started (ACTIVE, CLOSED, RESULTS_PUBLISHED).
        
        Per design authority: a registry is only locked when an election starts, NOT while in DRAFT.
        """
        return self.elections.exclude(status='DRAFT')

    @property
    def is_locked(self) -> bool:
        """True if this registry is locked because an election has started with it."""
        return self.active_elections.exists()


class AcademicGroupType(models.TextChoices):
    GROUP = 'GROUP', 'Group'
    SUBGROUP = 'SUBGROUP', 'Sub Group'


class AcademicGroup(models.Model):
    """Represents a node in the two-level academic hierarchy (Group -> Sub Group).

    - Level 1 (GROUP): top-level category, e.g. Computer Science, Class 10.
      parent is NULL.
    - Level 2 (SUBGROUP): optional subdivision, e.g. Batch A, Section B.
      parent points to a GROUP-level AcademicGroup.

    Voters are assigned to the leaf node:
    - To a SUBGROUP if sub-groups exist under a group.
    - Directly to the GROUP if it has no children.
    """
    registry = models.ForeignKey(
        VoterRegistry,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='groups',
        help_text="Voter registry this group belongs to."
    )
    name = models.CharField(
        max_length=150,
        help_text="Name of the group (e.g. Computer Science) or sub group (e.g. Batch A)."
    )
    type = models.CharField(
        max_length=20,
        choices=AcademicGroupType.choices,
        default=AcademicGroupType.GROUP,
        help_text="GROUP = top-level; SUBGROUP = child of a GROUP."
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        help_text="Parent GROUP for sub-groups. NULL for top-level groups."
    )

    class Meta:
        ordering = ['name', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['registry', 'name', 'parent'],
                name='unique_academic_group_hierarchy'
            ),
        ]

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} / {self.name}"
        return self.name

    @property
    def is_group(self) -> bool:
        return self.type == AcademicGroupType.GROUP

    @property
    def is_subgroup(self) -> bool:
        return self.type == AcademicGroupType.SUBGROUP

    @property
    def display_label(self) -> str:
        """Human-readable label showing hierarchy."""
        if self.parent:
            return f"{self.parent.name} › {self.name}"
        return self.name


class Voter(models.Model):
    """Represents an eligible voter in the installation's voter registry.
    
    Scoped to a VoterRegistry. Multiple registries are supported.
    No tenant or per-user scoping.
    """
    registry = models.ForeignKey(
        VoterRegistry,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='voters',
        help_text="Voter registry this voter belongs to."
    )
    primary_registry_value = models.CharField(
        max_length=100,
        help_text="Configured institution-defined identifier (e.g. Student ID, University ID, Admission Number)."
    )
    name = models.CharField(
        max_length=200,
        help_text="Voter full legal or school name."
    )
    gender = models.CharField(
        max_length=30,
        blank=True,
        help_text="Gender classification used for eligibility filtering."
    )
    academic_group = models.ForeignKey(
        AcademicGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='voters',
        help_text="Primary department, class, or cohort."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['primary_registry_value', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['registry', 'primary_registry_value'],
                name='unique_voter_primary_id_per_registry'
            ),
        ]

    def __str__(self):
        return f"{self.primary_registry_value} — {self.name}"

    def clean(self):
        super().clean()
        if self.primary_registry_value:
            self.primary_registry_value = self.primary_registry_value.strip()
        if self.name:
            self.name = self.name.strip()

    @property
    def identifier(self) -> str:
        return self.primary_registry_value


class Booth(models.Model):
    """Represents a physical polling booth within an election.
    
    Each booth has:
    - Exactly one Officer Station Device
    - Exactly one Voting Kiosk Device
    - A set of allocated ElectionVoters
    """
    election = models.ForeignKey(
        'elections.Election',
        on_delete=models.CASCADE,
        related_name='booths',
        help_text="Election this booth is configured for."
    )
    booth_number = models.PositiveIntegerField(
        help_text="Sequential booth number within the election (e.g. 1, 2, 3)."
    )
    name = models.CharField(
        max_length=150,
        blank=True,
        default='',
        help_text="Optional custom name or room location (e.g. Room 101, Main Auditorium)."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['booth_number', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['election', 'booth_number'],
                name='unique_booth_number_per_election'
            ),
        ]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        label = f"Booth {self.booth_number}"
        if self.name and self.name.strip():
            return f"{label} ({self.name.strip()})"
        return label

    def clean(self):
        super().clean()
        if self.name:
            self.name = self.name.strip()


class ElectionVoter(models.Model):
    """Represents a voter's participation record in a specific election.
    
    Invariants:
    - Exactly one record per (election, voter).
    - booth is assigned before the election starts.
    - has_voted starts False and transitions to True atomically upon ballot commit.
    """
    election = models.ForeignKey(
        'elections.Election',
        on_delete=models.CASCADE,
        related_name='election_voters',
        help_text="Parent election."
    )
    voter = models.ForeignKey(
        Voter,
        on_delete=models.CASCADE,
        related_name='election_enrollments',
        help_text="Enrolled voter."
    )
    booth = models.ForeignKey(
        Booth,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='allocated_voters',
        help_text="Assigned physical polling booth."
    )
    has_voted = models.BooleanField(
        default=False,
        help_text="True once a complete ballot is successfully committed."
    )

    class Meta:
        ordering = ['voter__primary_registry_value', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['election', 'voter'],
                name='unique_voter_per_election'
            ),
        ]

    def __str__(self):
        return f"{self.voter.primary_registry_value} in {self.election.name} [Voted: {self.has_voted}]"

