# Electra — Data Model Specification

**Status:** LOCKED  
**Document Type:** Data Model Contract  
**Purpose:** Define persistent data, relationships, constraints, entities, and multi-position ballot representation.

---

# 1. Data Model Principles

Electra's data model is designed around six properties:

1. One dedicated PostgreSQL database serves one independent Electra installation.
2. Election configuration must become immutable once polling starts.
3. A voter may participate only once in a given election.
4. An authorization is single-use.
5. A complete ballot must be committed atomically.
6. Persistent vote data must not directly identify the voter.

The database is part of the election-integrity boundary.

Application code must not rely solely on UI checks to maintain these properties.

---

# 2. Entity Overview

The core persistent entities are:

```text
User
  │
  └── Device
        │
        └── Booth
              │
              └── Election

Voter
  │
  └── ElectionVoter
        │
        ├── Booth
        │
        └── VoterAuthorization
                 │
                 └── [temporary voting workflow]

Election
  │
  ├── Position
  │     │
  │     ├── Candidate
  │     └── EligibilityRule
  │
  ├── Booth
  │
  └── ElectionVoter

Election
  │
  └── Vote
         │
         └── Candidate
```

Important:

```text
ElectionVoter ─X─ Vote
Voter ──────────X─ Vote
Authorization ──X─ Vote
```

There must be no persistent direct relationship from a Vote to the voter, ElectionVoter, or authorization.

---

# 3. ERD

Conceptual ERD:

```text
┌──────────────────┐
│       User       │
├──────────────────┤
│ id               │
│ role             │
│ ...              │
└────────┬─────────┘
         │
         │ 1
         │
         │ *
┌────────▼─────────┐
│      Device      │
├──────────────────┤
│ id               │
│ user             │
│ device_type      │
│ booth             │
│ identifier       │
│ credential_status│
│ created_at       │
│ updated_at       │
└────────┬─────────┘
         │
         │ belongs to
         ▼
┌──────────────────┐
│      Booth       │
├──────────────────┤
│ id               │
│ election         │
│ name/number      │
│ officer_device   │
│ kiosk_device     │
└────────┬─────────┘
         │
         │
         │
┌────────▼─────────┐
│     Election     │
├──────────────────┤
│ id               │
│ name             │
│ description      │
│ starts_at        │
│ ends_at          │
│ status           │
│ created_at       │
│ closed_at        │
│ results_published│
└───────┬──────────┘
        │
        ├──────────────────────┐
        │                      │
        │ *                    │ *
┌───────▼──────────┐   ┌───────▼──────────┐
│     Position     │   │  ElectionVoter   │
├──────────────────┤   ├──────────────────┤
│ id               │   │ id               │
│ election         │   │ election         │
│ name             │   │ voter            │
│ description      │   │ booth            │
│ display_order    │   │ has_voted        │
└────────┬─────────┘   └────────┬─────────┘
         │                      │
         │ *                    │ 1
         ▼                      ▼
┌──────────────────┐    ┌──────────────────┐
│    Candidate     │    │ Authorization    │
├──────────────────┤    ├──────────────────┤
│ id               │    │ election_voter   │
│ position         │    │ booth            │
│ name             │    │ kiosk            │
│ description      │    │ status           │
│ symbol           │    │ created_at       │
│ photo/...        │    │ used_at          │
└────────┬─────────┘    │ cancelled_at     │
         │              └──────────────────┘
         │
         │ *
         ▼
┌──────────────────┐
│       Vote       │
├──────────────────┤
│ id               │
│ election         │
│ candidate        │
│ created_at       │
└──────────────────┘


┌──────────────────┐
│      Voter       │
├──────────────────┤
│ id               │
│ primary_registry │
│ name             │
│ gender           │
│ academic_group   │
└────────┬─────────┘
         │
         │ *
         ▼
   ElectionVoter


┌──────────────────┐
│  AcademicGroup   │
├──────────────────┤
│ id               │
│ name             │
│ type             │
│ parent           │
└──────────────────┘


Position
   │
   └── EligibilityRule
          └── configured eligibility values
```

---

# 4. User

## Purpose

Represents authenticated application identities.

Roles:

```text
ADMIN
OFFICER
KIOSK
```

The User model extends or replaces Django's authentication user model (`AbstractBaseUser` / standard authentication).

---

## Role Definitions

### ADMIN
The single human administrative user of the Electra installation.

- Created during the first-run installation setup flow.
- Backed by a standard database record with `role = Role.ADMIN`.
- Must not be identified by a hardcoded username (e.g., `"admin"`), fixed primary key (e.g., `id = 1`), or settings constant.
- Manages the installation's elections, voter registry, booths, and station credentials.
- The installation enforces exactly one human Administrator. Public user registration and multi-admin features are prohibited in this version.

### OFFICER
Technical device identity bound to an Officer Station at a specific booth.

### KIOSK
Technical device identity bound to a Voting Kiosk at a specific booth.

---

## Fields

Conceptually:

```text
User
    username
    password (hashed)
    role (ADMIN, OFFICER, KIOSK)
    is_active
    created_at
    updated_at
```

---

## Responsibilities

User is responsible for:

* authentication identity;
* role assignment;
* Django session authentication.

Device-specific configuration and booth bindings belong to `Device`.

---

## Invariants

* Every installation has exactly one human Administrator (`role = ADMIN`).
* The Administrator is created dynamically during first-run initialization and must not rely on hardcoded usernames or IDs.
* There is no public user registration, SaaS tenant accounts, or third-party identity providers.
* Every authenticated technical device has an associated User identity.
* Role boundaries must be enforced server-side.
* A Kiosk identity cannot perform Administrator or Officer operations.
* An Officer identity cannot perform Administrator operations.

---

# 5. Device

## Purpose

Represents a logical Officer Station or Voting Kiosk.

A Device is a technical identity rather than a physical computer.

---

## Fields

Conceptually:

```text
Device
    user
    device_type
    booth
    identifier
    credential_status
    created_at
    updated_at
```

Device types:

```text
OFFICER
KIOSK
```

Credential states:

```text
ACTIVE
REVOKED
```

The logical identifier remains stable when credentials are rotated.

A physical computer may therefore be replaced without creating a new logical booth device.

---

## Relationships

```text
User
  1
  │
  *
Device

Booth
  1
  │
  ├── 1 Officer Device
  └── 1 Kiosk Device
```

---

## Constraints

A booth must have:

```text
exactly 1 Officer Device
exactly 1 Kiosk Device
```

A device must not simultaneously represent incompatible device roles.

A device identifier must be stable and uniquely identifiable.

---

# 6. DeviceSession

## Purpose

Tracks the currently active authenticated session for a technical device.

---

## Fields

```text
DeviceSession
    device
    session_key
    created_at
    last_activity
    revoked_at
```

---

## Invariants

For a given Device:

```text
at most one active DeviceSession
```

At login:

```text
lock Device
    ↓
check active session
    ↓
if active → reject
else → create DeviceSession
```

Credential rotation/revocation must invalidate the active session.

---

# 7. Booth

## Purpose

Represents one physical/logical polling station within an election.

---

## Fields

Conceptually:

```text
Booth
    election
    name/number
```

A Booth is associated with:

```text
1 Officer Device
1 Kiosk Device
```

---

## Relationships

```text
Election
   │
   └── * Booth

Booth
   ├── 1 Officer Device
   └── 1 Kiosk Device

Booth
   └── * ElectionVoter
```

---

## Invariants

### Booth ownership

A booth belongs to exactly one election.

### Device pairing

Every configured booth has exactly:

```text
1 Officer
1 Kiosk
```

### Allocation

Every eligible ElectionVoter must have exactly one Booth before polling begins.

### Active election

Booth configuration cannot change after the election becomes ACTIVE.

### Booth removal

A booth may be removed only before polling.

When a booth is removed:

```text
remove booth
    ↓
find allocated ElectionVoters
    ↓
reallocate voters
    ↓
validate allocation
```

---

# 8. Voter

## Purpose

Represents the central persistent voter registry for the Electra installation.

The Voter registry is installation-wide data, not election-specific data. It is owned by the installation as a whole; it is not partitioned by tenant, owned by individual users, or shared across independent Electra installations.

---

## Fields

