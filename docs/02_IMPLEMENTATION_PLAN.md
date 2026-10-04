# Electra — Implementation Plan

**Status:** LOCKED  
**Document Type:** Implementation Plan  
**Purpose:** Define the vertical slice build order, workflow dependencies, and completion gates for Electra.

> This document controls **sequence and workflow execution**, not detailed product behavior. Use `01_REQUIREMENTS.md` for requirements and the focused documents for architecture, data/invariants, state/flow, UI, coding, and testing. `electra-locked-in.txt` remains the highest-authority source of truth.

---

# 1. Implementation Strategy — Vertical Slices

Implement Electra vertically, workflow by workflow.

Do NOT implement the entire backend first and the entire frontend afterward.

Do NOT create isolated models, services, WebSocket consumers, templates, or UI pages merely because they belong to a particular technical layer.

Instead, take one meaningful user workflow and make that workflow work end-to-end through the existing architecture.

For each vertical slice:

1. Identify the exact user workflow.
2. Trace the workflow through the existing architecture, data model, state machines, invariants, authentication and authorization rules.
3. Implement only the backend/domain pieces required for that workflow.
4. Implement the required frontend interfaces.
5. Implement WebSocket communication where that workflow requires it.
6. Connect the actual frontend to the actual backend.
7. Test the complete workflow with realistic state transitions.
8. Verify the relevant invariants and failure cases.
9. Only after the slice works end-to-end, move to the next slice.

Example:

DO NOT:
"Build all WebSocket functionality."

INSTEAD:
"Implement voter authorization from Officer Station to Voting Kiosk."

The complete slice should be:

Officer Station  
→ identify/select eligible voter  
→ server validates voter and election state  
→ create the appropriate single-use authorization  
→ server sends authorization state through Django Channels/WebSocket  
→ Kiosk receives authorization  
→ Kiosk transitions to the authorized/unlocked state  
→ voter can proceed  
→ authorization remains valid according to the existing state machine  
→ invalid/expired/replayed authorization is rejected  
→ relevant state is synchronized to the Officer Station  
→ reconnect/resynchronization follows the existing architecture.  

Once that workflow is working and tested, proceed to the next vertical slice.

---

# 2. Vertical Slice Rule

A slice is NOT complete merely because:
- the model exists,
- the API exists,
- the WebSocket consumer exists,
- the template exists,
- or the page renders.

A slice is complete only when the actual user workflow can be executed end-to-end through the application and its important failure cases have been tested.

---

# 3. Order

Use the existing architecture and implementation plan to determine the exact technical dependencies, but generally organize implementation around complete workflows rather than technical layers.

The election setup workflow should become functional end-to-end first:

1. Create/save an election draft.
2. Configure election voters.
3. Configure positions and candidates.
4. Configure booths and devices.
5. Allocate voters.
6. Validate the election.
7. Save as draft / start election.

Then implement operational election workflows vertically:

8. Officer authentication and booth readiness.
9. Voter authorization.
10. Kiosk authorization reception and unlocking.
11. Ballot submission.
12. Transactional vote recording and authorization consumption.
13. Kiosk return to locked/ready state.
14. Real-time turnout and device synchronization.
15. Reconnection/state resynchronization.
16. Election closure.
17. Result publication.

Do not assume that the order above overrides the state machines or architecture. Resolve dependencies using the existing locked documents.

---

# 4. No Premature Abstraction

Do not build speculative infrastructure "for later."

Before introducing a service, abstraction, API, component, or utility, identify which current vertical slice requires it.

However, do not duplicate code when an existing architectural component already provides the correct abstraction.

Follow the existing coding standards.

---

# 5. After Each Slice

Before moving to the next slice:

- run the relevant tests
- manually verify the workflow where appropriate
- inspect database state where necessary
- verify state transitions
- verify relevant invariants
- verify authorization boundaries
- verify WebSocket behavior where applicable
- verify failure/rejection paths
- confirm that the implementation still matches the locked architecture

Then record the completed slice and any discovered implementation issue before proceeding.

---

# 6. Technical Foundations (Prerequisite Baseline)

The foundation infrastructure provides the runtime environment for executing vertical slices.

