# Electra — Product & Requirements

**Status:** LOCKED  
**Document Type:** Product / Requirements Contract  
**Purpose:** Define what Electra must and must not do.

> This document is a focused product contract. Detailed architecture, data-model, state-flow, coding, implementation-order, and testing rules belong in the corresponding numbered documents. `electra locked in.txt` remains the highest-authority source of truth.

---

## 1. Product Definition

Electra is a self-hosted web application deployed over a Local-Area Network (LAN) for school and campus elections.

One Electra installation represents one independent deployment for one institution or operator. Each installation consists of:

- one Electra Django web application;
- one dedicated PostgreSQL database;
- one human Administrator;
- one installation-wide central voter registry;
- multiple elections belonging to the installation;
- multiple polling booths;
- technical Officer Station accounts;
- technical Voting Kiosk accounts.

Separate installations on separate machines are completely independent and do not share users, databases, or state. A user accessing an existing installation connects to that server's LAN IP address through a standard web browser.

It digitally recreates a controlled physical polling-booth workflow:

1. An officer verifies a voter.
2. The system verifies eligibility and booth allocation.
3. The officer authorizes the voter.
4. The paired kiosk unlocks.
5. The voter completes one ballot.
6. The server atomically records the ballot.
7. The kiosk locks again.
8. Turnout updates in real time.
9. Results remain hidden until the election is closed.

The backend is authoritative for authentication, authorization, election state, voter eligibility, vote validation, and voting state.

Electra is a **controlled polling-booth system deployed over a LAN**, not a generic public online voting platform or multi-tenant SaaS service.

---

## 2. Actors and Interfaces

### 2.1 Administrator

The Administrator is the single human administrative user for the Electra installation.

The Administrator:

- is created during the first-run installation initialization flow;
- is represented by a standard database-backed Django `User` model with `role = ADMIN`;
- is never identified by a hardcoded username, fixed user ID, or configuration constant;
- operates from a centered, authenticated **Home** landing hub with direct access to:
  - **Start Election** (initiating the four-stage setup sequence);
  - **Master Voter Registry** (persistent institutional voter archive);
  - **Election History** (past completed elections and archived results);
- configures new elections through exactly four setup stages:
  1. **Election Voters** (selecting from master registry or direct bulk import);
  2. **Election Details & Candidates** (single coherent workspace);
  3. **Booths & Allocation** (booths, paired devices, credentials, voter allocation, lightweight printing);
  4. **Review & Start** (readiness validation, Save as Draft vs Start Election);
- monitors active polling via the dedicated **Live Election Dashboard**;
- ends the election to review and publish candidate tallies on the **Results** page.

The system enforces:
- exactly one human Administrator per installation (multi-admin and public registration are out of scope);
- exactly **ONE active/running election** at a time.

### 2.2 Officer Station

An Officer Station is a technical identity bound to one booth.

It is used to:

- authenticate;
- search voters;
- verify voter identity and eligibility;
- verify booth allocation;
- authorize a voter;
- monitor the paired kiosk;
- receive operational voting status.

The Officer Station must never receive the voter's candidate selections.

### 2.3 Voting Kiosk

A Voting Kiosk is a technical identity bound to one booth.

It is used to:

- receive authorization;
- present the ballot;
- accept ballot selections;
- submit the completed ballot;
- receive confirmation.

The kiosk is locked by default and returns to the locked state after a successful ballot.

### 2.4 Voter

The voter is not a separate authenticated application account.

The voter interacts with the Voting Kiosk only after authorization by the Officer Station.

---

## 3. Functional Requirements

### FR-01 — Authentication and Access Control

The system must provide authenticated identities for:

- Administrator (single human administrative user);
- Officer Station (technical device identity);
- Voting Kiosk (technical device identity).

Authentication rules:

- On first launch, the system must detect whether the installation has been initialized;
- If uninitialized, a first-run setup flow prompts for the creation of the installation's single Administrator account;
- The Administrator account must be stored as a normal Django `User` record with `role = ADMIN`;
- The Administrator must not be hardcoded to a fixed username, fixed database primary key, or configuration constant;
- There is no public user registration, SaaS account management, institutional credential verification, or third-party identity provider;
- The system enforces strict role boundaries using Django session authentication;
- Passwords and device credentials must be securely hashed;
- Only one active session is permitted for a given Officer Station or Voting Kiosk identity;
- Device credentials must be rotatable/revocable without changing the logical device identity.