```text
Voter
    primary_registry_value
    name
    gender
    academic_group
```

The exact institution-defined registry identity is configurable.

Examples:

```text
Student ID
University ID
Admission Number
```

Do not hardcode `student_id` as the universal identity.

---

## Primary Registry Identity

The configured primary registry identity is stored in:

```text
primary_registry_value
```

The administrator determines what this represents.

Example:

```text
Primary Registry Identity = University ID

primary_registry_value = "UNI202600123"
```

or:

```text
Primary Registry Identity = Student ID

primary_registry_value = "STU202600123"
```

---

## Invariants

The configured registry identity must be unique within the central voter registry.

Therefore:

```text
no two Voter records may have the same
configured primary_registry_value
```

The registry identity is used to:

* match imported records;
* prevent duplicate registry records;
* identify an existing voter during subsequent imports.

---

# 9. AcademicGroup

## Purpose

Represents the configurable academic hierarchy used by voter data and eligibility rules.

---

## Fields

```text
AcademicGroup
    name
    type
    parent
```

Possible types include:

```text
CLASS
SECTION
DEPARTMENT
PROGRAM
SEMESTER
```

The implementation must not assume a universal institutional hierarchy.

---

## Relationships

AcademicGroup may have a parent AcademicGroup.

Example:

```text
Department
    ↓
Semester
```

or:

```text
Class
    ↓
Section
```

---

## Invariants

Academic structure is configurable.

The system must not hardcode assumptions such as:

```text
MCA = department
Semester = child of department
```

unless configured by the administrator.

---

# 10. Election

## Purpose

Represents one election event belonging to the Electra installation.

Elections belong to the installation as a whole. There is no per-user ownership and the data model must not include an `Election.owner` or tenant field.

---

## Fields

```text
Election
    name
    description
    starts_at
    ends_at
    status
    created_at
    closed_at
    results_published_at
```

---

## States

```text
DRAFT
ACTIVE
CLOSED
RESULTS_PUBLISHED
```

There is no persistent `READY` state.

Readiness is determined through configuration validation before activation.

---

## State Transitions

```text
DRAFT
  │
  │ start_election()
  ▼
ACTIVE
  │
  │ close_election()
  ▼
CLOSED
  │
  │ publish_results()
  ▼
RESULTS_PUBLISHED
```

Invalid transitions must be rejected.

---

## Invariants

### DRAFT

Election configuration may be modified.

### ACTIVE

Election configuration is frozen.

### CLOSED

No new authorization or ballot submission is accepted.

### RESULTS_PUBLISHED

Results have been explicitly published.

### Timing

The server must enforce:

```text
now >= ends_at
```

as a voting cutoff.

After the cutoff:

```text
no new authorization
no ballot acceptance
```

---

# 11. Position

## Purpose

Represents an elected position within an election.

---

## Fields

```text
Position
    election
    name
    description
    display_order
```

---

## Relationships

```text
Election
   1
   │
   *
Position
```

Each Position belongs to exactly one Election.

---

## Invariants

A Position cannot be used in an unrelated Election.

A Candidate must belong to the same Position as the position selected on a ballot.

---

# 12. Candidate

## Purpose

Represents a candidate for one position.

---

## Fields

```text
Candidate
    position
    name
    description
    symbol
    photo / optional presentation data
```

---

## Relationships

```text
Position
   1
   │
   *
Candidate
```

A candidate belongs to exactly one position.

---

## Invariants

A ballot may select a candidate only if:

```text
candidate.position == submitted_position
```

A candidate from Position A cannot be submitted for Position B.

---

# 13. EligibilityRule

## Purpose

Defines which voters are eligible for a Position.

A Position may have zero or more eligibility rules.

---

## Rule Semantics

Within one rule:

```text
multiple values = OR
```

Between different rules:

```text
rules = AND
```

Example:

```text
Rule 1:
    academic_group ∈ {
        CSE,
        Mechanical,
        Civil,
        ECE
    }

Rule 2:
    gender ∈ {
        Female
    }
```

Means:

```text
(CSE OR Mechanical OR Civil OR ECE)
AND
Female
```

---

## Explicit Restriction

Do not implement a general-purpose Boolean expression engine.