### Foundation & Identity Baseline
- **LAN Deployment Setup**: Self-hosted Django project configured for Local-Area Network access (`http://192.168.x.x:8000`), dedicated PostgreSQL database per installation.
- **Dynamic First-Run Setup**: Uninitialized installation detection (`UNINITIALIZED → INITIALIZED`), dynamically creating exactly one Administrator (`role = ADMIN`). No hardcoded usernames or IDs.
- **Technical Accounts & Device Sessions**: Technical device identities (`Device`, `device_type = OFFICER / KIOSK`), password hashing, one-active-session exclusivity (`DeviceSession`), session revocation/rotation.
- **Channels & ASGI Configuration**: Daphne/ASGI foundation ready for domain-specific WebSocket consumers.
- **Central Master Voter Registry**: `Voter`, `AcademicGroup`, configurable primary registry identity, CSV/Excel preview and transactional import, persistent grouped display (`references/04_voter_registry.png`).

---

# 7. Part 1 — Election Setup Workflows (Slices 1–7)

The election setup flow guides the administrator through the locked four-stage wizard.

### Slice 1: Create and Save Election Draft
- **Workflow**: Admin navigates from Authenticated Home (`references/03_home.png`) → clicks **Start Election** → system initializes a new election draft (`status = DRAFT`) belonging to the installation.
- **Architecture & Invariants**: Enforce installation ownership (no `Election.owner` or tenant scoping). Enforce active election constraint: if an election is already `ACTIVE`, initiating another election is rejected.
- **Backend / Domain**: `elections.services.create_election_draft()`, model persistence.
- **Frontend**: Election creation modal or transition to Stage 1.
- **Test Scenarios**: Successful draft creation, draft persistence, rejection if an active election exists.

### Slice 2: Configure Election Voters (Stage 1)
- **Workflow**: In Stage 1 (`references/06.1_voter_list.png`), Admin views eligible voters from the Master Voter Registry or imports voters directly via bulk CSV/Excel into the election. Admin selects voters (individually or in bulk) to enroll into the election as `ElectionVoter` records.
- **Architecture & Invariants**: `unique(election, voter)`. Registry identity matching prevents duplicates. Voters are scoped to this election.
- **Backend / Domain**: `voters.services.enroll_voters()`, `voters.importers.import_election_voters()`.
- **Frontend**: Stage 1 template (`references/06.1_voter_list.png`): dense/grouped voter roster, search/filter controls, bulk selection, counter badges, next navigation.
- **Test Scenarios**: Bulk enrollment, duplicate prevention, selection filtering, navigation state.

### Slice 3: Configure Positions and Candidates (Stage 2)
- **Workflow**: In Stage 2 (`references/06.2_election_details.png`), Admin defines election title, voting period (`starts_at`, `ends_at`), positions, candidate assignments from enrolled election voters, editable candidate symbols, and position eligibility rules.
- **Architecture & Invariants**: Positions belong to the election. Candidates belong to a position. Eligibility rules follow (OR within rule, AND across rules).
- **Backend / Domain**: `elections.services.update_election_details()`, `elections.services.create_position()`, `elections.services.add_candidate()`, `elections.services.set_eligibility()`.
- **Frontend**: Stage 2 template (`references/06.2_election_details.png`): single coherent workspace with modal candidate picker, symbol editing, and position accordion/card lists.
- **Test Scenarios**: Position CRUD, candidate selection from enrolled voters, candidate validation, eligibility rule persistence.

### Slice 4: Configure Booths and Devices (Stage 3 — Setup)
- **Workflow**: In Stage 3 (`references/06.3_booth&allocation.png`), Admin adds/configures polling booths. For each booth, system generates exactly one paired Officer Station device and one Voting Kiosk device, complete with masked credentials and one-click credential rotation. Lightweight printing actions (`Print voter list`, `Print booth slips`) are available.
- **Architecture & Invariants**: Exactly one Officer and one Kiosk per booth. Derived booth isolation. Credential rotation revokes previous sessions.
- **Backend / Domain**: `voters.services.create_booth()`, `accounts.services.provision_booth_devices()`, `accounts.services.rotate_device_credentials()`.
- **Frontend**: Stage 3 template (`references/06.3_booth&allocation.png`): borderless booth cards, device pairing display, masked passwords, rotate pass button, discrete print buttons.
- **Test Scenarios**: Booth creation, 1:1 device binding, credential generation/rotation, session revocation on rotation.