---

### FR-02 — Election Management

Elections belong to the Electra installation as a whole. There is no per-user ownership of elections and the data model must not include an `Election.owner` field.

The system enforces that **only one election may be active/running at any given time**.

The administrator configures elections through a four-stage setup flow:
1. **Stage 1 — Election Voters** (`references/06.1_voter_list.png`): Select voters from the master registry or directly bulk import election voters;
2. **Stage 2 — Election Details & Candidates** (`references/06.2_election_details.png`): Single workspace configuring election name, optional description, voting hours, positions, and candidate roster (selected from election voters, with editable symbols and modal dialog support);
3. **Stage 3 — Booths & Allocation** (`references/06.3_booth&allocation.png`): Single workspace configuring physical booths, paired Officer and Kiosk devices, masked credentials with reveal/copy, pass rotation, balanced or manual voter allocation, and lightweight print actions;
4. **Stage 4 — Review & Start** (`references/06.4_review.png`): Comprehensive configuration and device readiness summary via borderless accordion cards, with the **Start election** launch button positioned in the top navigation bar alongside **&larr; Back**, triggering the cutoff confirmation modal.

Progress and draft state:
- The setup flow stores `Election.setup_stage` (1..4) to record the current step and restore progress;
- The administrator can choose **Save as Draft** (`Election.is_saved_draft = true`) at any point to exit to the Home dashboard and resume later.

Once started:
- The election transitions from `DRAFT → ACTIVE`;
- Active polling is monitored via the dedicated **Live Election Dashboard**;
- Voting configuration is permanently frozen;
- During active polling, normal configuration (booths, allocations, positions, candidates, eligibility rules) must not change. Credential rotation/revocation is the deliberate operational exception.

After polling, the administrator ends the election:
- Transitions from `ACTIVE → CLOSED`;
- Candidate tallies, percentages, and winners are published on the **Results** page;
- Device credentials from the closed election are automatically purged from the database;
- Navigation returns to Home.

---

### FR-03 — Master Voter Registry vs. Election Voters

The system maintains one persistent central voter registry per Electra installation.

"Central" means central to the specific installation; the registry is not shared across independent installations, owned by individual users, or partitioned by tenants.

The **Master Voter Registry** is a separate, persistent administrative area:
- It is NOT the election voter list;
- Administrators can view grouped voters, add, edit, remove, and import voters;
- Administrators can assign/change academic groups and search/filter;
- Configurable primary registry identity (e.g. Student ID, University ID, Admission Number) is unique within the central registry and used to prevent duplicate records.

**Election Voters (`ElectionVoter`)**:
- Voters participating in a specific election are configured in **Stage 1 (Election Voters)**;
- Obtained either by selecting voters from the master registry or by direct bulk import into the election;
- Dense/grouped display, search/filtering, and bulk selection support adding/removing election voters.

Initial structured import support must include:

- CSV;
- Excel.

PDF import is not part of the core implementation.

The primary registry identity must be configurable rather than hardcoded to a particular institutional identifier.

Examples include:

- Student ID;
- University ID;
- Admission Number.

The configured identity is used to match existing voters and prevent duplicate registry records.

---

### FR-04 — Academic Structure

The voter registry must support institution-specific academic structures.

The system must not hardcode assumptions such as:

- a particular program always being a department;
- semesters always having one fixed structure;
- class/section relationships being universal.

Academic groups must support configurable hierarchies such as:

```text
Department
└── Semester
```

or:

```text
Class
└── Section
```

The administrator configures the relevant structure during import/configuration.

---

### FR-05 — Booth Management

Configured within **Stage 3 (Booths & Allocation)** on a single setup page.

Administrators must be able to:

- create, edit, and delete polling booths before polling starts;
- view booths represented as clean, borderless/transparent sections/cards rather than generic admin tables;
- assign and pair an Officer Station and a Voting Kiosk to each booth;
- inspect device status and masked credentials (`••••••••`) with one-click reveal/copy for operator distribution (`Device.cleartext_password`);
- rotate device passwords/passes on demand, immediately revoking active sessions.

Every booth must have exactly:

```text
1 Officer Station
1 Voting Kiosk
```

A physical computer may be replaced without changing the logical booth/device identity by logging the replacement computer into the same device identity.

Device login is strictly permitted only if the election is in `ACTIVE` or `DRAFT` status; devices are never in an "unassigned" state.

Booth configuration is frozen once polling becomes active.

---

### FR-06 — Voter Allocation & Printing

Configured within **Stage 3 (Booths & Allocation)** alongside booths.

For every eligible voter participating in an election:

```text
ElectionVoter → exactly one Booth
```

Allocation rules:
- All election voters must be allocated before polling begins;
- Supports automatic balanced allocation as well as manual adjustment;
- Displays allocation counts and group breakdowns per booth;
- An Officer Station may authorize only voters allocated to its own bound booth;
- A voter allocated to one booth must never be authorizable through another booth;
- Allocation configuration is frozen once the election becomes active.

Printing rules:
- Printing is intentionally lightweight and non-intrusive (no bulky print materials panel);
- Discrete print action buttons are provided directly in Stage 3:
  - `Print voter list`
  - `Print booth slips`: renders dedicated printable voter slips (`templates/elections/voter_slips_print.html`) displaying voter name, ID, roll number, booth number, and a physical signature line for station verification.

---

### FR-07 — Voter Verification

Before authorization, the Officer Station must verify:

- voter exists;
- voter is registered for the election;
- voter is eligible;
- voter is allocated to the officer's booth;
- voter has not already voted;
- paired kiosk is connected;
- paired kiosk is ready;
- paired kiosk is in fullscreen mode.

If any required condition fails, authorization must be rejected.

---

### FR-08 — Single-Use Authorization

Authorization is the bridge between voter verification and kiosk voting.

An authorization must:

- belong to one election voter;
- belong to one booth;
- target the paired kiosk;
- be single-use;
- permit exactly one accepted ballot;
- become used after successful ballot commitment;
- be cancellable by the officer.

An active authorization must not permit a second ballot.

There is no automatic authorization-expiry requirement.

---

### FR-09 — Kiosk Lock/Unlock

The Voting Kiosk must be locked by default.

The kiosk may unlock only after valid server-side authorization.

The authorization must originate from the Officer Station bound to the same booth.

After a successful ballot:

```text
vote committed
    ↓
confirmation
    ↓
kiosk locks
    ↓
kiosk resets for next voter
```

The browser Fullscreen API is a readiness/security signal only.

OS-level device lockdown is outside the project scope.

---

### FR-10 — Ballot Casting

The kiosk must support both single-position and multi-position elections.

A ballot may contain multiple position selections.

The voter sees only positions for which they are eligible.

The kiosk may present the ballot across multiple UI pages.

Multiple UI pages represent one logical ballot.

The complete ballot is submitted once.

The backend must validate the entire ballot before committing it.

---

### FR-11 — Server-Authoritative Vote Validation

Every ballot submission must be validated server-side.

Validation must include, where applicable:

- election is active;
- election deadline has not passed;
- authenticated kiosk is valid;
- kiosk matches the authorization;
- authorization is active;
- voter is registered for the election;
- voter is allocated to the kiosk's booth;
- voter has not already voted;
- each submitted position belongs to the election;
- each candidate belongs to its position;
- voter is eligible for each selected position;
- exactly one candidate is selected for each required position.

UI checks are never sufficient for vote security.

---

### FR-12 — Atomic Vote Recording

The ballot must be recorded atomically.

A successful multi-position ballot must result in:

- all required Vote records being created;
- voter election status being updated;
- authorization being marked USED;
- kiosk state being reset.

If any part fails, the transaction must roll back.

No partial ballot may remain recorded.

The implementation must use database transactions and appropriate row locking to prevent race conditions.

---

### FR-13 — Ballot Secrecy

Persistent Vote records must not contain a direct voter identity link.

The system must not persist a relationship equivalent to:

```text
voter → candidate
```

or:

```text
ElectionVoter → Vote
```

The architecture deliberately separates voter participation state from ballot content.

The Officer Station must never receive candidate selections.

Operational logs must also avoid creating voter-to-candidate linkage.

---

### FR-14 — Duplicate Vote Prevention

A voter may cast at most one accepted ballot per election.

Duplicate-vote prevention must exist at the backend/database level.

