"""Data models for voting application.

Owns:
- VoterAuthorization: Single-use, time-bound permission for one ElectionVoter to cast a ballot at one Kiosk.
- Vote: Anonymous candidate selection record preserving strict ballot secrecy.
"""
from django.db import models
from django.utils import timezone


class AuthorizationStatus(models.TextChoices):
    """Lifecycle states of a voter authorization."""
    ACTIVE = 'ACTIVE', 'Active'
    USED = 'USED', 'Used'
    CANCELLED = 'CANCELLED', 'Cancelled'


class VoterAuthorization(models.Model):
    """Represents temporary permission for one ElectionVoter to vote at one Kiosk.
    
    Invariants:
    - Exactly one ACTIVE authorization per ElectionVoter.
    - Exactly one ACTIVE authorization per Kiosk at any time.
    - Booth isolation: authorization.booth == election_voter.booth == kiosk.booth.
    - USED and CANCELLED are terminal states.
    """
    election_voter = models.ForeignKey(
        'voters.ElectionVoter',
        on_delete=models.CASCADE,
        related_name='authorizations',
        help_text="Enrolled election voter being authorized."
    )
    booth = models.ForeignKey(
        'voters.Booth',
        on_delete=models.CASCADE,
        related_name='authorizations',
        help_text="Polling booth where this authorization was issued."
    )
    kiosk = models.ForeignKey(
        'accounts.Device',
        on_delete=models.CASCADE,
        related_name='authorizations',
        help_text="Target Voting Kiosk unlocked by this authorization."
    )
    status = models.CharField(
        max_length=20,
        choices=AuthorizationStatus.choices,
        default=AuthorizationStatus.ACTIVE,
        help_text="Current state of the authorization."
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when the authorization was created."
    )
    used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the ballot was successfully committed."
    )
    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the authorization was cancelled by officer or election close."
    )

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # Invariant A: A voter must not have multiple simultaneous ACTIVE authorizations for the same election.
            models.UniqueConstraint(
                fields=['election_voter'],
                condition=models.Q(status=AuthorizationStatus.ACTIVE),
                name='unique_active_authorization_per_voter'
            ),
            # Invariant: A kiosk must not have multiple simultaneous ACTIVE authorizations.
            models.UniqueConstraint(
                fields=['kiosk'],
                condition=models.Q(status=AuthorizationStatus.ACTIVE),
                name='unique_active_authorization_per_kiosk'
            ),
        ]

    def __str__(self):
        return f"Auth #{self.pk} [{self.status}] — Voter {self.election_voter_id} on Kiosk {self.kiosk_id}"

    @property
    def is_active(self) -> bool:
        return self.status == AuthorizationStatus.ACTIVE

    @property
    def is_used(self) -> bool:
        return self.status == AuthorizationStatus.USED

    @property
    def is_cancelled(self) -> bool:
        return self.status == AuthorizationStatus.CANCELLED


class Vote(models.Model):
    """Represents a recorded candidate selection.
    
    STRICT BALLOT SECRECY INVARIANT:
    Vote deliberately contains ONLY:
    - election
    - candidate
    - created_at
    
    NEVER add voter_id, election_voter_id, authorization_id, or booth_id.
    """
    election = models.ForeignKey(
        'elections.Election',
        on_delete=models.CASCADE,
        related_name='votes',
        help_text="Election for which this vote was cast."
    )
    candidate = models.ForeignKey(
        'elections.Candidate',
        on_delete=models.CASCADE,
        related_name='votes',
        help_text="Candidate selected by the voter."
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when the ballot was committed."
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Vote for Candidate {self.candidate_id} in Election {self.election_id}"