### Slice 5: Allocate Voters to Booths (Stage 3 — Allocation)
- **Workflow**: In Stage 3 (`references/06.3_booth&allocation.png`), Admin allocates enrolled `ElectionVoter` records to booths automatically (balanced distribution) or manually adjusts them. Pre-election booth addition or removal prompts necessary voter reallocation.
- **Architecture & Invariants**: Every `ElectionVoter` must be assigned to exactly one Booth. No unallocated voters allowed before election start.
- **Backend / Domain**: `voters.services.allocate_voters_balanced()`, `voters.services.reallocate_voter()`, `voters.services.remove_booth_and_reallocate()`.
- **Frontend**: Stage 3 allocation controls: auto-allocate button, drag/select manual adjustment, per-booth voter count and academic group breakdown.
- **Test Scenarios**: Balanced allocation math, manual reallocation, booth deletion with forced reallocation, booth-bound uniqueness invariant.

### Slice 6: Validate Election Configuration (Stage 4 — Verification)
- **Workflow**: Admin proceeds to Stage 4 Review & Start (`references/06.4_review.png`). Server executes comprehensive configuration validation checking election fields, positions, candidates, enrolled voters, 100% booth allocation, and device runtime readiness (Officer/Kiosk logged in and connected).
- **Architecture & Invariants**: Read-only validation service. Distinguishes `Valid / Ready`, `Incomplete / Invalid`, and `Device Not Ready`. No separate `READY` database state.
- **Backend / Domain**: `elections.services.validate_election_configuration()`.
- **Frontend**: Stage 4 verification screen (`references/06.4_review.png`): status checklist, detailed validation warnings/blockers, runtime device connectivity badges.
- **Test Scenarios**: Missing candidates rejection, unallocated voters rejection, unauthenticated devices warning/blocker, fully valid election confirmation.

### Slice 7: Save as Draft / Start Election (Stage 4 → ACTIVE)
- **Workflow**: On Stage 4 (`references/06.4_review.png`), Admin can choose **Save as Draft** (returns to Home) or **Start Election** (transitions `DRAFT → ACTIVE`).
- **Architecture & Invariants**: Atomic transition `DRAFT → ACTIVE`. Locks election configuration completely (freeze on booths, candidates, positions, voter allocation, eligibility). Only credential rotation remains permitted. Exclusivity: exactly one active election across installation.
- **Backend / Domain**: `elections.services.start_election()`, atomic state transition, configuration freeze enforcement.
- **Frontend**: Start Election confirmation modal, redirect to Live Election Dashboard (`references/07_live.png`).
- **Test Scenarios**: Transition validation, concurrent start prevention, configuration freeze validation (mutations rejected once active).

---

# 8. Part 2 — Operational Election Workflows (Slices 8–17)

Operational workflows execute live polling over the LAN with strict booth isolation, server authority, and ballot secrecy.

### Slice 8: Officer Authentication and Booth Readiness
- **Workflow**: Officer launches browser on their station (`http://192.168.x.x:8000/login/`) → logs in with device credentials → server identifies device, derives its bound Booth, locks session exclusivity, and connects Officer WebSocket. Officer sees the bound Officer Station Dashboard (`voting/officer/dashboard.html`).
- **Architecture & Invariants**: Technical identity authentication. Derived booth (Officer A → Booth A only). One active session enforcement.
- **Backend / Domain**: `accounts.services.authenticate_device()`, `accounts.consumers.OfficerConsumer`.
- **Frontend**: Officer Station dashboard: booth indicator, voter search box, kiosk status indicator.
- **Test Scenarios**: Successful login, booth derivation, reject concurrent second login on same device, WebSocket channel subscription.

