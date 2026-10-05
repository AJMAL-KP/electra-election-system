# Electra — Core System Invariants & Integrity Contract

**Status:** LOCKED  
**Document Type:** Invariants and Integrity Contract  
**Purpose:** Define transactional boundaries, concurrency protections, ballot secrecy, and non-negotiable invariants.

# 18. Installation & Deployment Boundary Invariants

Electra is deployed as a self-hosted web application over a dedicated Local-Area Network (LAN).

The installation itself is the primary data, ownership, and security boundary:

```text
Electra Installation (Single Institution / LAN)
    ├── Exactly One PostgreSQL Database
    ├── Exactly One Human Administrator (role = ADMIN)
    ├── One Installation-Wide Voter Registry
    ├── Multiple Sequential Elections in History (at most ONE election ACTIVE at a time)
    └── Multiple Polling Booths (Officer Station + Kiosk Devices)
```

Non-negotiable installation invariants:

1. **Database Boundary:** One Electra installation maps to exactly one PostgreSQL database. There is no multi-tenancy, cross-institution data sharing, or external cloud synchronization.
2. **Absence of User Ownership:** Neither `Election` nor `Voter` has an `owner` foreign key. Entities belong to the installation as a whole.
3. **Single Administrator Invariant:** The installation permits exactly one active human Administrator (`User.role == ADMIN`).
4. **Dynamic Administrator Identity:** The system must never hardcode the Administrator's username (`"admin"`), primary key (`id == 1`), or credentials. Admin discovery must be performed dynamically via `role == Role.ADMIN` to ensure future extensibility without architectural refactoring.
5. **First-Run Initialization Invariant:** System operations (creating elections, importing voters, provisioning booths) are strictly disallowed while the installation is `UNINITIALIZED`.

---

# 19. Ballot Atomicity Invariant

A multi-position ballot must be all-or-nothing.

Required transaction:

```text
BEGIN
    lock Election
    lock ElectionVoter
    lock Authorization

    validate complete ballot

    create all Vote records

    set ElectionVoter.has_voted = TRUE

    set Authorization.status = USED

    set kiosk state appropriately

COMMIT
```

If any operation fails:

```text
ROLLBACK
```

No Vote records from the failed ballot may remain.

---

# 20. Vote Submission Invariants

For every accepted ballot:

```text
Election.status == ACTIVE
```

and:

```text
now < Election.ends_at
```

and:

```text
Authorization.status == ACTIVE
```

and:

```text
ElectionVoter.has_voted == FALSE
```

and:

```text
authenticated kiosk == Authorization.kiosk
```

and:

```text
kiosk.booth == Authorization.booth
```

and every submitted position/candidate relationship is valid.

---

# 21. Vote Count Invariants

For a complete ballot:

```text
number of Vote records
==
number of required ballot positions
```

No required position may be silently omitted.

No position may receive multiple selections in one ballot.

No candidate may be selected for a position to which it does not belong.

---

# 22. Duplicate-Vote Invariant

A voter may have at most one accepted ballot per election.

The primary persistent indicator is:

```text
ElectionVoter.has_voted
```

Before accepting a ballot:

```text
has_voted == FALSE
```

After successful commit:

```text
has_voted == TRUE
```

Concurrent submissions must lock the relevant ElectionVoter row.

Therefore two concurrent submissions cannot both legitimately commit.

---

# 23. Election Closure Invariant

Vote submission and election closure must synchronize through the Election row.

Both operations must lock the Election row.

Conceptually:

```text
Vote transaction
    ↓
lock Election
```

and:

```text
Close transaction
    ↓
lock Election
```

Only one operation can establish the relevant state transition first.

---

## Case A — Vote obtains lock first

```text
Vote
  ↓
commit
  ↓
Close Election
```

The vote is accepted before closure.

---

## Case B — Close obtains lock first

```text
Close Election
  ↓
Election = CLOSED
  ↓
Vote transaction
  ↓
reject
```

This creates a deterministic closure boundary.

---

# 24. Authorization Transaction Invariant

Authorization creation must be transactional.

Required conceptual order:

```text
BEGIN

lock Election
lock ElectionVoter
lock Kiosk

verify:
    election ACTIVE
    voting time valid
    voter exists
    voter enrolled
    voter belongs to officer booth
    voter has not voted
    kiosk connected
    kiosk ready
    kiosk fullscreen
    no ACTIVE authorization

create ACTIVE authorization

COMMIT
```

Only after commit:

```text
kiosk.unlock
```

The Officer Station must not be told authorization succeeded before persistence succeeds.

---

# 25. Lock Ordering

Critical voting operations must use a consistent lock order.

Preferred order:

```text
Election
    ↓
ElectionVoter
    ↓
Authorization
    ↓
Kiosk/Device where required
```

The implementation must avoid inconsistent lock ordering between different services.

The purpose is to reduce race conditions and deadlock risk.

---

# 26. Kiosk Runtime State

Runtime kiosk state should not be confused with persistent election configuration.

Conceptual kiosk states:

```text
OFFLINE
NOT_READY
READY
UNLOCKED
VOTING
LOCKED
```

The exact implementation may separate browser/UI state from persistent state.

The server remains authoritative.

---

# 27. Kiosk State Invariants

### Default

A kiosk begins locked.

### Authorization

A kiosk may unlock only after valid server-side authorization.

### Booth

A kiosk may respond only to authorizations belonging to its own booth.

### Fullscreen

If fullscreen is exited:

```text
kiosk = NOT_READY
```

The Officer Station must not authorize another voter until readiness is restored.

### Successful vote

After successful commit:

```text
kiosk = LOCKED
```

### Failed vote

A failed transaction must not mark:

```text
has_voted = TRUE
```

and must not mark authorization USED.

---

# 28. Device/Booth Invariants

For every Booth:

```text
exactly one Officer Device
exactly one Kiosk Device
```

For every Device:

```text
device identity is stable
```

Credential rotation changes:

```text
password/credential
Device.cleartext_password
session validity
```

but does not change:

```text
logical device identity
booth association
```

### Active / Draft Login Exclusivity & Credential Auto-Purge

1. **Active/Draft Exclusivity**: Technical devices (`OFFICER` and `KIOSK`) may authenticate only if their parent election is in `ACTIVE` or `DRAFT` status. Login attempts for closed elections are strictly rejected.
2. **Automatic Cleanup of Past Credentials**: When an election transitions to `CLOSED`, all technical device credentials (`Device`, `DeviceSession`, and underlying `User` accounts) associated with the closed election are automatically purged from the database, eliminating residual credentials on the LAN.
3. **No Unassigned State**: Devices are strictly bound 1:1 to their booth at creation time and are never in an "unassigned" state. Any invalid, terminated, or rotated device session routes canonically to `session_revoked.html`.
4. **Cleartext Password Distribution**: `Device.cleartext_password` persists the provisioned clear visible password exclusively to permit masked reveal/copy/print on the Booth Management setup screen for LAN station operators.

---

# 29. Election Freeze Invariant

Once:

```text
Election.status = ACTIVE
```

the election configuration becomes frozen.

Frozen data includes:

```text
positions
candidates
eligibility rules
booths
voter election allocation
election-relevant configuration
```

Normal CRUD operations must reject modifications to frozen configuration.

---

# 30. Credential Exception

Credential rotation and credential revocation are the explicit exception to the election configuration freeze.

During an active election, administrators may still:

```text
rotate credentials
revoke credentials
invalidate sessions
```

The logical Device identity remains unchanged. Rotating credentials updates `cleartext_password` and immediately terminates active device sessions.

---

# 31. Registry Invariants

The central voter registry is installation-wide and shared across elections. It must satisfy:

```text
primary_registry_value is unique across the installation
```

The system must not create duplicate Voter records when importing the same registry identity.

Subsequent imports should match existing voters using the configured primary registry identity.

The voter registry belongs to the installation as a whole and must never be scoped to individual users or administrators.

---

# 32. ElectionVoter vs Voter vs Vote

These three concepts must never be conflated.

```text
Voter
    =
persistent institutional registry identity

ElectionVoter
    =
that voter's participation in one election

Vote
    =
recorded candidate selection without voter identity
```

Therefore:

```text
Voter
   ↓
ElectionVoter
   ↓
has_voted
```

is separate from:

```text
Election
   ↓
Vote
   ↓
Candidate
```

This separation is intentional.

---

# 33. Authorization vs Vote

An Authorization proves temporary permission to enter the voting workflow.

It is not the vote itself.

```text
Authorization
    =
permission to cast one ballot

Vote
    =
persistent recorded selection
```

Therefore:

```text
Authorization USED
```

does not mean that the authorization itself contains the ballot.

The Vote records remain separate.

---

# 34. No Vote Identity Leakage

The following must never be persisted as part of Vote:

```text
voter_id
student_id
university_id
election_voter_id
authorization_id
booth_id
kiosk_id
```

unless a future explicit requirements change changes the ballot-secrecy model.

Operational systems may know that a voter was processed.

The persistent ballot record must not directly encode:

```text
Voter X selected Candidate Y
```

---

# 35. Audit/Data Separation

Operational auditing may record events such as:

```text
VOTER_VERIFIED
AUTHORIZATION_CREATED
AUTHORIZATION_CANCELLED
BALLOT_ACCEPTED
```

