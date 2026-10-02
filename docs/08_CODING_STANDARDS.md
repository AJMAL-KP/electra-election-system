# 07 — Coding Standards

## 1. Purpose

This is the implementation coding contract for Electra.

It locks code organization, responsibility boundaries, security/concurrency rules, and change discipline.

`electra-locked-in.txt` remains the final source of truth. Do not invent behavior when the specification is silent.

---

## 2. Locked Stack

Use:

```text
Python
Django
Django Channels
Daphne / ASGI
PostgreSQL
Django ORM
Django session authentication
Django CSRF protection
transaction.atomic()
select_for_update()
```

Do not introduce:

```text
React
JWT
Redis
Celery
Docker/Kubernetes
microservices
offline voting
```

Do not add infrastructure without an explicit specification decision.

---

## 3. App Ownership

Use four domain apps:

```text
accounts/
elections/
voters/
voting/
```

### accounts

Owns User, roles, Device, credentials, DeviceSession, authentication, permissions, and session enforcement.

### elections

Owns Election, Position, Candidate, eligibility rules, configuration validation, and election lifecycle.

### voters

Owns Voter, AcademicGroup, ElectionVoter, CSV/Excel import, enrollment, filtering, allocation, and pre-election reallocation.

### voting

Owns VoterAuthorization, Vote, authorization, ballot generation/validation, atomic vote recording, kiosk state, officer/kiosk workflow, WebSockets, turnout, results, and reconnection/resynchronization.

Do not create new apps for small features without a real domain boundary.

---

## 4. Layering

Use:

```text
Browser
  ↓
HTTP Views / WebSocket Consumers
  ↓
Services
  ↓
Selectors / Models
  ↓
PostgreSQL
```

### Views

Handle HTTP/UI concerns:

- authentication checks;
- input/form handling;
- service calls;
- rendering;
- HTTP responses.

Do not put critical business workflows in views.

### Consumers

Handle:

- WebSocket connection lifecycle;
- authentication;
- event reception/delivery;
- connection-specific behavior.

Do not put the voting transaction or duplicated domain rules in consumers.

### Services

Own:

- business rules;
- domain operations;
- critical transactions;
- concurrency control;
- state transitions.

Examples:

```text
elections.services
    validate_election_configuration()
    start_election()
    close_election()

voters.services
    allocate_voter()
    reallocate_voters()
    enroll_voters()

voting.services
    create_authorization()
    cancel_authorization()
    build_ballot()
    submit_ballot()
    calculate_turnout()
    publish_results()

accounts.services
    generate_device_credentials()
    rotate_device_credentials()
    revoke_device_credentials()
    create_device_session()
```

### Models

Own persisted data, relationships, database constraints, and small local invariants.

Do not turn models into large workflow controllers.

### Selectors / Queries

Use them for reusable/read-heavy query logic when they make the code clearer.

Do not create abstractions that only wrap an obvious one-line query.

---

## 5. Core Rule

Business logic must not be duplicated across views, consumers, forms, JavaScript, and services.

Prefer:

```python
result = submit_ballot(...)
```

over implementing ballot rules directly in the caller.

The layer that owns a domain operation owns its rules.

---

## 6. Server Authority

Treat every browser value as untrusted.

The server must validate:

- identity and role;
- booth binding;
- election state/time;
- voter eligibility;
- allocation;
- kiosk identity/readiness;
- authorization state;
- candidate/position validity;
- duplicate-vote conditions.

Client-side validation is usability only.

A disabled button, hidden option, or local state is not security.

---

## 7. Authentication and Sessions

Use Django session authentication and CSRF protection.

Roles:

```text
ADMIN
OFFICER
KIOSK
```

### Installation Administrator Standards
- Exactly one human Administrator account exists per installation (`role = Role.ADMIN`).
- Administrator creation occurs during first-run setup (`UNINITIALIZED → INITIALIZED`) handled by `accounts.services.initialize_installation()`.
- **Dynamic Identity Rule:** Never hardcode administrator usernames (`"admin"`), primary keys (`id == 1`), or default passwords in models, services, views, selectors, or tests. Resolve the administrator dynamically via `User.objects.filter(role=Role.ADMIN)`.

### Technical Devices (Officer & Kiosk)
- Officer and Kiosk are technical identities bound to physical `Device` records.
- Derive the Officer's booth from the authenticated `Device`. Do not trust a browser-selected booth ID.
- Enforce exactly one active `DeviceSession` per Officer and Kiosk device.
- Credential rotation or revocation must invalidate the active session and close associated WebSocket connections.

---

## 8. Booth Isolation

Treat booth boundaries as security boundaries.

For an Officer:

```text
authenticated Device
      ↓
server-derived Booth
      ↓
allowed voters + paired Kiosk
```

An Officer must not authorize a voter from another booth or affect another booth's kiosk.

Do not use a client-supplied booth ID as the authority when the server already knows the binding.

---

## 9. Ballot Secrecy

`Vote` contains only:

```text
election
candidate
created_at
```

Never add:

```text
voter_id
election_voter_id
authorization_id
```