### Slice 9: Voter Authorization
- **Workflow**: Voter arrives at Officer Station. Officer searches voter by identity/name → verifies eligibility and booth allocation → checks that Kiosk is connected, ready, and fullscreen → clicks **Authorize**. Server transactionally creates single-use `VoterAuthorization(status = ACTIVE)`.
- **Architecture & Invariants**: Row locking order (`Election` → `ElectionVoter` → `Kiosk`). Server validates: election ACTIVE, within voting window, voter enrolled, voter belongs to officer's booth, `has_voted == False`, kiosk connected/ready/fullscreen, no existing ACTIVE authorization for kiosk.
- **Backend / Domain**: `voting.services.create_authorization()`, `transaction.atomic()`, `select_for_update()`.
- **Frontend**: Officer verification screen, authorize button with loading state, active authorization confirmation.
- **Test Scenarios**: Valid authorization, rejection if voter belongs to other booth, rejection if voter already voted, rejection if kiosk not ready, rejection if another authorization is active.

### Slice 10: Kiosk Authorization Reception and Unlocking
- **Workflow**: On authorization commit, server broadcasts `kiosk.unlock` event via Django Channels to the paired Kiosk WebSocket. Kiosk unlocks, transitions from `LOCKED` to `UNLOCKED/VOTING`, and displays the voter's ballot interface. Officer station receives status sync.
- **Architecture & Invariants**: `transaction.on_commit()` event emission. Server-authoritative kiosk state transition. No voter identity transmitted to Kiosk.
- **Backend / Domain**: `voting.consumers.KioskConsumer`, `voting.services.unlock_kiosk()`.
- **Frontend**: Kiosk screen transitions smoothly from locked waiting screen (`references/kiosk/locked.html`) to voting interface (`references/kiosk/ballot_position.html`).
- **Test Scenarios**: Unlock event received only by bound kiosk, no voter identity leaked in payload, kiosk state transition to UNLOCKED, pre-commit failure emits no event.

### Slice 11: Ballot Submission
- **Workflow**: Voter on Kiosk interacts with position-by-position ballot screens, selecting candidates. Voter reviews complete choices on review screen (`references/kiosk/review.html`) → clicks **Submit Ballot**. Complete ballot is posted over HTTP.
- **Architecture & Invariants**: Multi-page UI, single complete ballot submission over HTTP (never WebSocket). Temporary local state in browser until submit. Candidate selections are never exposed to Officer or logs.
- **Backend / Domain**: `voting.views.SubmitBallotView`, ballot payload validation.
- **Frontend**: Step-by-step position screens, candidate cards with symbols, review screen, single-submit button with duplicate click prevention.
- **Test Scenarios**: Multi-position selection, eligibility filtering (voter sees only positions they qualify for), payload structure validation, duplicate submission blocked at UI.

### Slice 12: Transactional Vote Recording and Authorization Consumption
- **Workflow**: Server receives complete ballot via HTTP. Within an atomic transaction with row locking, validates election state, authorization `ACTIVE`, voter `has_voted == False`, and every position/candidate. Creates anonymous `Vote` records, sets `ElectionVoter.has_voted = True`, sets `Authorization.status = USED`, locks Kiosk.
- **Architecture & Invariants**: Lock order: `Election` → `ElectionVoter` → `Authorization` → `Kiosk`. Atomicity: all votes or none. Ballot secrecy: `Vote` contains only `(election, candidate, created_at)`—strictly no voter or authorization foreign keys.
- **Backend / Domain**: `voting.services.submit_ballot()`, `transaction.atomic()`, `select_for_update()`.
- **Frontend**: HTTP JSON response `{success: true}`.
- **Test Scenarios**: Atomic commit, all `Vote` records created, `has_voted` set to true, authorization marked `USED`, rollback on partial/invalid candidate selection, concurrent double submission rejected.

### Slice 13: Kiosk Return to Locked/Ready State
- **Workflow**: Kiosk receives HTTP success response → displays **Vote recorded successfully** on success screen (`references/kiosk/success.html`) → automatically clears browser selection state → locks and returns to waiting screen (`references/kiosk/locked.html`).
- **Architecture & Invariants**: Transient success feedback, zero persistence of candidate choices in browser, kiosk runtime state becomes `LOCKED`.
- **Backend / Domain**: Kiosk runtime state synchronization.
- **Frontend**: Success screen with timeout/reset, clean transition back to waiting for next authorization.
- **Test Scenarios**: Selection memory wipe, return to locked screen, rejection of back-navigation or resubmission.