but must not record:

```text
voter X → candidate Y
```

and must not include candidate identity in an operational audit event that can be linked to a voter.

Auditability must not defeat ballot secrecy.

---

# 36. Client State Invariant

Client-side state is never authoritative.

This applies to:

* kiosk state;
* authorization state;
* election state;
* vote success;
* fullscreen/readiness;
* turnout;
* results visibility.

Example:

```text
Browser says:
    "authorized"

Server says:
    "CANCELLED"
```

The server wins.

Example:

```text
Browser says:
    "vote not submitted"

Server says:
    "USED"
```

The server wins.

The browser must resynchronize.

---

# 37. WebSocket State Invariant

WebSocket communication is transport, not authority.

A WebSocket event such as:

```text
kiosk.unlock
```

does not itself establish authorization.

The underlying database state must already contain the valid authorization.

Similarly:

```text
ballot.recorded
```

must only be emitted after the database transaction commits.

---

# 38. Transaction Boundary Invariant

Every critical state-changing voting operation must have an explicit transaction boundary.

At minimum:

```text
create_authorization()
submit_ballot()
close_election()
```

must use transactional protection.

For critical row coordination:

```python
transaction.atomic()
select_for_update()
```

must be used as appropriate.

---

# 39. Event Ordering Invariant

Never broadcast successful state before persistence.

Incorrect:

```text
WebSocket → "ballot.recorded"
       ↓
database commit
```

Correct:

```text
database transaction
       ↓
COMMIT
       ↓
transaction.on_commit()
       ↓
WebSocket → "ballot.recorded"
```

This prevents clients from observing state that never actually committed.

---

# 40. Failure Invariants

If ballot submission fails before commit:

```text
ElectionVoter.has_voted == FALSE
Authorization.status == ACTIVE
Vote records == NONE
```

The voter may retry.

If the transaction committed but the HTTP response was lost:

```text
ElectionVoter.has_voted == TRUE
Authorization.status == USED
Vote records EXIST
```

The voter must not be permitted to submit a second ballot.

The kiosk must discover the committed state through resynchronization.

---

# 41. Concurrency Invariants

The following concurrent operations must be safe:

```text
two authorization requests
two ballot submissions
authorization vs election closure
ballot submission vs election closure
credential rotation vs active session
reconnection vs active authorization
```

The implementation must rely on database transactions and locking rather than timing assumptions.

---

# 42. Required Database Constraints

At minimum, enforce the following through database constraints where practical.

```text
UNIQUE(ElectionVoter.election, ElectionVoter.voter)
```

Primary registry identity:

```text
UNIQUE(Voter.primary_registry_value)
```

Device identifiers:

```text
UNIQUE(Device.identifier)
```

Booth/device pairing:

```text
one Officer Device per Booth
one Kiosk Device per Booth
```

Active authorization:

```text
at most one ACTIVE authorization
per ElectionVoter
```

Single Administrator per installation:

```text
UNIQUE(User.role) WHERE role = 'ADMIN'
(or enforced via database constraint / validation layer ensuring count(role == ADMIN) <= 1)
```

The exact Django/PostgreSQL implementation may use:

* `UniqueConstraint`
* conditional `UniqueConstraint`
* foreign-key constraints
* check constraints

where appropriate.

---

# 43. Application-Level vs Database-Level Invariants

Not every invariant can be represented by a simple database constraint.

Use database constraints for:

```text
identity uniqueness
relationship uniqueness
basic field validity
```

Use services + transactions for:

```text
authorization validity
voter eligibility
election state
booth authorization
ballot validation
atomic vote recording
election closure
```

Use both when possible.

---

# 44. Data Model Rules for the Agent

When adding a new model or field, the implementation agent must ask:

1. What business concept does this represent?
2. Which existing entity owns it?
3. Is it election-wide or election-specific?
4. Does it contain voter identity?
5. Could it compromise ballot secrecy?
6. What uniqueness constraint is required?
7. What states can it have?
8. What transitions are legal?
9. Which service changes it?
10. Which transaction protects the change?
11. Can two concurrent requests violate its invariant?
12. Should the database enforce part of the invariant?

Do not add a model merely to make an implementation easier.

---

# 45. Forbidden Data-Model Shortcuts

Do not:

```text
store voter_id on Vote
```

Do not:

```text
store candidate_id on Voter
```

Do not:

```text
use Voter.has_voted
```

for election-specific voting state.

Voting status belongs to:

```text
ElectionVoter.has_voted
```

Do not:

```text
reuse an Authorization after USED
```

Do not:

```text
represent multi-position voting as independent authorization transactions
```

Do not:

```text
create separate voter tables for departments/classes/semesters
```

Academic structures belong in AcademicGroup relationships.

