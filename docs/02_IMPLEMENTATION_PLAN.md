# Electra — Implementation Plan

**Status:** LOCKED  
**Document Type:** Implementation Plan  
**Purpose:** Define the build order, dependencies, and completion gates for Electra.

> This document controls **sequence**, not detailed product behavior. Use `01-requirements.md` for requirements and the focused documents for architecture, data/invariants, state/flow, UI, coding, and testing. `electra-locked-in.txt` remains the highest-authority source of truth.

---

## 1. Build Strategy

Build incrementally. Each phase must leave the system runnable and testable.

```text
1. Foundation
2. Authentication / RBAC
3. Election Management
4. Voter Registry
5. Booth Management
6. Authorization
7. WebSocket Infrastructure
8. Kiosk State Machine
9. Voting
10. Reconnection / Resynchronization
11. Turnout / Results
12. Testing / Hardening
```

Do not implement later functionality by bypassing an unfinished dependency.

Phase boundaries are dependency and verification boundaries, not mandatory calendar deadlines.

---

# 2. Phase 1 — Foundation

### Goal

Establish the Django project, dedicated PostgreSQL database, and locked technical foundation for a self-hosted web application deployed on a Local-Area Network (LAN).

### Build

- Django project configuration for LAN access.
- Dedicated PostgreSQL database configuration (one database per installation).
- Installation initialization check (detects whether first-run setup is required).
- Django session authentication foundation.
- Django Channels / ASGI / Daphne configuration.
- `accounts`, `elections`, `voters`, and `voting` apps.
- Initial test configuration.
- Required project-level configuration and diagnostic health check.

### Depends on

None.

### Complete when

- Project starts cleanly and is accessible over LAN.
- Database connection works.
- Migrations run cleanly.
- Installation initialization status can be checked.
- Four domain apps are installed.
- ASGI/Channels configuration loads.
- Basic tests run.
- No forbidden infrastructure (Redis, Celery, multi-tenant schemas) has been introduced.

### Explicitly not required yet

Voting logic, authorization, kiosk workflow, application heartbeat, Redis, Celery, or external scheduler infrastructure.

---

# 3. Phase 2 — Authentication and RBAC

### Goal

Establish secure authenticated technical identities and the first-run Administrator setup.

### Build

- First-run installation setup view (creates the initial Administrator account).
- Custom `User` model with database-backed `ADMIN`, `OFFICER`, and `KIOSK` roles.
- Single-administrator enforcement (exactly one human Administrator per installation).
- Dynamic Administrator identity (no hardcoded username, fixed ID, or settings constant).
- `Device`.
- Device credentials.
- Django password hashing.
- Credential rotation/revocation.
- `DeviceSession`.
- One-active-session enforcement for Officer and Kiosk devices.
- Role and permission enforcement.
- Login/logout.

### Depends on

Phase 1.

### Complete when

- Uninitialized installation routes to first-run Administrator creation.
- Initialized installation disables further public Administrator creation.
- Administrator can authenticate using normal Django session authentication.
- Administrator identity is stored normally in the database without hardcoded assumptions.
- Officer and Kiosk technical identities can authenticate.
- Role boundaries are strictly enforced.
- A device cannot have two simultaneous active sessions.
- Rotation invalidates the previous session.
- Revocation prevents continued access.
- Replacement hardware can use the same logical device identity.
- No public registration, SaaS account management, or multi-admin features exist.

### Verify

First-run setup, authentication, permissions, session exclusivity, rotation, revocation, unauthorized access, and single-admin enforcement.

---

# 4. Phase 3 — Election Management

### Goal

Implement election configuration and lifecycle belonging to the installation.

### Build

- `Election` (belongs to the installation; no `owner` field).
- `Position`.
- `Candidate`.
- Eligibility configuration.
- Election configuration UI.
- Configuration validation.
- `DRAFT → ACTIVE → CLOSED → RESULTS_PUBLISHED`.
- Start and close operations.
- Configuration freeze after activation.
- Server-side election deadline enforcement.