### Slice 14: Real-time Turnout and Device Synchronization
- **Workflow**: Following successful ballot commitment, server emits `ballot.recorded` and `turnout.updated` WebSocket events to Live Election Dashboard (`references/07_live.png`) and Officer Stations. Dashboard updates total ballots cast, turnout percentage, and booth-specific metrics in real time.
- **Architecture & Invariants**: `transaction.on_commit()` event broadcast. Live turnout derived from server counts. Zero candidate selection or voter link exposed in turnout events.
- **Backend / Domain**: `voting.services.calculate_turnout()`, `voting.consumers.AdminLiveConsumer`.
- **Frontend**: Live Election Dashboard (`references/07_live.png`): real-time animated counters, booth activity status, Officer/Kiosk connectivity badges.
- **Test Scenarios**: Turnout increments only on committed ballot, event delivered to Live Dashboard, no candidate data in event, correct booth-specific count.

### Slice 15: Reconnection and State Resynchronization
- **Workflow**: Kiosk or Officer browser disconnects, refreshes, or loses LAN connection. Upon reconnecting, WebSocket authenticates session, derives device/booth, reads authoritative server state, and sends synchronization message restoring exact server state.
- **Architecture & Invariants**: Authoritative server reconstruction. Never trust local browser state. If vote committed but response was lost: recovery detects `Authorization = USED` and displays success/locked, preventing second vote.
- **Backend / Domain**: `voting.services.resynchronize_kiosk()`, `voting.consumers.KioskConsumer.connect()`.
- **Frontend**: Reconnecting overlay, seamless state restoration to appropriate screen.
- **Test Scenarios**: Reconnect during active authorization, reconnect after vote commit with dropped HTTP response, reconnect after officer cancellation, reject stale local state.

### Slice 16: Election Closure
- **Workflow**: Admin clicks **End Election** on Live Dashboard (or election reaches `ends_at`). Server locks `Election` row, transitions `ACTIVE → CLOSED`, cancels any dangling `ACTIVE` authorizations, broadcasts `election.closed` WebSocket event to all stations, and rejects all subsequent authorizations and ballot submissions.
- **Architecture & Invariants**: Lock `Election` row to prevent race with ongoing votes. Expired/closed elections immediately reject new authorizations and ballot submissions.
- **Backend / Domain**: `elections.services.close_election()`, `voting.services.cancel_active_authorizations()`.
- **Frontend**: End Election confirmation modal, Live Dashboard closed banner, Officer/Kiosk transition to election-ended view.
- **Test Scenarios**: Vote vs close race condition (deterministic boundary), immediate rejection of voting after closure, dangling authorization cancellation, closure broadcast.

### Slice 17: Result Publication
- **Workflow**: After election is `CLOSED`, Admin accesses Election Results (`references/08_result.png`). Server calculates tally exclusively from persisted `Vote` records: Position → Candidate → Vote count. Displays position breakdowns, vote shares, highlights winners, and enables official paper tally printing (`Print Results`).
- **Architecture & Invariants**: Results strictly inaccessible while election is `ACTIVE`. Derived exclusively from anonymous `Vote` records. Winner calculation handles ties correctly.
- **Backend / Domain**: `voting.services.calculate_results()`, `voting.views.ResultsView`.
- **Frontend**: Results screen (`references/08_result.png`): position cards, vote counts, percentage bars, winner badge, `Print Results` button, navigation back to Home.
- **Test Scenarios**: Results hidden while active, accurate tally matching votes, winner identification, multi-position tally, print view stylesheet.

---

# 9. Cross-Slice Invariants and Architectural Rules

These core invariants apply across every vertical slice:

### Deployment and Installation Boundary
- Electra is a self-hosted web application deployed on a dedicated Local-Area Network (LAN).
- Exactly one PostgreSQL database per installation.
- Exactly one human Administrator per installation (`role = ADMIN`), provisioned dynamically during first-run setup.
- Exactly **one active election** at any time across the entire installation.
- Strictly no SaaS, multi-tenancy, per-user election ownership (`Election.owner`), or public user registration.

### Architecture Layering & Responsibilities
- Strictly follow: `Browser → HTTP Views / WebSocket Consumers → Services → Selectors / Models → PostgreSQL`.
- Services own domain operations, business rules, and transaction boundaries.
- Models own persistence, database constraints, and local integrity.
- Templates and JavaScript handle presentation and user interactions only.
- No business logic duplicated across views, consumers, forms, or JavaScript.

