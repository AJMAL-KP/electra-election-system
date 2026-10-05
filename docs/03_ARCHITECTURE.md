# Electra — Architecture

**Status:** LOCKED  
**Document Type:** Architecture Contract  
**Purpose:** Define where Electra logic lives, how components communicate, and which architectural boundaries must not be crossed.

> This document defines **structure and responsibility**. Product behavior belongs in `01-requirements.md`; build order in `02-implementation-plan.md`; exact persistence rules in `04-data-model-invariants.md`; state transitions in `05-state-flow.md`; UI rules in `06-ui-ux-theme.md`; coding rules in `07-coding-standards.md`; verification in `08-testing-definition-of-done.md`. `electra-locked-in.txt` remains the highest-authority source of truth.

---

## 1. System Architecture

Electra is a self-hosted web application deployed over a Local-Area Network (LAN) for controlled school and campus elections.

One Electra installation represents one independent deployment and isolation boundary for one institution or operator:

```text
Electra Installation
  ├── Dedicated PostgreSQL database
  ├── Single human Administrator
  ├── Central Voter Registry
  ├── Elections (belonging to the installation)
  ├── Polling Booths
  ├── Officer Stations
  └── Voting Kiosks
```

Separate installations on separate machines are completely independent and do not share databases, users, or election state.

### LAN Deployment Topology

Clients access the Electra server through standard web browsers over the local network (e.g., `http://192.168.x.x:8000`):

```text
LAN Browser Clients (Admin / Officer Stations / Kiosks)
                         │
              ┌──────────┴──────────┐
              │                     │
             HTTP                WebSocket
              │                     │
              ▼                     ▼
       Django URL / View      Django Channels
              │                  Consumers
              │                     │
              └──────────┬──────────┘
                         ▼
                  Domain Services
                         │
              ┌──────────┴──────────┐
              │                     │
          Selectors              Models
              │                     │
              └──────────┬──────────┘
                         ▼
              Dedicated PostgreSQL DB
```

The browser is never the authority for election, authorization, eligibility, vote validity, or security-sensitive state.

### Deployment-Aware Architecture Requirements

1. **No hardcoded server address**  
   The application shall not hardcode a server IP address, hostname, or port in application code.

2. **Dynamic WebSocket endpoint**  
   The WebSocket endpoint shall be derived from the current application origin so that the same frontend works across different deployment addresses and supports both `ws://` and `wss://` as appropriate.

3. **Environment-based deployment configuration**  
   Deployment-specific settings shall be supplied through environment variables or equivalent external configuration rather than application source code.

4. **No hardcoded credentials or secrets**  
   Database credentials, secret keys, passwords, tokens, and other sensitive deployment values shall not be stored directly in application source code.

5. **First-run Administrator setup**  
   A fresh Electra installation shall provide a first-run setup process through which the initial Administrator account is created. The Administrator identity shall not be hardcoded.

6. **Persistent database authority**  
   PostgreSQL shall be the authoritative source of persistent application and election state. Critical election state shall not depend solely on application-process memory.

7. **WebSocket recovery and resynchronization**  
   WebSocket-dependent runtime state shall be recoverable after client reconnection or server restart. Clients shall resynchronize against authoritative server state rather than relying solely on previously held client state.

8. **Deployment-independent business logic**  
   Domain and business logic shall not depend on the physical deployment topology, server address, network layout, or specific web server.

9. **No institution-specific source-code assumptions**  
   Institution-specific values and election data shall be represented through configuration or database data rather than hardcoded source-code constants.

10. **ASGI-compatible application architecture**  
    The application shall be implemented as an ASGI-compatible Django application and shall not depend on Django's development `runserver` command for its application architecture.

---

## 2. Locked Technology Stack

Use:

- Python;
- Django;
- Django Channels;
- Daphne / ASGI;
- PostgreSQL;
- Django ORM;
- Django session authentication;
- Django CSRF protection;
- database transactions;
- row locking with `select_for_update()` where required.

The core implementation does not introduce:

- React;
- JWT authentication;
- Redis;
- Celery;
- Docker/Kubernetes;
- microservices;
- offline voting;
- application-level heartbeat infrastructure.

---

## 3. Django Project Structure

Use four domain applications:

```text
electra/
├── accounts/
├── elections/
├── voters/
└── voting/
```

The Django project package contains project-level configuration only:

```text
electra/
    settings.py
    urls.py
    asgi.py
    routing.py
    wsgi.py
```

Do not turn the project package into a miscellaneous business-logic application.

---

# 4. Domain Boundaries

## 4.1 accounts

Owns identity and access.

Responsibilities:

- installation initialization check (detecting first-run state);
- first-run Administrator setup view and account creation;
- custom `User` model with `role = ADMIN`, `OFFICER`, and `KIOSK`;
- single human Administrator enforcement (one Administrator per installation);
- dynamic Administrator identity (never identified by a hardcoded username, fixed ID, or configuration constant);
- `Device`;
- device credentials and password hashing;
- credential rotation/revocation;
- `DeviceSession`;
- Django session authentication;
- role and permission enforcement;
- session enforcement (one active session per Officer/Kiosk device).

There is no public user registration, SaaS account management, or third-party identity verification.

The `accounts` app does not own election configuration, voter registry records, or vote recording.

---

## 4.2 elections

Owns election configuration and lifecycle.

Elections belong to the Electra installation as a whole. There is no per-user ownership and the data model must not include an `Election.owner` field.

Responsibilities:

- `Election`;
- `Position`;
- `Candidate`;
- eligibility configuration;
- election configuration validation;
- start/close operations;
- election lifecycle;
- election configuration UI.

It does not own voter registry records or the actual polling workflow.

---

## 4.3 voters

Owns the central voter registry and election allocation.

The registry is central to the installation. It is not shared across independent installations, owned by individual users, or partitioned by tenants.

Responsibilities:

- `Voter`;
- `AcademicGroup`;
- `ElectionVoter`;
- CSV/Excel import;
- voter filtering/search;
- voter enrollment;
- booth allocation;
- pre-election reallocation.

It does not own ballot submission or Vote records.

---

## 4.4 voting

Owns the polling workflow.

Responsibilities:

- `VoterAuthorization`;
- `Vote`;
- authorization operations;
- ballot generation;
- ballot validation;
- atomic vote recording;
- kiosk runtime state;
- Officer workflow;
- Kiosk workflow;
- WebSocket consumers;
- turnout;
- result calculation/publication;
- reconnection/resynchronization.

---

# 5. Layer Responsibilities

## 5.1 Views

Django views handle HTTP concerns.

They may:

- authenticate/request context;
- parse and validate request shape;
- call domain services;
- return templates or HTTP responses;
- translate service outcomes into UI responses.

They must not contain substantial election/voting business logic.

Do not put transaction-heavy voting logic directly in a view.

---

## 5.2 WebSocket Consumers

Channels consumers handle WebSocket communication.

They may:

- authenticate the connection;
- identify the technical device;
- join appropriate groups;
- receive operational client events;
- call domain services where required;
- serialize/send server events;
- close/reject unauthorized connections.

They must not become a second business-logic layer.

Do not implement voting transactions directly inside consumers.

Actual ballot submission is HTTP.

---

## 5.3 Services

Services own domain operations and transaction boundaries.

Examples:

```text
accounts
    generate_device_credentials()
    rotate_device_credentials()
    revoke_device_credentials()
    create_device_session()

elections
    validate_election_configuration()
    start_election()
    close_election()
    close_if_expired()

voters
    enroll_voters()
    allocate_voter()
    reallocate_voters()
    import_csv()
    import_excel()

voting
    create_authorization()
    cancel_authorization()
    build_ballot()
    submit_ballot()
    calculate_turnout()
    publish_results()
```

Services are the primary location for business rules that span multiple models or require transactions.

---

## 5.4 Models

Models own persistence concerns.

They may contain:

- fields;
- relationships;
- database constraints;
- simple model-level validation;
- simple model behavior that naturally belongs to the entity.

Models should not become a substitute for domain services.

Do not put the complete election workflow or ballot transaction into model methods merely to avoid creating services.

---

## 5.5 Selectors / Query Layer

Complex read/query logic may be isolated in selectors or an equivalent query layer.

Use this boundary for reusable reads such as:

- voter search;
- election dashboard queries;
- turnout queries;
- result queries;
- kiosk synchronization state.