### Depends on

Phase 2.

### Complete when

- Admin can create/configure elections for the installation.
- No per-user election ownership or `Election.owner` field exists.
- Positions and candidates can be managed.
- Eligibility can be configured.
- Invalid elections cannot start.
- Valid elections can become `ACTIVE`.
- Active election configuration is frozen.
- Elections can close.
- Results cannot be published before closure.
- Expired elections reject new voting operations.

### Rule

Do not create a separate election `READY` state. Readiness is a configuration-validation result.

---

# 5. Phase 4 — Voter Registry

### Goal

Implement the installation's central voter registry and election enrollment.

### Build

- `Voter` (scoped to the installation; no tenant/user ownership).
- `AcademicGroup`.
- Configurable primary registry identity.
- Configurable academic hierarchy.
- CSV import.
- Excel import.
- Import preview/validation.
- Duplicate detection.
- Voter search/filtering.
- Election enrollment.
- `ElectionVoter`.

### Depends on

Phase 3.

### Complete when

- Voters can be created and maintained in the installation registry.
- Registry identity is configurable and unique within the installation.
- Academic hierarchy is configurable.
- CSV/Excel imports use validation before confirmation.
- Duplicate registry records are prevented.
- Voters can be enrolled in an election.
- `ElectionVoter` uniquely represents a voter's participation in an election.
- No per-user or tenant registry partitioning exists.

### Deferred

Arbitrary OCR and non-core PDF import.

---

# 6. Phase 5 — Booth Management

### Goal

Configure polling booths and voter allocation.

### Build

- `Booth`.
- Officer Device ↔ Booth binding.
- Kiosk Device ↔ Booth binding.
- Exactly one Officer and one Kiosk per booth.
- Booth configuration UI.
- Individual allocation.
- Bulk/filter allocation.
- Pre-election reallocation.
- Pre-election booth add/remove.
- Voter → Booth export/print view.

### Depends on

Phases 2–4.

### Complete when

- Every booth has exactly one Officer Device and one Kiosk Device.
- Participating voters can be allocated.
- Every `ElectionVoter` has exactly one booth before activation.
- Cross-booth authorization is impossible.
- Booth configuration cannot change after activation.
- Removing a booth requires affected voters to be reallocated before activation.

---

# 7. Phase 6 — Authorization

### Goal

Implement the Officer → Authorization → Kiosk bridge.

### Build

- `VoterAuthorization`.
- Officer voter verification.
- Eligibility checks.
- Booth-allocation checks.
- Kiosk readiness checks.
- Authorization creation/cancellation.
- `ACTIVE`, `USED`, and `CANCELLED` authorization states.

### Depends on

Phases 2–5.

### Complete when

Authorization is rejected unless all required conditions are valid:

- election active;
- voting time valid;
- voter enrolled;
- voter eligible;
- voter belongs to officer's booth;
- voter has not voted;
- paired kiosk connected;
- paired kiosk ready;
- kiosk fullscreen;
- no active authorization already exists.

Authorization creation must be transactional.

Kiosk unlock must occur only after successful authorization commit.

---

# 8. Phase 7 — WebSocket Infrastructure

### Goal

Establish real-time operational communication.

### Build

- Channels routing.
- Authenticated WebSocket connections.
- Required booth/election/admin groups.
- Connection/disconnection events.
- Readiness/fullscreen events.
- Lock/unlock notifications.
- Authorization notifications.
- Election lifecycle events.
- Ballot/turnout notifications.
- Resynchronization messages.

### Depends on

Phases 2–6.

### Complete when

- Authorized clients connect.
- Server identifies the technical device.
- Server derives the device's booth.
- Connection/disconnection state is reliable.
- Events reach only their intended clients.
- Candidate selections never reach Officer Stations.
- Voter identity never reaches Kiosks.

### Explicit exclusion

**No application-level heartbeat.** Persistent WebSocket connection lifecycle is the liveness mechanism.

