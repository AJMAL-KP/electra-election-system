"""Data models for elections application.

Owns:
- Election (scoped to installation; no per-user owner)
- Position
- Candidate
- Lifecycle state transitions and configuration freeze
"""
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class ElectionStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    ACTIVE = 'ACTIVE', 'Active'
    CLOSED = 'CLOSED', 'Closed'
    RESULTS_PUBLISHED = 'RESULTS_PUBLISHED', 'Results Published'


class Election(models.Model):
    """Represents an election event belonging to the installation.
    
    The installation is the boundary. There is NO per-user ownership and no Election.owner.
    """
    name = models.CharField(max_length=255, help_text="Election title.")
    description = models.TextField(blank=True, help_text="Institutional overview or notes.")
    starts_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Scheduled voting start time (automatically stamped when election goes live)."
    )
    ends_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Scheduled voting cutoff time."
    )
    status = models.CharField(
        max_length=20,
        choices=ElectionStatus.choices,
        default=ElectionStatus.DRAFT,
        help_text="Canonical lifecycle state."
    )
    voter_registry = models.ForeignKey(
        'voters.VoterRegistry',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='elections',
        help_text="The single voter registry selected as the voter source for this election."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    results_published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} [{self.get_status_display()}]"

    def clean(self):
        if self.starts_at and self.ends_at:
            if self.ends_at <= self.starts_at:
                raise ValidationError({"ends_at": "Election end time must be strictly after start time."})

    @property
    def is_draft(self) -> bool:
        return self.status == ElectionStatus.DRAFT

    @property
    def is_active(self) -> bool:
        return self.status == ElectionStatus.ACTIVE

    @property
    def is_closed(self) -> bool:
        return self.status == ElectionStatus.CLOSED

    @property
    def is_results_published(self) -> bool:
        return self.status == ElectionStatus.RESULTS_PUBLISHED

    @property
    def is_expired(self) -> bool:
        return self.is_active and timezone.now() >= self.ends_at


class Position(models.Model):
    """Represents an elected post on the ballot for an election."""
    election = models.ForeignKey(
        Election,
        on_delete=models.CASCADE,
        related_name='positions',
        help_text="Parent election."
    )
    name = models.CharField(max_length=200, help_text="e.g. President, Vice President")
    display_order = models.PositiveIntegerField(
        default=1,
        help_text="Presentation order on the voting kiosk ballot."
    )

    class Meta:
        ordering = ['display_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['election', 'name'],
                name='unique_position_name_per_election'
            ),
            models.UniqueConstraint(
                fields=['election', 'display_order'],
                name='unique_position_order_per_election'
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.election.name})"


class Candidate(models.Model):
    """Represents a candidate standing for an elected position."""
    position = models.ForeignKey(
        Position,
        on_delete=models.CASCADE,
        related_name='candidates',
        help_text="Position this candidate is contesting."
    )
    voter = models.ForeignKey(
        'voters.Voter',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='candidacies',
        help_text="Optional link to central voter registry identity."
    )
    name = models.CharField(max_length=200, help_text="Candidate full name.")
    academic_group = models.CharField(
        max_length=150,
        blank=True,
        help_text="Academic department, grade, class, or cohort details."
    )
    symbol = models.CharField(
        max_length=100,
        blank=True,
        help_text="Ballot symbol or slate/party name."
    )
    photo = models.ImageField(
        upload_to='candidates/',
        blank=True,
        null=True,
        help_text="Candidate photo displayed on kiosk."
    )

    class Meta:
        ordering = ['name', 'id']

    def __str__(self):
        return f"{self.name} - {self.position.name}"

    def clean(self):
        super().clean()
        if self.symbol and self.symbol.strip():
            cleaned_sym = self.symbol.strip()
            if hasattr(self, 'position') and self.position_id:
                duplicate_sym = Candidate.objects.filter(
                    position__election=self.position.election,
                    symbol__iexact=cleaned_sym
                )
                if self.pk:
                    duplicate_sym = duplicate_sym.exclude(pk=self.pk)
                if duplicate_sym.exists():
                    raise ValidationError({
                        "symbol": f"Ballot symbol '{cleaned_sym}' is already assigned to another candidate in this election."
                    })