### Server Authority
- The server is the sole authority on all validation: voter eligibility, booth binding, election timing, authorization status, kiosk readiness, and ballot validity.
- Client-side validation is strictly for usability and never serves as proof of authorization or commit.

### Ballot Secrecy
- `Vote` records contain only `(election, candidate, created_at)`.
- Never associate `Vote` with `voter_id`, `election_voter_id`, or `authorization_id`.
- Never expose candidate selections to Officers, WebSocket broadcasts, or operational audit logs.

### Booth Isolation
- Officer and Kiosk devices are strictly bound to their assigned Booth.
- The server derives booth identity exclusively from the authenticated `Device` session.
- Client-supplied booth IDs are never trusted.

### Atomicity and Concurrency
- Critical operations execute inside `transaction.atomic()` with `select_for_update()`.
- Preserve the canonical row lock order: `Election → ElectionVoter → Authorization → Kiosk`.
- Post-commit events must use `transaction.on_commit()`.

### Configuration Freeze
- Once an election becomes `ACTIVE`, all configuration (booths, candidates, positions, voter allocation, eligibility) is permanently frozen.
- Credential rotation/revocation is the sole deliberate exception permitted during an active election.

### Removed Infrastructure
- No Redis, Celery, APScheduler, external message brokers, JWT, React SPA, microservices, or offline voting.
- Persistent WebSocket connection lifecycle is the liveness mechanism (no application-level heartbeat).

---

# 10. Slice Completion Gate

A vertical slice is NOT complete merely because individual components exist. Before marking a slice complete and moving to the next:

```text
[ ] Exact user workflow identified and traced end-to-end
[ ] Backend/domain pieces implemented in owning app services
[ ] Required frontend interfaces created matching references/
[ ] WebSocket communication implemented where workflow requires it
[ ] Frontend connected to backend and exercised end-to-end
[ ] Relevant tests written and passing
[ ] Invariants and failure paths verified
[ ] Security, booth isolation, and server authority checked
[ ] No duplicated business logic or premature abstractions introduced
```

---

# 11. Final End-to-End Gate

Electra is complete only when the full end-to-end lifecycle executes seamlessly without violating any locked contract:

```text
Admin logs in (Home)
      ↓
Start Election (Draft created)
      ↓
Stage 1: Configure Election Voters (enrolled from Master Registry / bulk import)
      ↓
Stage 2: Configure Details & Candidates (single workspace, editable symbols, eligibility)
      ↓
Stage 3: Configure Booths & Allocation (paired devices, credentials, balanced allocation, print slips)
      ↓
Stage 4: Review & Start (validation verified, runtime readiness checked)
      ↓
Election Starts (DRAFT → ACTIVE, configuration frozen)
      ↓
Live Election Dashboard active (real-time turnout monitoring)
      ↓
Officer logs in (bound to Booth, single active session)
      ↓
Kiosk connects (fullscreen readiness signal, locked state)
      ↓
Officer verifies and authorizes voter (transactional ACTIVE authorization)
      ↓
Kiosk unlocks (receives WebSocket unlock event)
      ↓
Voter completes position-by-position ballot
      ↓
Complete ballot submitted over HTTP
      ↓
Atomic commit: Vote rows created, has_voted = True, Authorization = USED, Kiosk locked
      ↓
Turnout updated in real time on Live Dashboard
      ↓
Kiosk returns to locked waiting state
      ↓
Admin ends election (ACTIVE → CLOSED)
      ↓
Further authorization and ballot submission rejected
      ↓
Results calculated and published (winner highlight, paper tally printing)
      ↓
Return to Home
```

---

# 12. Rule for Choosing the Next Task

Before implementing any task:

1. Identify the current vertical slice in the locked sequence (Slices 1–17).
2. Trace the workflow through the specifications, data model, state machines, and invariants.
3. Inspect existing code in the repository.
4. Implement the smallest correct change that completes the vertical workflow end-to-end.
5. Write and run tests for the workflow and its failure cases.
6. Verify relevant invariants and security boundaries.
7. Record completion and proceed to the next slice only when the Slice Completion Gate is satisfied.