---

# 9. Phase 8 — Kiosk State Machine

### Goal

Implement server-authoritative kiosk runtime.

### Build

Kiosk states:

```text
OFFLINE
NOT_READY
READY
UNLOCKED
VOTING
LOCKED
```

Implement:

- readiness reporting;
- Fullscreen API readiness signal;
- server-side lock/unlock;
- authorization-driven unlock;
- lock/reset behavior;
- state synchronization;
- corresponding kiosk UI states.

### Depends on

Phases 6–7.

### Complete when

- Kiosk starts locked.
- Kiosk cannot unlock without valid server authorization.
- Fullscreen exit makes the kiosk not ready.
- Officer cannot authorize through a not-ready kiosk.
- Successful voting returns the kiosk to locked.
- Browser state cannot override server state.

---

# 10. Phase 9 — Voting

### Goal

Implement the complete atomic ballot workflow.

### Build

- Ballot generation.
- Eligibility-filtered positions.
- Candidate validation.
- Multi-position ballot UI.
- Complete ballot submission over HTTP.
- Atomic vote transaction.
- Duplicate-vote prevention.
- Authorization consumption.
- Kiosk locking after commit.
- Post-commit success/events.

### Depends on

Phases 1–8.

### Complete when

A successful ballot follows:

```text
complete ballot submitted
        ↓
server validates entire ballot
        ↓
database transaction commits
        ↓
Vote records created
        ↓
ElectionVoter.has_voted = true
        ↓
Authorization = USED
        ↓
kiosk locked
        ↓
success returned/broadcast
```

A failed transaction leaves no partial ballot.

A voter cannot submit a second accepted ballot.

The actual vote is submitted through HTTP, never WebSocket.

### Critical concurrency

Use the transaction and row-lock rules defined by the architecture/data contracts.

---

# 11. Phase 10 — Reconnection / Resynchronization

### Goal

Make kiosk behavior correct across disconnects, reconnects, and lost responses.

### Build

On reconnection:

1. Authenticate the Django session.
2. Identify the kiosk.
3. Derive its booth from the server-side device binding.
4. Read authoritative election state.
5. Read authoritative kiosk state.
6. Read authorization state.
7. Resynchronize the browser.
8. Ignore stale local state.

Handle:

- reconnect before voting;
- reconnect during active authorization;
- browser close after authorization;
- committed vote with lost HTTP response;
- cancelled authorization;
- election closure while disconnected.

### Complete when

- A committed ballot is never shown as uncommitted.
- An uncommitted ballot is never shown as successfully recorded.
- Lost responses can be resolved from authoritative server state.

---

# 12. Phase 11 — Turnout and Results

### Goal

Complete live election monitoring and post-election results.

### Build

Turnout:

- total eligible voters;
- total voters who voted;
- turnout percentage;
- per-booth activity;
- live updates after committed ballots.

Results:

- result calculation;
- Position → Candidate → Vote count;
- result publication;
- results UI.

### Depends on

Phase 9.

### Complete when

- Turnout changes only after successful ballot commitment.
- Candidate-wise results are not exposed during active polling.
- Results can be calculated after closure.
- Results can be published only after closure.
- Results are based on persisted Vote records.

---

# 13. Phase 12 — Testing and Hardening

### Goal

Verify correctness, security, integrity, concurrency, and the complete lifecycle.

### Verify

- Model/database constraints.
- Service logic.
- Views.
- WebSockets.
- Integration.
- Full election lifecycle.
- Permissions/security.
- Concurrency.
- Failure/retry behavior.
- Reconnection.
- Import/allocation.
- UI acceptance.
- Data integrity.

### Mandatory failure scenarios

- duplicate authorization;
- duplicate ballot submission;
- simultaneous ballot submissions;
- vote vs election-close race;
- authorization vs election-close race;
- failed ballot transaction;
- commit succeeds but response is lost;
- kiosk disconnect/reconnect;
- fullscreen exit;
- revoked credentials;
- rotated credentials;
- stale browser state;
- cross-booth access;
- unauthorized WebSocket access;
- incomplete election configuration;
- invalid voter allocation.