It must not depend only on:

- disabled buttons;
- frontend state;
- browser state;
- JavaScript checks.

Concurrent requests must also be handled safely.

---

### FR-15 — Real-Time Communication

WebSockets are used for real-time operational state.

They must support events such as:

- kiosk connected;
- kiosk disconnected;
- kiosk ready;
- kiosk not ready;
- fullscreen changes;
- lock/unlock;
- authorization notifications;
- ballot-recorded notifications;
- turnout updates;
- election lifecycle events;
- state resynchronization.

Actual ballot submission uses HTTP.

The vote itself must never be submitted over WebSocket.

#### Liveness Rule

Do **not** implement an application-level heartbeat mechanism.

Kiosk liveness is determined using the persistent WebSocket connection and its connection/disconnection lifecycle.

The backend remains authoritative.

---

### FR-16 — Reconnection and Resynchronization

A kiosk must never assume that its previous browser state is authoritative after reconnection.

On WebSocket reconnection, the server must determine the current authoritative state.

The kiosk must resynchronize:

- election state;
- kiosk state;
- authorization state;
- voting state.

Authorization states are:

```text
ACTIVE
USED
CANCELLED
```

For example:

```text
Browser reconnects
    ↓
Server checks authoritative state
    ↓
ACTIVE    → recover active voting state
USED      → show successful completion and remain locked
CANCELLED → remain/reset to locked state
```

Stale local browser state must never override server state.

---

### FR-17 — Election Closure

An administrator may close an active election.

Closure must cause:

- officer stations to be notified;
- kiosks to be notified;
- new authorizations to be rejected;
- new ballot submissions to be rejected.

The server must enforce the closed state even if a client attempts to bypass the UI.

Election closure and vote submission must be concurrency-safe.

A vote cannot be accepted after the transactionally established election-closure boundary.

---

### FR-18 — Turnout

The administrator must receive live turnout information.

Turnout must update after a successfully committed ballot.

The dashboard may display:

- total eligible voters;
- total voters who voted;
- turnout percentage;
- per-booth turnout/activity.

Candidate-wise results must not be exposed while polling is active.

---

### FR-19 — Results

Candidate-wise results may be calculated and published only after the election is closed.

Results are organized by:

```text
Position
└── Candidate
    └── Vote count
```

Candidate-wise results must not be visible during active polling.

---

### FR-20 — Election Timer

The election stores:

```text
starts_at
ends_at
```

The server must enforce the election deadline.

When the deadline has passed:

- new authorization must be rejected;
- ballot submission must be rejected.

Automatic scheduler infrastructure is not required merely to enforce expiry.

The server may evaluate expiry during election-related operations and connected-client lifecycle events.

---

## 4. Security and Integrity Requirements

The backend is the final authority.

Security-sensitive decisions must be enforced server-side.

Required controls include:

- role-based access control;
- Django session authentication;
- secure password hashing;
- CSRF protection;
- one active session per technical device identity;
- server-side authorization checks;
- server-side ballot validation;
- backend/database duplicate-vote protection;
- transactional vote recording;
- row locking for critical concurrent operations;
- booth-bound device identities;
- credential revocation and rotation.

No client may determine whether a vote is valid.

---

## 5. Communication Boundary

### HTTP

Use HTTP for:

- authentication;
- logout;
- CRUD;
- voter lookup;
- election configuration;
- candidate management;
- booth management;
- voter import;
- authorization creation;
- authorization cancellation;
- ballot retrieval;
- ballot submission;
- result retrieval;
- credential operations.

### WebSocket

Use WebSocket for:

- connection state;
- kiosk readiness;
- fullscreen state;
- lock/unlock notifications;
- authorization notifications;
- election lifecycle events;
- turnout updates;
- ballot-recorded notifications;
- reconnection/resynchronization.

**Never submit the actual vote through WebSocket.**

Detailed transport and consumer architecture belongs in `03-architecture.md`.

---

## 6. Required User Experience

### 6.1 Administrator Workflow

The administrator experiences a centered, onboarding-style sequence with editorial typography, generous whitespace, and dense data displays where appropriate. Generic sidebar layouts and card-within-card containers are strictly prohibited.

The canonical flow is:

```text
HOME
  ↓
START ELECTION
  ↓
1. ELECTION VOTERS
  ↓
2. ELECTION DETAILS & CANDIDATES
  ↓
3. BOOTHS & ALLOCATION
  ↓
4. REVIEW & START
  ↓
LIVE ELECTION DASHBOARD
  ↓
END ELECTION
  ↓
RESULTS
  ↓
HOME
```

Key characteristics:
- **Home**: Authenticated, centered landing hub with direct access to Start Election, Master Voter Registry, and Election History.
- **Master Voter Registry**: Persistent administrative area separate from election setup.
- **Election History**: Separate administrative archive of past elections and published results.
- **Stage 1 — Election Voters**: Dense, grouped voter selection from the master registry or direct bulk import into the election.
- **Stage 2 — Election Details & Candidates**: Single coherent workspace configuring election meta, positions, and candidate roster with modal interactions and editable symbols.
- **Stage 3 — Booths & Allocation**: Single setup page with borderless booth cards, device pairings, masked credentials, pass rotation, auto/manual allocation, and lightweight print actions (`Print voter list`, `Print booth slips`).
- **Stage 4 — Review & Start**: Comprehensive configuration and runtime readiness summary with clear `Valid / Ready`, `Incomplete / Invalid`, and `Device Not Ready` distinctions; actions for `Save as Draft` and `Start Election`.
- **Live Election Dashboard**: Dedicated operational monitoring screen during active polling (not part of the wizard); real-time turnout, booth metrics, device statuses, pass management, and `End Election` action.
- **Results**: Post-election candidate vote tallies, percentage breakdowns, visual highlight on winners, `Print Results` action, and return to Home.
- **Active Election Constraint**: Exactly ONE election can be active at a time.

### 6.2 Officer Station

The expected workflow is:

```text
Login
  ↓
Bound booth dashboard
  ↓
Search voter
  ↓
Verify identity
  ↓
Verify eligibility/allocation
  ↓
Check kiosk readiness
  ↓
Authorize
  ↓
Wait for voting result
```

The officer receives operational status but never ballot contents.

### 6.3 Voting Kiosk

The expected lifecycle is:

```text
LOCKED
  ↓
AUTHORIZED / UNLOCKED
  ↓
VOTING
  ↓
SUBMITTING
  ↓
SUCCESS
  ↓
LOCKED
```

On failure:

```text
SUBMITTING
  ↓
ERROR / CONNECTION
```

The kiosk must never display a false success message.

Confirmation is shown only after the server confirms the database commit.

Detailed visual and interaction rules belong in `07_UI_DESIGN.md`.

---

## 7. Non-Goals

The following are outside the core product scope.

### NG-01 — Public Internet Voting and Cloud Hosting

Electra is not a public, open-internet voting platform or a cloud-hosted SaaS application.

It is a browser-accessed web application deployed on a Local-Area Network (LAN) (e.g., `http://192.168.x.x:8000`). Internet deployment, cloud infrastructure, domain management, and public access are out of scope.

### NG-02 — Offline Voting

Voting requires an active network connection.

The system must not implement offline vote acceptance or local vote queues.

### NG-03 — OS-Level Kiosk Lockdown

Electra provides browser-level kiosk locking/fullscreen behavior.

It does not control:

- operating-system restrictions;
- desktop/window managers;
- device administration;
- hardware-level lockdown;
- BIOS/device configuration.

### NG-04 — Application-Level Heartbeat

Do not implement a separate application heartbeat/ping mechanism for kiosk liveness.

WebSocket connection lifecycle is the liveness mechanism.

### NG-05 — Generic Online Voting Platform

Do not turn Electra into a generic self-service online voting system.

The Officer Station → Authorization → Kiosk workflow is fundamental to the product.

### NG-06 — Candidate Automation

Candidate generation, recommendation, AI candidate creation, or automated candidate management is not required.

Candidates are administrator-managed.

### NG-07 — Arbitrary Boolean Eligibility Engine

Do not build a general-purpose expression/rule engine.

Eligibility uses:

```text
multiple values within one rule = OR
different rules = AND
```

### NG-08 — Arbitrary Document OCR

Arbitrary scanned-document OCR is not part of the core implementation.

Structured PDF import, if implemented later, is limited to structured/tabular data.

### NG-09 — Microservices