Keep write-side business operations in services.

---

## 5.6 Templates

Templates are presentation.

They should:

- render server-provided state;
- display forms and messages;
- present the correct interface for the current role/state.

Templates must not decide whether a voter is eligible or whether a vote is valid.

---

## 5.7 Browser JavaScript

JavaScript handles browser interaction.

It may manage:

- UI interaction;
- temporary ballot selections;
- Fullscreen API;
- WebSocket connection handling;
- client-side presentation;
- loading/error states.

It must not be treated as authoritative for:

- election state;
- booth identity;
- voter eligibility;
- authorization validity;
- vote validity;
- successful vote commitment.

---

# 6. HTTP / WebSocket Boundary

Keep the transport boundary strict.

## HTTP

Use HTTP for transactional/request-response operations:

- login/logout;
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
- credential rotation/revocation.

## WebSocket

Use WebSocket for real-time operational state:

- connection/disconnection;
- kiosk readiness;
- fullscreen changes;
- lock/unlock notifications;
- authorization notifications;
- election lifecycle events;
- ballot-recorded notifications;
- turnout updates;
- reconnection/resynchronization.

### Critical rule

**The actual vote is never submitted over WebSocket.**

WebSocket messages are transport notifications/state synchronization, not the authority for committing votes.

---

# 7. Browser Interfaces

Electra has three main browser interfaces accessed over the Local-Area Network.

## Administrator

The browser interface for the single human Administrator of the Electra installation.

Used for:

- first-run installation setup;
- election configuration;
- voter registry;
- voter enrollment/allocation;
- booth configuration;
- credential management;
- runtime monitoring;
- turnout;
- post-election results.

## Officer Station

A technical identity bound to one booth.

Used for:

- voter lookup;
- voter verification;
- eligibility/allocation verification;
- authorization;
- operational kiosk monitoring.

The Officer interface must never receive candidate selections.

## Voting Kiosk

A technical identity bound to one booth.

Used for:

- receiving authorization;
- presenting the ballot;
- collecting temporary selections;
- submitting the complete ballot;
- displaying server-confirmed result;
- locking/resetting.

The Kiosk must not receive unnecessary voter identity information.

---

# 8. Device and Booth Isolation

Device identity determines booth identity.

The server must derive:

```text
Authenticated Device
        ↓
Device.booth
        ↓
Authorized booth context
```

Do not trust a client-supplied booth identifier.

An Officer Station can authorize only voters allocated to its server-derived booth.

A Kiosk can receive authorization only for its server-derived booth.

Cross-booth authorization must be rejected server-side.

### Device Credential Distribution & Visibility

To support local booth operator setup across the dedicated Local-Area Network:
- Each generated device stores its provisioned credentials, including `Device.cleartext_password`.
- On the Booth Management setup screen, passwords are masked by default (`••••••••`) and can be revealed, copied, or printed as slips by the Administrator for physical station operators.
- Password rotation generates fresh credentials, updates `cleartext_password`, and immediately revokes active device sessions.

### Active / Draft Login Rule & Past Credential Purge

- **Exclusive Active/Draft Authentication**: Technical devices (`OFFICER` and `KIOSK`) can authenticate only if their associated election is currently in `ACTIVE` or `DRAFT` status.
- **Automatic Cleanup**: When an election closes or when credentials rotate, device credentials belonging to inactive/closed elections are automatically purged (`Device`, `DeviceSession`, and underlying `User` records deleted), preventing stale or unauthorized access on the LAN.
- **No Unassigned State**: Devices are strictly bound 1:1 to their booth at creation time and are never "unassigned". Any invalid, terminated, or rotated device session routes canonically to `session_revoked.html`.

---

# 9. WebSocket Grouping

Use narrowly scoped Channels groups.

Suggested groups:

```text
booth:<booth_id>
election:<election_id>
admin:<election_id>
```

Group membership must be derived from authenticated server-side identity and authorization.

Do not allow a client to subscribe to arbitrary booth/election groups merely by supplying an identifier.

Sensitive events must be scoped to the minimum required audience.

---

# 10. Sensitive Data Boundaries

## Officer Station must not receive