### Complete when

All critical invariants pass, the full election lifecycle passes, and no known critical security, ballot-secrecy, integrity, concurrency, or state-management defect remains.

---

# 14. Cross-Phase Rules

These rules apply throughout the build.

### Deployment and Installation Boundary

Electra is a self-hosted web application deployed on a Local-Area Network (LAN):

- One installation = one PostgreSQL database = one human Administrator.
- Separate installations are completely independent.
- The browser is the client, communicating with Django via HTTP and WebSockets over the LAN.
- The Administrator is created during first-run setup and must not be hardcoded.
- Do not introduce SaaS platforms, multi-tenancy, tenant IDs, per-user ownership (`Election.owner`), or public user registration.

### Architecture

Keep business logic in the correct layer:

```text
HTTP → Views → Services → Models/Selectors
WebSocket → Consumers → Services → Models/Selectors
```

Views and consumers orchestrate communication.

Services own domain operations and transaction boundaries.

Models enforce persistence constraints and simple model behavior.

Complex reads belong in the appropriate query/selector layer.

### Server authority

Never rely on browser state for:

- authorization validity;
- eligibility;
- booth identity;
- election state;
- vote validity;
- duplicate-vote prevention;
- successful vote confirmation.

### Ballot secrecy

Do not create voter-to-candidate linkage through:

- models;
- API responses;
- WebSocket events;
- logs;
- audit records;
- UI.

### Booth isolation

The server derives booth identity from the authenticated technical identity.

Do not trust a client-supplied booth identifier as authority.

### Atomicity

A multi-position ballot is one logical ballot and one atomic database operation.

### Configuration freeze

Do not introduce normal election-configuration mutations after activation.

Credential rotation/revocation remains the deliberate operational exception.

### Removed infrastructure

Do not introduce:

- application heartbeat;
- Redis;
- Celery;
- external scheduler;
- JWT;
- React SPA;
- microservices;
- offline voting.

Only an explicit locked requirements change can alter this scope.

---

# 15. Phase Completion Gate

A phase is complete only when:

```text
Implementation
    ↓
Tests pass
    ↓
Relevant invariants checked
    ↓
Architecture boundaries checked
    ↓
No scope expansion introduced
    ↓
Acceptance criteria satisfied
    ↓
Phase reported complete
```

Code existing is not sufficient to mark a phase complete.

Unresolved critical failures keep the phase incomplete.

---

# 16. Final End-to-End Gate

The implementation is complete only when this lifecycle works without violating the locked contracts:

```text
Admin configures election
        ↓
Voters enrolled and allocated
        ↓
Booths/devices configured
        ↓
Election validated
        ↓
Election starts
        ↓
Officer authenticates
        ↓
Kiosk connects and becomes ready
        ↓
Officer verifies voter
        ↓
Officer authorizes voter
        ↓
Kiosk unlocks
        ↓
Voter completes ballot
        ↓
Server validates complete ballot
        ↓
Atomic commit
        ↓
Authorization USED
        ↓
Kiosk LOCKED
        ↓
Turnout updated
        ↓
Election closes
        ↓
Further authorization/voting rejected
        ↓
Results calculated/published
```

Every stage must satisfy the relevant requirements, architecture rules, state transitions, data invariants, and testing/Definition-of-Done contract.

---

# 17. Rule for Choosing the Next Task

Before implementing a change:

1. Check this phase plan.
2. Check `01-requirements.md`.
3. Read the relevant focused contract.
4. Inspect the existing code.
5. Implement the smallest change that completes the current phase.
6. Run the required tests.
7. Check invariants and architecture boundaries.
8. Check that scope has not expanded.
9. Report the phase status.
10. Proceed only when the phase completion gate is satisfied.

The implementation plan controls **sequence**. It does not override the product requirements or the higher-authority locked specification.