Electra is not a microservice architecture.

The backend remains a Django application with domain-specific Django apps.

### NG-10 — React or SPA Frontend

React is not part of the locked implementation.

The planned frontend stack uses:

- HTML;
- CSS;
- HTMX;
- JavaScript.

### NG-11 — JWT Authentication

JWT-based authentication is not part of the locked implementation.

Use Django session authentication.

### NG-12 — Redis/Celery/Kubernetes Infrastructure

The core implementation does not require:

- Redis;
- Celery;
- Kubernetes;
- Docker-based deployment;
- external scheduler infrastructure.

Do not introduce these technologies unless a later explicitly locked requirement changes the scope.

### NG-13 — Multi-Tenancy and SaaS Platform

Electra is not a SaaS multi-tenant platform.

The system must not implement:

- tenant identifiers or tenant models;
- per-user data ownership (`Election.owner`, `VoterRegistry.owner`);
- cross-tenant data isolation logic;
- multiple independent administrator datasets within one installation;
- public user registration or self-service signup;
- institutional authorization/verification of the administrator.

---

## 8. Product Scope Rules

### Rule 1 — Do not invent requirements

If a feature is not specified in the locked specification set, do not add it as product functionality.

### Rule 2 — Prefer the simplest implementation

Do not introduce infrastructure merely because it is commonly used in similar systems.

### Rule 3 — Server authority is mandatory

Election and security decisions must remain enforceable by the backend.

### Rule 4 — Preserve ballot secrecy

Do not introduce a data relationship, log entry, event payload, API response, or UI element that unnecessarily links voter identity to candidate selection.

### Rule 5 — Preserve booth isolation

An Officer Station and Kiosk are bound to their logical booth.

Authorization must never cross booth boundaries.

### Rule 6 — Preserve atomicity

A ballot is one logical operation.

Partial multi-position ballots must never be committed.

### Rule 7 — Preserve frozen election configuration

Once polling is active, normal election configuration changes are prohibited.

### Rule 8 — Do not reintroduce removed concepts

In particular, do not reintroduce application-level heartbeat/liveness polling unless the locked requirements are explicitly changed.

### Rule 9 — Do not expand the product during implementation

If implementation reveals a missing requirement, document the requirement gap rather than silently expanding scope.

### Rule 10 — Requirements changes must be explicit

A change to this contract must be reflected in the relevant implementation, architecture, data, state, UI, and testing documents.

---

## 9. Minimum Product Completion

Electra's core product is complete only when this end-to-end workflow works:

```text
Administrator logs in (Home)
    ↓
Start Election
    ↓
Stage 1: Configure Election Voters (from Master Registry or direct bulk import)
    ↓
Stage 2: Configure Election Details & Candidates (single workspace, modal/dialog support)
    ↓
Stage 3: Configure Booths & Allocation (device pairings, credentials, allocation, lightweight printing)
    ↓
Stage 4: Review & Start (validation checks, runtime device readiness)
    ↓
Start Election (transition DRAFT → ACTIVE, configuration frozen)
    ↓
Live Election Dashboard (real-time monitoring)
    ↓
Officer logs in
    ↓
Kiosk connects and becomes ready
    ↓
Officer verifies voter
    ↓
Officer authorizes voter
    ↓
Paired kiosk unlocks
    ↓
Voter completes ballot
    ↓
Backend validates complete ballot
    ↓
Atomic database commit
    ↓
Authorization becomes USED
    ↓
Kiosk locks
    ↓
Turnout updates in real time on Live Dashboard
    ↓
Administrator ends election (ACTIVE → CLOSED)
    ↓
Further voting is rejected
    ↓
Administrator inspects and publishes Results
    ↓
Return to Home
```

Every security-sensitive step in this flow must be enforced by the backend.

---

## 10. Requirements Lock

This document is the product-level boundary for Electra.

The implementation must treat:

- required features as mandatory;
- non-goals as prohibited scope;
- security and integrity rules as invariants;
- server authority as non-negotiable;
- ballot secrecy as a design requirement;
- booth isolation as a design requirement;
- atomic voting as a design requirement.

Implementation details may change when necessary, but externally observable requirements and core invariants must not be weakened without an explicit requirements change.

For detailed implementation contracts, use the corresponding numbered documents rather than duplicating their contents here.