Do not add arbitrary:

```text
OR groups
nested expressions
custom Boolean trees
```

unless requirements are explicitly changed.

---

# 14. ElectionVoter

## Purpose

Represents the participation of one registered Voter in one Election.

This is the central election-specific voter record.

---

## Fields

```text
ElectionVoter
    election
    voter
    booth
    has_voted
```

---

## Relationships

```text
Voter
   1
   │
   *
ElectionVoter
   *
   │
   1
Election

ElectionVoter
   *
   │
   1
Booth
```

---

## Database Constraint

Must enforce:

```text
UNIQUE(election, voter)
```

This prevents multiple participation records for the same voter in the same election.

---

## Invariants

### One election participation record

For any pair:

```text
(election, voter)
```

there can be only one ElectionVoter.

### One booth

Before election activation:

```text
every eligible ElectionVoter
    → exactly one Booth
```

### Voting state

Initially:

```text
has_voted = false
```

After successful ballot commitment:

```text
has_voted = true
```

Once true, it must never return to false as part of normal voting.

---

# 15. VoterAuthorization

## Purpose

Represents temporary permission for one ElectionVoter to vote at one kiosk.

---

## Fields

```text
VoterAuthorization
    election_voter
    booth
    kiosk
    status
    created_at
    used_at
    cancelled_at
```

---

## States

```text
ACTIVE
USED
CANCELLED
```

There is no `EXPIRED` state.

---

## State Transitions

```text
                 ┌───────────┐
                 │  ACTIVE   │
                 └─────┬─────┘
                       │
             ┌─────────┴─────────┐
             │                   │
             ▼                   ▼
           USED              CANCELLED
```

`ACTIVE` is the only state that permits ballot submission.

`USED` and `CANCELLED` are terminal states.

---

# 16. Authorization Invariants

## Invariant A — Active authorization uniqueness

A voter must not have multiple simultaneous ACTIVE authorizations for the same election.

Conceptually:

```text
For one ElectionVoter:
    ACTIVE authorizations ≤ 1
```

This must be enforced transactionally.

If the database supports the appropriate partial uniqueness constraint, use it.

Otherwise, enforce it through a transaction with row locking.

---

## Invariant B — Authorization belongs to one booth

An authorization's booth must equal the ElectionVoter's assigned booth.

```text
authorization.booth
    ==
authorization.election_voter.booth
```

---

## Invariant C — Authorization belongs to the correct kiosk

The kiosk must belong to the same booth as the authorization.

```text
authorization.kiosk.booth
    ==
authorization.booth
```

---

## Invariant D — Used authorization cannot be reused

Once:

```text
status = USED
```

the authorization can never return to ACTIVE.

It cannot authorize another ballot.

---

## Invariant E — Cancelled authorization cannot be reused

Once:

```text
status = CANCELLED
```

it cannot become ACTIVE again.

A new authorization may be created later if the voter still has not voted.

---

## Invariant F — Successful ballot consumes authorization

A successful ballot must atomically cause:

```text
Authorization.status = USED
```

and:

```text
ElectionVoter.has_voted = TRUE
```

---

# 17. Vote

## Purpose

Represents a recorded candidate selection.

Vote deliberately does not contain voter identity.

---

## Fields

```text
Vote
    election
    candidate
    created_at
```

---

## Relationships

```text
Election
   1
   │
   *
 Vote
   *
   │
   1
Candidate
```

---

## Explicitly Forbidden Relationships

Vote must NOT contain:

```text
voter_id
election_voter_id
authorization_id
```

There must be no direct persistent relationship:

```text
Voter → Vote
ElectionVoter → Vote
Authorization → Vote
```

This is a core ballot-secrecy invariant.

---

# 18. Multi-Position Ballot Representation

A ballot is a logical transaction, not a persistent model.

Example:

```text
One voter
    │
    └── one authorization
             │
             └── one logical ballot
                    │
                    ├── Position 1 → Candidate A
                    ├── Position 2 → Candidate C
                    └── Position 3 → Candidate F
```

Persistent result:

```text
Vote(candidate=A)
Vote(candidate=C)
Vote(candidate=F)
```

These Vote records are not directly linked to the voter.

---