- candidate selections;
- ballot contents;
- voter-to-candidate linkage;
- unnecessary Vote data.

Officer notifications should remain operational, for example:

```text
authorization.created
ballot.recorded
authorization.cancelled
kiosk.not_ready
```

## Kiosk must not receive

- unnecessary voter registry information;
- other booth information;
- other voters' information;
- candidate-wise election results during active polling.

The kiosk receives only the ballot/state information required for its current operation.

---

# 11. Vote Data Separation

The architecture deliberately separates participation state from ballot content.

Participation state is represented through election-specific records such as:

```text
ElectionVoter
VoterAuthorization
```

Persistent ballot content is represented by:

```text
Vote
    election
    candidate
    created_at
```

`Vote` must not contain:

```text
voter_id
election_voter_id
authorization_id
```

Do not introduce another indirect relationship that defeats the same separation.

Detailed data constraints belong in `04-data-model-invariants.md`.

---

# 12. Transaction Boundaries

Critical voting operations are transactional.

Use:

```python
transaction.atomic()
```

and row locking where required:

```python
select_for_update()
```

The important operations include:

- authorization creation;
- ballot submission;
- election closure;
- other operations that can race with voting.

Use a consistent lock order for critical voting paths.

The locked order is:

```text
Election
    ↓
ElectionVoter
    ↓
Authorization
    ↓
Kiosk/device where required
```

The exact locking requirements and invariants are defined in the data/invariant contract.

---

# 13. Authorization Architecture

Authorization is created through a domain service, not directly by a view or consumer.

Conceptually:

```text
Officer HTTP request
        ↓
Authorization service
        ↓
transaction.atomic()
        ↓
lock required rows
        ↓
validate election/time/voter/booth/kiosk
        ↓
create ACTIVE authorization
        ↓
COMMIT
        ↓
transaction.on_commit()
        ↓
WebSocket kiosk.unlock
```

The unlock notification must not be emitted as though authorization succeeded before the transaction commits.

---

# 14. Ballot Architecture

The kiosk submits the complete logical ballot over HTTP.

Conceptually:

```text
Kiosk
  ↓
HTTP submit
  ↓
Voting service
  ↓
transaction.atomic()
  ↓
lock required rows
  ↓
validate complete ballot
  ↓
create Vote records
  ↓
mark ElectionVoter.has_voted
  ↓
mark Authorization USED
  ↓
lock/reset kiosk state
  ↓
COMMIT
  ↓
HTTP success
  +
WebSocket notifications
```

A multi-page kiosk interface does not create multiple voting transactions.

Temporary browser selections remain temporary until final submission.

One logical ballot produces one atomic commit.

---

# 15. Post-Commit Events

Events describing successful database operations must be emitted only after the relevant transaction commits.

Use Django's:

```python
transaction.on_commit()
```

for post-commit notifications where appropriate.

Examples:

```text
authorization.created
kiosk.unlock
ballot.recorded
turnout.updated
election.closed
```

Do not broadcast a successful state before the database has committed it.

---

# 16. Election Lifecycle Architecture

Persistent election state:

```text
DRAFT
ACTIVE
CLOSED
RESULTS_PUBLISHED
```

Do not add a separate `READY` state.

Configuration readiness is determined by validation before activation.

The server enforces the election deadline.

A lightweight expiry operation such as:

```text
close_if_expired()
```

may be invoked during relevant requests/lifecycle events.

Do not introduce an external scheduler merely to change the election status.

---

# 17. Kiosk Runtime Architecture

Kiosk runtime state is separate from persistent election state.

The runtime states are:

```text
OFFLINE
NOT_READY
READY
UNLOCKED
VOTING
LOCKED
```

`READY` requires the server to know that:

- the kiosk WebSocket is connected;
- the kiosk has reported readiness;
- fullscreen/readiness conditions are satisfied.

Fullscreen is a browser readiness signal, not OS-level lockdown.

### Liveness

Do not implement an application-level heartbeat.

Use WebSocket connection/disconnection lifecycle for kiosk liveness.

---

# 18. Reconnection Architecture

The server is authoritative after reconnection.

Kiosk reconnect flow:

```text
WebSocket reconnect
        ↓
Authenticate Django session
        ↓
Identify Kiosk
        ↓
Derive Booth
        ↓
Read current Election state
        ↓
Read current Kiosk state
        ↓
Read current Authorization state
        ↓
Send authoritative state
        ↓
Browser updates itself
```

Never restore state solely from local browser memory.

This must correctly handle:

```text
ACTIVE authorization
USED authorization
CANCELLED authorization
```

and the case where an HTTP response was lost after a successful commit.

---

# 19. Configuration Freeze

Election configuration becomes immutable for normal administrative changes after activation.

This includes, as applicable:

- positions;
- candidates;
- eligibility configuration;
- booth structure;
- voter election configuration;
- booth allocation.

Credential rotation/revocation remains the deliberate operational exception.

The architecture must not provide normal mutation paths that bypass this rule.

---

# 20. Separation of Configuration and Runtime State

The system should distinguish configuration state from operational runtime state.

Example:

```text
Configuration
    Booth configured
    Officer assigned
    Kiosk assigned
    Voters allocated

Runtime
    Officer logged in
    Kiosk connected
    Fullscreen active
    Kiosk READY
```

This distinction is particularly important for the Administrator dashboard.

A correctly configured booth can still be operationally unavailable.

---

# 21. Architectural Anti-Patterns

Do not introduce the following:

### Business logic in views

Bad:

```text
view
 ├── lock rows
 ├── validate eligibility
 ├── create authorization
 └── decide kiosk state
```

Prefer:

```text
view
    ↓
service
    ↓
transaction/domain operation
```

### Business logic in consumers

Do not implement the complete vote transaction inside a WebSocket consumer.

### Browser authority

Do not accept:

```text
"the browser says this voter is eligible"
"the browser says the kiosk is ready"
"the browser says the vote succeeded"
```

as authoritative.

### Cross-app ownership

Do not duplicate domain entities across apps merely to avoid relationships.

For example, there should not be separate voter registries inside `elections` and `voting`.

### Transport as business state

A WebSocket message is not itself proof that a database operation succeeded.

Database state is authoritative.

### Multi-tenancy and SaaS abstractions

Do not introduce:

- tenant identifiers or tenant models;
- per-user ownership of elections (`Election.owner`) or voter registries;
- cross-tenant data isolation;
- multiple independent administrator datasets within one installation;
- public user registration or self-service signup.

### Hardcoded Administrator identity

Do not write code that assumes:

- the Administrator's username is `"admin"`;
- the Administrator's user ID is `1`;
- a singleton database relationship identifies the Administrator;
- a hardcoded settings constant identifies the Administrator.

The Administrator must be a standard database-backed `User` with `role = ADMIN`, created during the first-run installation setup.

### Internet hosting assumptions

Do not design components around cloud hosting, public domains, or public network exposure. Electra is an intranet web application deployed on a local network.

---

# 22. Dependency Direction

Prefer this dependency direction:

```text
project configuration
        ↓
domain apps
        ↓
services
        ↓
models / query layer
        ↓
database
```

HTTP views and WebSocket consumers are delivery mechanisms around the service layer.

Avoid circular domain dependencies.

When a workflow crosses domains, use service boundaries rather than duplicating the same business rule in multiple apps.

---

# 23. Architecture Change Rule

When a new implementation requirement appears:

1. Check whether the behavior already exists in the locked specification.
2. Identify which layer owns the behavior.
3. Check the relevant focused contract.
4. Prefer extending the existing boundary over creating a parallel mechanism.
5. Do not introduce infrastructure solely to solve a local implementation inconvenience.
6. If the change alters a locked architectural invariant, stop and treat it as an explicit requirements change.

---

# 24. Architecture Completion Criteria

The architecture is being followed when:

- domain ownership is clear;
- views remain thin;
- consumers remain communication-focused;
- services own domain operations;
- persistence constraints live with the data model/database;
- complex reads have a defined query boundary;
- browser state is never authoritative;
- HTTP/WS responsibilities remain separated;
- booth isolation is server-derived;
- ballot secrecy is preserved;
- critical operations are transactional;
- post-commit events are emitted after commit;
- no application heartbeat has been introduced;
- no forbidden infrastructure has been introduced.

Detailed verification belongs in `08-testing-definition-of-done.md`.