Do not:

```text
hardcode student_id as the universal registry identity
```

---

# 46. Core Invariant Checklist

The following must always remain true.

## Installation & Administration

```text
The installation maps to exactly one PostgreSQL database.
Elections and Voter Registry belong to the installation (no user ownership fields).
Exactly one human Administrator exists per installation (role == Role.ADMIN).
Administrator identity is resolved dynamically by role, never by hardcoded username or ID.
```

## Identity

```text
A device has one logical identity.
A device has at most one active session.
```

## Booth

```text
Every booth has exactly one Officer Device.
Every booth has exactly one Kiosk Device.
```

## Registry

```text
Primary registry identity is unique.
```

## Election

```text
An ElectionVoter is unique per (election, voter).
Active election configuration is frozen.
```

## Allocation

```text
Every eligible ElectionVoter has exactly one booth before polling.
```

## Authorization

```text
An ElectionVoter has at most one ACTIVE authorization.
An authorization belongs to exactly one booth.
The authorization kiosk belongs to that same booth.
USED authorization cannot be reused.
CANCELLED authorization cannot be reused.
```

## Voting

```text
A voter can have at most one accepted ballot per election.
A ballot is atomic.
A failed ballot creates no partial Vote records.
```

## Ballot secrecy

```text
Vote does not directly identify the voter.
Vote does not directly identify the authorization.
```

## Election closure

```text
No authorization after the effective election closure.
No ballot acceptance after the effective election closure.
```

## Client authority

```text
Client state is never authoritative.
Server state wins after reconnection.
```

## Events

```text
Success events are emitted only after successful database commit.
```

---

# 47. Critical Invariant Matrix

| Invariant                        | Primary Enforcement         | Secondary Verification |
| -------------------------------- | --------------------------- | ---------------------- |
| Single Administrator per install | Unique constraint / Service | Setup / User service   |
| Installation boundary (no owner) | Schema definition           | Architecture tests     |
| Unique voter registry identity   | Database constraint         | Import service         |
| Unique ElectionVoter             | Database constraint         | Service                |
| One booth allocation             | Database/service            | Start validation       |
| One Officer per booth            | Database constraint         | Start validation       |
| One Kiosk per booth              | Database constraint         | Start validation       |
| One active authorization         | Transaction + constraint    | Authorization service  |
| Authorization booth match        | Service                     | Database relationships |
| Authorization kiosk match        | Service                     | Database relationships |
| One vote per voter/election      | ElectionVoter + transaction | Tests                  |
| Atomic ballot                    | Transaction                 | Integration tests      |
| No voter identity on Vote        | Data model                  | Schema tests           |
| Election freeze                  | Service/state checks        | Integration tests      |
| No vote after closure            | Election lock + service     | Concurrency tests      |
| Server-authoritative kiosk state | Backend state               | Reconnection tests     |
| Success after commit             | `transaction.on_commit()`   | Failure tests          |

---

# 48. Data Integrity Is More Important Than CRUD Convenience

Electra should not optimize the data model for the easiest CRUD implementation.

The difficult problems are:

```text
concurrency
authorization
state transitions
transactional consistency
ballot secrecy
booth isolation
reconnection
```

The data model must make these properties explicit.

If a convenient implementation conflicts with an invariant, preserve the invariant.

---

# 49. Final Data Model Rule

The implementation agent must treat the following as non-negotiable:

```text
DATABASE STATE
    >
SERVICE LOGIC
    >
CLIENT STATE
```

More precisely:

```text
Database + server-side domain rules
            ↓
       authoritative
            ↓
       browser state
            ↓
       presentation only
```

The browser displays and interacts with election state.

It does not define election state.

---

# 50. Final Integrity Contract

A valid Electra vote must satisfy all of the following:

```text
Authenticated Kiosk
        AND
Correct Booth
        AND
Active Election
        AND
Valid Time
        AND
Valid ElectionVoter
        AND
has_voted = FALSE
        AND
Active Authorization
        AND
Authorization belongs to Kiosk
        AND
Authorization belongs to Booth
        AND
Every Position is valid
        AND
Every Candidate belongs to its Position
        AND
Voter is eligible for every Position
        AND
Exactly one selection per required Position
        ↓
    ATOMIC COMMIT
        ↓
All Vote records created
        AND
ElectionVoter.has_voted = TRUE
        AND
Authorization.status = USED
        AND
Kiosk becomes LOCKED
        ↓
      COMMIT
        ↓
Success event may be emitted
```

If any required condition fails:

```text
NO BALLOT IS ACCEPTED
```

If any operation inside the transaction fails:

```text
ROLLBACK EVERYTHING
```

That is the central data-integrity contract of Electra.