Do not create another direct voter-to-vote link elsewhere.

Never expose candidate selections to Officers.

Do not send candidate-selection data through Officer WebSockets.

Do not put candidate identity into operational audit events.

---

## 10. HTTP / WebSocket Boundary

### HTTP

Use for:

- authentication;
- CRUD/configuration;
- voter search/import;
- candidate/booth management;
- authorization creation/cancellation;
- ballot retrieval;
- ballot submission;
- results;
- credential rotation.

### WebSocket

Use for:

- connection/readiness/fullscreen state;
- lock/unlock notifications;
- election lifecycle;
- authorization notifications;
- ballot-recorded notifications;
- turnout;
- resynchronization.

**Never submit the actual vote over WebSocket.**

---

## 11. Transactions and Locks

Critical voting operations must use:

```python
transaction.atomic()
```

and appropriate:

```python
select_for_update()
```

Keep logically atomic operations inside one transaction.

Use the locked order:

```text
Election
  ↓
ElectionVoter
  ↓
Authorization
  ↓
Kiosk/device where required
```

Preserve this order whenever those resources are locked together.

---

## 12. Authorization

`create_authorization()` must validate inside the transaction:

- Election is `ACTIVE`;
- current time is valid;
- voter exists and is enrolled;
- voter belongs to the Officer's booth;
- `has_voted` is false;
- kiosk is connected;
- kiosk is ready/fullscreen;
- no `ACTIVE` authorization already exists.

Then:

```text
create ACTIVE authorization
COMMIT
```

Only after commit send the kiosk unlock event.

Use `transaction.on_commit()` for the post-commit event.

Never report authorization success before commit.

---

## 13. Ballot Submission

The kiosk submits the complete ballot over HTTP.

The atomic operation must:

```text
lock required rows
→ validate election/voter/authorization/kiosk
→ validate every position
→ validate every candidate
→ create all Vote rows
→ has_voted = true
→ authorization = USED
→ kiosk = LOCKED
→ COMMIT
```

Only after commit:

```text
HTTP success
ballot.recorded
turnout.updated
```

Any failure before commit must roll back the entire ballot.

No partial multi-position ballot is acceptable.

---

## 14. Duplicate Voting and Closure

Do not rely on UI checks.

Duplicate prevention uses server validation, transaction locking, and database constraints/invariants where applicable.

Election closure must lock the `Election` row.

Therefore:

```text
vote transaction ↔ close transaction
```

have a deterministic race boundary.

If closure wins the lock, later voting is rejected because the election is `CLOSED`.

---

## 15. Election Timing

Use:

```text
starts_at
ends_at
```

The server enforces:

```text
now >= ends_at
```

as the voting deadline.

At/after the deadline:

```text
no new authorization
no ballot acceptance
```

Use the defined `close_if_expired()` approach.

Do not introduce Celery, APScheduler, or another scheduler solely for election expiry.

---

## 16. Locked States

Do not invent states casually.

Election:

```text
DRAFT
ACTIVE
CLOSED
RESULTS_PUBLISHED
```

Authorization:

```text
ACTIVE
USED
CANCELLED
```

Kiosk:

```text
OFFLINE
NOT_READY
READY
UNLOCKED
VOTING
LOCKED
```

There is no application-level heartbeat.

Kiosk liveness uses the WebSocket connection/disconnection lifecycle plus readiness/fullscreen state.

---

## 17. Client State and Reconnection

The server/database is authoritative for:

- election state;
- voter state;
- authorization;
- vote acceptance;
- kiosk state;
- booth binding;
- session validity.

Never treat stale browser state as proof of anything.

On kiosk WebSocket reconnection:

```text
authenticate session
→ identify kiosk
→ derive booth
→ fetch election state
→ fetch runtime state
→ fetch authorization
→ resynchronize browser
```

Do not restore local state blindly.

---

## 18. Post-Commit Events

Do not emit a successful domain event before the transaction commits.

Use:

```python
transaction.on_commit(...)
```

for events such as:

```text
kiosk.unlock
ballot.recorded
turnout.updated
election.closed
```

The event stream is transport, not authority.

---

## 19. Error Handling

Fail explicitly.

Do not:

- swallow critical exceptions;
- silently continue after failed validation;
- return success after partial failure;
- claim a vote succeeded before commit.

User-facing errors should be clear without exposing internal implementation details.

---

## 20. ORM and Database Rules

Use Django ORM and Django migrations.

For critical queries:

- lock deliberately with `select_for_update()`;
- keep transaction boundaries visible;
- use `select_related()` / `prefetch_related()` when justified;
- avoid accidental N+1 queries;
- do not hide critical locking inside opaque helpers.

Schema changes must use migrations.

Do not rewrite/delete existing migrations merely to simplify development.

Correctness takes priority over premature query optimization.

---

## 21. Naming

Use the domain terminology exactly where practical:

```text
Election
Position
Candidate
Voter
ElectionVoter
Booth
Device
VoterAuthorization
Vote
```

Prefer explicit operation names:

```text
create_authorization()
submit_ballot()
close_election()
allocate_voter()
rotate_device_credentials()
```

Avoid vague names such as:

```text
process()
handle_data()
do_action()
update_stuff()
```

Functions should have one clear responsibility.

---

## 22. Comments

Comment non-obvious **reasons**, especially security and concurrency decisions.

Good:

```python
# Lock Election first so voting and closure share one race boundary.
```

Avoid comments that merely restate code.

Do not copy large sections of the specification into source files.

---

## 23. Templates and JavaScript

Templates are presentation.

JavaScript may handle:

- interaction;
- temporary ballot selections;
- fullscreen API;
- UI state;
- WebSocket presentation.

JavaScript must not own:

- eligibility;
- authorization;
- duplicate-vote prevention;
- ballot validity;
- vote commitment;
- election closure.

The browser is never the security boundary.

---

## 24. Kiosk Code

Temporary ballot selections remain in the browser until final submission.

Do not treat local persistence as proof of a committed vote.

On reconnect, derive state from the server.

The kiosk must never show success merely because the submit button was clicked.

---

## 25. WebSocket Consumers

Keep consumers thin.

A consumer should mainly:

```text
connect
→ authenticate
→ determine scope
→ receive/send events
→ disconnect
```

Domain workflows belong in services.

Do not put complex eligibility, ballot transactions, or duplicated business rules inside consumers.

---

## 26. Security and Sensitive Data

Never log or expose:

- passwords;
- raw credentials;
- voter-to-candidate linkage;
- candidate selections to Officers;
- unnecessary sensitive request data.

Passwords use Django's password hashing.

Credential rotation/revocation invalidates the existing session.

Server-side validation remains mandatory even when the UI already validates the input.

---

## 27. Importers

CSV/Excel import belongs in:

```text
voters/importers.py
```

Use the configured `primary_registry_value` identity.

Import logic must:

- match existing voters correctly;
- prevent duplicate registry records;
- validate input;
- report errors;
- avoid silent destructive changes.

Do not hardcode `student_id`, `university_id`, or another institution-specific identity.

PDF import is not part of the core implementation unless separately specified.

---

## 28. Scope Discipline

Before adding code, check:

```text
Is it required?
Which app owns it?
Which layer owns it?
Does it duplicate an existing rule?
Does it introduce forbidden infrastructure?
Does it change a locked invariant?
```

Do not add:
- multi-tenancy, organization scoping, or tenant routing;
- user-ownership fields or queries on `Election` or `Voter` (e.g., `election.owner` or `filter(owner=request.user)`);
- frameworks, cloud infrastructure, background workers (Celery/Redis), or alternate authentication schemes (JWT).

If the specification and proposed implementation conflict, stop and resolve the conflict rather than silently choosing.

---

## 29. Minimal-Change Rule

For existing code:

1. understand current behavior;
2. identify the affected contract;
3. make the smallest correct change;
4. preserve unrelated behavior;
5. run relevant tests;
6. verify invariants.

Do not rewrite working modules simply because another design looks cleaner.

Refactor when there is a real correctness, maintainability, or architecture reason.

---

## 30. Testing Requirement

Critical business logic requires tests.

At minimum cover:

- authorization;
- duplicate voting;
- atomic ballot recording;
- rollback;
- concurrency;
- election-close races;
- authorization states;
- kiosk states;
- device sessions;
- permissions;
- booth isolation;
- ballot secrecy;
- reconnection;
- voter import;
- eligibility;
- allocation;
- turnout;
- result publication.

A happy-path test alone is not sufficient for critical voting behavior.

---

## 31. Code Review Gate

Before accepting a change:

```text
[ ] Correct app
[ ] Correct layer
[ ] No duplicated business rule
[ ] No forbidden dependency
[ ] Dynamic admin resolution (no hardcoded 'admin' username or ID)
[ ] Installation ownership preserved (no Election.owner or tenant scoping)
[ ] Server validates sensitive input
[ ] Critical transactions are atomic
[ ] Required locks are present
[ ] Lock order is preserved
[ ] Ballot secrecy preserved
[ ] Booth isolation preserved
[ ] Client state not authoritative
[ ] Post-commit events are post-commit
[ ] Relevant tests pass
[ ] No unrelated scope added
```

---

## 32. Definition of Done

A code change is complete when it:

- follows the app/layer boundaries;
- preserves locked invariants;
- handles relevant failure paths;
- has appropriate tests;
- introduces no forbidden infrastructure;
- duplicates no business rules;
- preserves ballot secrecy;
- preserves booth isolation;
- preserves server authority;
- passes relevant tests.

For voting code:

> Correctness, security, atomicity, and concurrency take priority over convenience or brevity.

---

## 33. Final Rule

Use this sequence:

```text
READ
→ CHECK LOCKED SPEC
→ IDENTIFY OWNER/LAYER
→ MAKE SMALLEST CORRECT CHANGE
→ TEST
→ VERIFY INVARIANTS
```

Do not invent unspecified behavior.

Do not silently change a locked decision.

Do not move business logic into a convenient but incorrect layer.
