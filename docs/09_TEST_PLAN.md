# 08 — Testing / Definition of Done

> **Status:** LOCKED  
> **Purpose:** Define how Electra is verified and what must be true before a phase or the entire product is considered complete.  
> **Testing philosophy:** Test-first for critical business logic; acceptance/testing-after for straightforward UI work.

---

# 1. Core Testing Principle

Electra is an election workflow, so correctness is more important than test volume.

The most important question is not:

> "Do we have many tests?"

It is:

> "Have we proved that the critical election invariants and failure cases cannot silently break?"

Testing must therefore concentrate on:

- authorization;
- ballot recording;
- duplicate-vote prevention;
- concurrency;
- state transitions;
- permissions;
- election timing/expiry;
- reconnection;
- resynchronization;
- transaction rollback;
- ballot secrecy;
- configuration freeze.

---

# 2. TDD Strategy

Use a pragmatic TDD workflow.

For critical business logic:

```text
Write test
   ↓
Implement
   ↓
Test passes
   ↓
Refactor
```

Test-first is expected for logic where an incorrect implementation can affect election correctness.

This includes:

```text
Authorization
Vote recording
Duplicate prevention
Concurrency
State transitions
Permissions
Election expiry
Reconnection
Resynchronization
```

Do not require textbook TDD for every tiny UI change.

For straightforward UI work:

```text
Implement
   ↓
Run relevant tests
   ↓
Perform acceptance check
   ↓
Fix
```

For example, a simple dashboard card does not require a test to be written before the HTML is created unless the card contains meaningful business behavior.

The distinction is:

```text
Critical domain behavior → test-first
Simple presentation → test-after / acceptance testing
```

---

# 3. Agent Development Workflow

The implementation agent must operate in this order:

```text
READ SPEC
   ↓
UNDERSTAND CURRENT STATE
   ↓
PLAN PHASE
   ↓
CHECK ARCHITECTURE
   ↓
IMPLEMENT
   ↓
RUN TESTS
   ↓
CHECK INVARIANTS
   ↓
CHECK CODE QUALITY
   ↓
FIX
   ↓
REPORT
   ↓
MOVE TO NEXT PHASE
```

Do not skip directly from:

```text
READ SPEC
    ↓
IMPLEMENT
```

The agent must understand the current implementation before modifying it.

---

# 4. Before Starting a Phase

Before writing code, the agent must identify:

### Requirements

- What exact requirements does this phase implement?
- What requirements are explicitly out of scope?
- What existing behavior must remain unchanged?

### Architecture

- Which Django app owns the behavior?
- Is the behavior a model, service, selector, view, consumer, template, or JavaScript concern?
- Does the proposed implementation respect existing application boundaries?

### Data

- Which models are involved?
- Which invariants apply?
- Are database constraints required?

### State

- Which state transitions are involved?
- Which transitions are valid?
- Which transitions must be rejected?

### Concurrency

- Can two requests perform this operation simultaneously?
- Does the operation require `transaction.atomic()`?
- Which rows require `select_for_update()`?
- Is the established lock order preserved?

### Security

- Which role/device may perform the operation?
- Could another booth access it?
- Could the browser manipulate the result?
- Could the implementation expose voter identity or ballot information?

---

# 5. Test Layers

Use tests at the appropriate level.

## 5.1 Model / database tests

Test:

- model validation;
- database constraints;
- uniqueness;
- relationships;
- state values;
- simple model behavior.

Examples:

```text
ElectionVoter unique(election, voter)
```

must be enforced.

Critical persistence invariants must not depend solely on UI validation.

---

## 5.2 Service tests

Service tests are the most important business-logic tests.

Test:

- installation initialization (`accounts.services.initialize_installation()`);
- single administrator invariant enforcement;
- authorization creation;
- authorization cancellation;
- ballot validation;
- ballot recording;
- election start;
- election close;
- expiry handling;
- voter allocation;
- credential rotation;
- device session enforcement;
- turnout calculation;
- result publication.

Service tests should verify both:

```text
success path
```

and:

```text
rejection/failure path
```

---

## 5.3 View tests

Test:

- authentication requirements;
- role permissions;
- request validation;
- correct service invocation;
- HTTP response behavior;
- redirects;
- template rendering;
- forbidden access.

Views should not contain enough business logic to require extensive domain testing themselves.

---

## 5.4 WebSocket / consumer tests

Test:

- authentication;
- connection acceptance/rejection;
- kiosk identification;
- booth binding;
- readiness events;
- fullscreen events;
- lock/unlock events;
- election lifecycle events;
- authorization notifications;
- ballot-recorded notifications;
- turnout updates;
- reconnection/resynchronization.

Also verify that sensitive data is not sent to the wrong interface.

---

## 5.5 Integration tests

Integration tests should exercise complete workflows across multiple layers.

At minimum:

```text
Admin logs in (Home)
        ↓
Start Election
        ↓
Stage 1: Election Voters configured (from Master Registry or direct bulk import)
        ↓
Stage 2: Election Details & Candidates configured (single workspace, modal/dialog)
        ↓
Stage 3: Booths & Allocation configured (booths, paired devices, credentials, allocation, lightweight printing)
        ↓
Stage 4: Review & Start (validation verified, runtime readiness verified)
        ↓
Election starts (DRAFT → ACTIVE, configuration frozen)
        ↓
Live Election Dashboard active (real-time monitoring)
        ↓
Officer logs in
        ↓
Kiosk logs in
        ↓
Kiosk becomes READY
        ↓
Officer verifies voter
        ↓
Officer authorizes voter
        ↓
Kiosk unlocks
        ↓
Voter completes ballot
        ↓
Ballot submitted over HTTP
        ↓
Votes committed atomically
        ↓
Turnout updated on Live Dashboard
        ↓
Admin ends election (ACTIVE → CLOSED)
        ↓
Further voting rejected
        ↓
Results calculated and published
        ↓
Return to Home
```

This is the primary end-to-end lifecycle.

---

# 6. Authorization Test Contract

Authorization is critical business logic and must be tested first.

Test that authorization succeeds only when all required conditions are true:

```text
Election is ACTIVE
Current time is valid
Voter exists
Voter is enrolled
Voter belongs to the officer's booth
Voter has not voted
Kiosk is connected
Kiosk is READY/fullscreen
No ACTIVE authorization already exists
```

Test rejection when each condition fails independently.

Examples:

```text
inactive election → reject
expired election → reject
unknown voter → reject
voter not enrolled → reject
wrong booth → reject
has_voted = true → reject
kiosk disconnected → reject
kiosk not ready → reject
fullscreen exited → reject
existing ACTIVE authorization → reject
```

Verify that a failed authorization does not create an `ACTIVE` authorization.

---

# 7. Authorization Transaction Tests

Test that authorization creation is atomic.

Expected:

```text
BEGIN
→ lock required records
→ validate
→ create ACTIVE authorization
→ COMMIT
→ emit unlock event
```

The event must not be emitted before commit.

Test rollback behavior:

```text
validation/write failure
        ↓
ROLLBACK
        ↓
no new authorization
```

Test that concurrent authorization requests cannot create two active authorizations for the same voter.

The implementation must preserve the established lock order:

```text
Election
    ↓
ElectionVoter
    ↓
Authorization
    ↓
Kiosk/device where required
```

---

# 8. Ballot Validation Tests

Test the complete ballot, not merely individual candidate selections.

A valid ballot requires:

- election is active;
- current time is valid;
- authenticated kiosk is correct;
- kiosk matches the authorization;
- authorization is `ACTIVE`;
- voter has not already voted;
- each submitted position belongs to the election;
- voter is eligible for each position;
- each candidate belongs to the submitted position;
- exactly one candidate is selected per position.

Test invalid ballots including:

```text
missing position
extra position
position from another election
candidate from another position
candidate from another election
ineligible position
multiple candidates for one position
missing candidate for a required position
inactive authorization
wrong kiosk
already-voted voter
closed election
expired election
```

The server must reject invalid ballots even if the browser submits them manually.

---

# 9. Atomic Vote Recording Tests

The complete multi-position ballot is one logical transaction.

Test:

```text
one authorization
      ↓
multiple position selections
      ↓
multiple Vote rows
      ↓
one successful commit
```

Verify that on success:

```text
Vote rows exist
ElectionVoter.has_voted = true
Authorization.status = USED
Kiosk = LOCKED
```

Verify that the success response/event happens only after commit.

---

# 10. Rollback Tests

Force a failure during ballot recording.

Expected:

```text
Vote rows = none
has_voted = false
Authorization = ACTIVE
```

No partial ballot may remain.

Example:

```text
Position 1 → valid
Position 2 → valid
Position 3 → invalid
```

The system must not save the first two selections as votes.

The transaction must roll back as a whole.

---

# 11. Duplicate-Vote Tests

Test duplicate submission explicitly.

Scenario:

```text
Request A
    ↓
ballot submitted

Request B
    ↓
same ballot submitted again
```

Only one logical ballot may be accepted.

After the first successful commit:

```text
has_voted = true
Authorization = USED
```

The second request must be rejected.

Also test concurrent duplicate submissions.

Two simultaneous requests must not result in:

```text
two accepted ballots
```

or duplicate Vote records for the same authorization.

---

# 12. Concurrency Tests

Concurrency testing is mandatory for critical operations.

Test at least:

### Two authorizations

```text
Officer request A ─┐
                   ├── same voter
Officer request B ─┘
```

Expected:

```text
one succeeds
one is rejected
```

### Two ballot submissions

```text
Submit A ─┐
           ├── same authorization
Submit B ─┘
```

Expected:

```text
one accepted
one rejected
```

### Vote vs election close

```text
Vote transaction ─┐
                  ├── same Election
Close transaction ┘
```

Expected behavior is deterministic according to lock acquisition.

If the vote obtains the Election lock first, it may complete before closure.

If closure obtains the lock first, the subsequent vote must see `CLOSED` and fail.

---

# 13. Election State Tests

Test the locked election states:

```text
DRAFT
ACTIVE
CLOSED
RESULTS_PUBLISHED
```

Test valid transitions:

```text
DRAFT → ACTIVE
ACTIVE → CLOSED
CLOSED → RESULTS_PUBLISHED
```

Test invalid transitions.

Examples:

```text
CLOSED → ACTIVE
RESULTS_PUBLISHED → ACTIVE
RESULTS_PUBLISHED → CLOSED
DRAFT → CLOSED
DRAFT → RESULTS_PUBLISHED
```

Do not create tests for `SCHEDULED` or `LIVE` as canonical states because those states are not part of the locked implementation.

---

# 14. Election Start Validation Tests (Stage 4 — Review & Start)

Before:

```text
DRAFT → ACTIVE
```

test the **Stage 4 — Review & Start** configuration checkpoint (`references/06.4_review.png`).

Verify rejection and UI reporting when:

```text
name missing
invalid start/end times
no positions
position without candidates
booth missing
booth without Officer device
booth without Kiosk device
inactive credentials
eligible voters not enrolled
ElectionVoter missing booth allocation
invalid eligibility configuration
required stations not logged in / not ready
another election is already ACTIVE (single active election invariant)
```

The review interface must clearly distinguish:
- `Valid / Ready`
- `Incomplete / Invalid`
- `Device Not Ready`

A failed start must leave the election in:

```text
DRAFT
```

---

# 15. Election Expiry Tests

The election does not require a separate persistent expiry state.

Test server-side enforcement:

```text
now < ends_at
    → authorization/ballot may proceed if all other rules pass

now >= ends_at
    → no new authorization
    → no ballot acceptance
```

Test both:

- authorization immediately before the deadline;
- authorization after the deadline;
- ballot submission immediately before the deadline;
- ballot submission after the deadline.

Do not rely on the browser countdown for correctness.

---

# 16. Election Closure Tests

Test that closing the election:

```text
ACTIVE → CLOSED
```

prevents new voting operations.

Test that closure is synchronized correctly with active transactions.

Also verify that:

- connected clients receive the appropriate closure event;
- candidate-wise results are not exposed while the election is active;
- results become available only after closure according to the lifecycle.

---

# 17. Authorization State Tests

Test:

```text
ACTIVE → USED
ACTIVE → CANCELLED
```

Test that terminal states cannot be reused:

```text
USED → ACTIVE       reject
CANCELLED → ACTIVE  reject
USED → CANCELLED    reject
CANCELLED → USED    reject
```

There is no automatic authorization expiry.

Do not implement or test an `EXPIRED` state.

---

# 18. Kiosk State Tests

Test the locked runtime states:

```text
OFFLINE
NOT_READY
READY
UNLOCKED
VOTING
LOCKED
```

Verify important transitions:

```text
connection
readiness/fullscreen
authorization
ballot workflow
successful submission
reset
disconnect
reconnect
```

Test that:

```text
READY
```

means the server knows the kiosk is connected and ready/fullscreen.

Test fullscreen exit:

```text
READY / active kiosk
       ↓
fullscreen exit
       ↓
NOT_READY
```

The Officer must not be able to authorize another voter while the kiosk is not ready.

Do not add an application heartbeat test.

The locked implementation uses WebSocket connection lifecycle for liveness.

---

# 19. Device Session Tests

Test:

```text
Login
→ DeviceSession created
```

Test that a second simultaneous login for the same Device is rejected.

Test:

```text
Logout
→ DeviceSession inactive
```

Test credential rotation/revocation:

```text
active session
     ↓
credential rotation/revocation
     ↓
session invalidated
     ↓
WebSocket closed
```

---

# 20. Permissions / Security Tests

Test each role independently.

### Installation Setup & Single Administrator Tests
- When uninitialized, unauthenticated requests to protected endpoints redirect to the first-run setup view.
- First-run setup creates exactly one Administrator account (`role = Role.ADMIN`).
- After initialization, first-run setup is permanently inaccessible (returns 403 or redirects to login).
- Attempting to create a second Administrator account is strictly rejected by the database/service layer.
- **Dynamic Identity Test:** Test with non-standard administrator credentials (e.g. username `"super_electra_admin"`, ID `42`) to verify no part of the system assumes `username == 'admin'` or `id == 1`.

### Admin

Can perform permitted administrative and configuration operations across the installation:
- Access authenticated Home landing hub (`03_home.png`);
- Access and manage the persistent Master Voter Registry (`04_voter_registry.png`) independently of any active election;
- Access and inspect Election History (`05_election_history.png`);
- Navigate through Stages 1–4 of election setup (`06.1`–`06.4`);
- Cannot start a second election while an election is already `ACTIVE` (strictly enforced single active election constraint);
- Access the Live Election Dashboard (`07_live.png`) only when an election is `ACTIVE`;
- Close an active election to calculate and inspect Results (`08_result.png`).

### Officer

Can operate only the assigned booth workflow.

### Kiosk

Can operate only as its assigned technical kiosk identity.

### Unauthenticated user

Cannot access protected operations.

### Wrong booth

An Officer must not operate another booth.

A Kiosk must not operate as another kiosk/booth.

Test direct HTTP requests, not only UI navigation.

Test WebSocket authentication separately.

---

# 21. Booth Isolation Tests

Test that a device's booth is derived from its server-side identity.

Attempt:

```text
Officer A → Booth B
Kiosk A   → Booth B
```

using manipulated request parameters.

Expected:

```text
rejected
```

The browser must not be able to change its booth by modifying an ID in the request.

---

# 22. Ballot Secrecy Tests

Verify that operational interfaces do not expose voter-to-candidate linkage.

Officer responses/events must not contain:

```text
candidate selection
ballot contents
candidate sequence
```

Kiosk operational events must not expose voter identity unnecessarily.

Vote persistence must remain separated from:

```text
voter_id
election_voter_id
authorization_id
```

Do not add those fields to `Vote` merely to simplify tests or queries.

Audit tests must also verify that voter-to-candidate selections are not logged.

---

# 23. Reconnection Tests

Simulate kiosk disconnection at important points:

```text
before authorization
after authorization
during ballot
after ballot commit
after commit but before HTTP response
```

On reconnect, the server must reconstruct authoritative state.

Verify:

```text
ACTIVE authorization
```

is recovered when the ballot has not been committed.

Verify:

```text
USED authorization
has_voted = true
Vote rows exist
```

is recovered when the ballot was already committed.

Verify:

```text
CANCELLED
```

is recovered when the authorization was cancelled.

The browser must not manufacture a state from stale local data.

---

# 24. Response-Loss Test

This is a required failure test.

Simulate:

```text
Database commit succeeds
        ↓
HTTP response is lost
        ↓
Kiosk believes request failed
        ↓
Kiosk reconnects
```

Expected authoritative state:

```text
Vote exists
has_voted = true
Authorization = USED
```

The kiosk must resynchronize and display the completed/success state rather than attempting to cast another vote.

---

# 25. WebSocket Event Tests

Verify that events representing committed state are emitted only after successful transactions.

Examples:

```text
authorization.created
kiosk.unlock
ballot.recorded
turnout.updated
election.closed
```

Test rollback:

```text
transaction fails
     ↓
business state unchanged
     ↓
corresponding post-commit event not emitted
```

Test that events do not contain prohibited sensitive data.

---

# 26. Voter Registry / Import Tests

Test:

- valid CSV import;
- valid Excel import;
- malformed files;
- missing required columns;
- column mapping;
- preview;
- validation errors;
- duplicate registry identity;
- existing voter matching;
- creation/update behavior;
- academic-group hierarchy;
- import confirmation.

Do not silently create duplicate voters.

The configured `primary_registry_value` must remain the registry-level identity used for duplicate prevention and matching.

---

# 27. Eligibility Tests

Test the locked eligibility semantics:

```text
multiple values inside one rule = OR
different rules = AND
```

Example:

```text
(CSE OR Mechanical OR Civil)
AND
Female
```

Test:

- matching one allowed group;
- matching another allowed group;
- matching none;
- satisfying all rules;
- satisfying only one rule;
- invalid academic group references.

Do not build or test an arbitrary Boolean rule engine.

---

# 28. Allocation Tests

Test:

- individual allocation;
- bulk allocation;
- filtered allocation;
- moving voters before election start;
- booth addition;
- booth removal;
- reallocation after booth removal;
- exactly one booth per `ElectionVoter`;
- frozen allocation after election activation.

Attempt invalid multiple-booth allocation and verify rejection.

---

# 29. Turnout Tests

After successful ballots, verify:

```text
allocated voters
voted voters
turnout
```

remain consistent.

Turnout must change only after successful ballot commitment.

A failed ballot must not increase turnout.

A duplicate submission must not increase turnout twice.

---

# 30. Results Tests

Results are available after closure according to the election lifecycle.

Verify:

```text
Position
    Candidate
        Vote count
```

Test multi-position elections.

Verify that each accepted ballot contributes correctly to the appropriate candidate/position count.

Verify that results are not exposed as candidate-wise active-election results while the election is still `ACTIVE`.

---

# 31. UI Acceptance Testing

Straightforward UI work does not require test-first development for every component.

Use acceptance checks for:

- **Setup Flow (Stages 1–4)**:
  - Centered onboarding-style sequence with 4-step progress indicator;
  - Rejection of generic SaaS admin dashboards (no permanent sidebars, no generic grid cards, no card-within-card containers);
  - **Stage 1 (Election Voters)**: dense grouped display, search/filtering, bulk select, add/remove election voters;
  - **Stage 2 (Election Details & Candidates)**: single coherent setup workspace (not split into multiple wizard pages), inline/modal candidate creation, editable candidate symbol;
  - **Stage 3 (Booths & Allocation)**: single setup page, borderless/transparent booth sections/cards, paired Officer and Kiosk devices, masked credentials, pass rotation, auto/manual allocation, lightweight print action buttons (`Print voter list`, `Print booth slips`, no large print panel);
  - **Stage 4 (Review & Start)**: comprehensive validation summary, clear visual distinction between `Valid / Ready`, `Incomplete / Invalid`, and `Device Not Ready`, Save as Draft vs Start Election actions.
- **Operational Screens**:
  - **Live Election Dashboard**: dedicated monitoring screen during active polling (not part of the wizard), real-time aggregate turnout, booth-wise turnout and statuses, fullscreen indicators, pass management, End Election button;
  - **Election Results**: position breakdowns, candidate tallies and percentages, visual focus on winners, Print Results action, Return to Home;
  - **Home**: centered landing hub connecting Start Election, Master Voter Registry, and Election History;
  - **Master Voter Registry**: persistent administrative area with dense grouped rosters, add/edit/delete, group assignment, bulk import, search/filter;
  - **Election History**: archived past elections and published tallies.
- **Officer & Kiosk Interfaces**:
  - Officer: bound booth dashboard, voter verification, authorization trigger, operational status;
  - Kiosk: locked default, fullscreen behavior, touch-friendly ballot controls, selection visibility, duplicate-submit prevention, success/error presentation, reconnect behavior, no voter identity exposure.

Check:

```text
correct content
correct permissions
correct state displayed
correct action enabled/disabled
correct error message
correct success message
```

---

# 32. Visual / UI Definition of Done

A UI change is complete when:

- it follows the locked UI theme;
- existing design primitives are reused;
- permissions are correct;
- loading/error/empty states are handled;
- relevant acceptance checks pass;
- no business logic was duplicated into templates/JavaScript;
- no critical workflow state is invented in the UI;
- accessibility basics are preserved.

Do not require exhaustive automated visual tests for every simple component.

---

# 33. Security Definition of Done

Before declaring a phase complete, verify:

- authentication is enforced;
- role permissions are enforced;
- booth isolation is enforced;
- CSRF protection is present for relevant HTTP operations;
- session handling is correct;
- credential passwords are hashed;
- credential rotation/revocation invalidates sessions as required;
- no secrets are logged;
- no voter-to-candidate linkage is exposed;
- browser input cannot bypass server validation;
- WebSocket authentication is enforced;
- direct HTTP/WS manipulation is rejected.

---

# 34. Data Integrity Definition of Done

Verify:

- database constraints are active;
- single administrator invariant is strictly enforced;
- elections and voter registry have no user-ownership fields (`Election.owner` does not exist);
- election/voter uniqueness is preserved;
- booth allocation invariants hold;
- authorization states are valid;
- votes are recorded atomically;
- failed transactions leave no partial ballot;
- duplicate submissions cannot create duplicate accepted ballots;
- election closure cannot race into an inconsistent state;
- turnout matches accepted ballots;
- results match committed Vote records.

---

# 35. Architecture Definition of Done

Before completing a phase:

- business logic remains in services;
- views remain transport/UI orchestration;
- consumers remain WebSocket orchestration;
- selectors remain read-oriented;
- models remain focused on persistence and simple domain behavior;
- app boundaries remain intact;
- HTTP/WS boundaries remain intact;
- no unnecessary abstraction layer was introduced;
- no unnecessary dependency was added;
- no business logic was duplicated.

---

# 36. Code Quality Definition of Done

Check:

- descriptive names;
- focused functions;
- explicit state transitions;
- early validation;
- predictable exceptions;
- useful type hints where appropriate;
- no unexplained magic values;
- no giant functions;
- no giant services;
- no duplicated business logic;
- no hidden critical side effects;
- readable tests;
- comments explain important reasons rather than obvious code.

---

# 37. Phase Completion Checklist

After **every phase**, the agent must verify:

```text
□ Requirements satisfied
□ Architecture preserved
□ Invariants preserved
□ Tests passing
□ Critical failure paths tested
□ No duplicated business logic
□ No unnecessary dependencies
□ No obvious security issues
□ Code reasonably clean
□ Documentation updated
```

Do not proceed to the next phase if a critical item is unresolved.

If an item is intentionally deferred, explicitly report it as deferred rather than silently treating it as complete.

---

# 38. Full Product Definition of Done

Electra is not complete merely because all pages render.

The product is complete when the full controlled polling lifecycle works:

```text
Admin logs in (Home)
        ↓
Start Election
        ↓
Stage 1: Election Voters configured (from Master Registry or direct bulk import)
        ↓
Stage 2: Election Details & Candidates configured (single workspace, modal/dialog)
        ↓
Stage 3: Booths & Allocation configured (booths, paired devices, credentials, allocation, lightweight printing)
        ↓
Stage 4: Review & Start (validation verified, runtime readiness verified)
        ↓
Election starts (DRAFT → ACTIVE, configuration frozen)
        ↓
Live Election Dashboard active (real-time monitoring)
        ↓
Officer and Kiosk authenticate
        ↓
Kiosk becomes READY
        ↓
Officer verifies eligible voter
        ↓
Officer authorizes voter
        ↓
Authorization commits
        ↓
Kiosk unlocks
        ↓
Voter completes ballot
        ↓
Complete ballot submitted over HTTP
        ↓
Atomic transaction commits
        ↓
Votes recorded
        ↓
has_voted = true
        ↓
Authorization = USED
        ↓
Kiosk locks
        ↓
Turnout updates on Live Dashboard
        ↓
Admin ends election (ACTIVE → CLOSED)
        ↓
Further voting rejected
        ↓
Results calculated and published
        ↓
Return to Home
```

Every critical transition in this lifecycle must have automated verification.

---

# 39. Required Failure Scenarios Before Final Sign-Off

The following scenarios must be tested before the product is declared complete:

```text
□ Invalid voter
□ Wrong booth
□ Ineligible voter
□ Kiosk disconnected
□ Kiosk not ready
□ Fullscreen exited
□ Duplicate authorization
□ Duplicate ballot submission
□ Invalid candidate
□ Invalid position
□ Missing ballot selection
□ Election expired
□ Election closed during vote attempt
□ Transaction rollback
□ Concurrent authorization
□ Concurrent ballot submission
□ Vote/close race
□ Lost HTTP response after successful commit
□ Kiosk disconnect/reconnect
□ Stale browser state
□ Second device login
□ Credential revocation
□ Credential rotation
□ Unauthorized Admin/Officer/Kiosk access
□ Cross-booth access attempt
□ Sensitive ballot information leakage
□ Second Administrator creation attempt
□ First-run setup access attempt after system initialized
□ Operation attempt while installation is UNINITIALIZED
```

---

# 40. Test Failure Policy

A failing critical test is not a reason to weaken the test.

Do not:

- delete the test;
- skip the test;
- loosen assertions;
- make the test depend on timing luck;
- change expected behavior merely to make CI green.

Instead:

```text
Failure
  ↓
Understand cause
  ↓
Determine whether implementation or requirement is wrong
  ↓
Fix implementation or explicitly change specification
  ↓
Run test again
```

If the requirement itself is ambiguous or contradictory, stop and report the ambiguity rather than inventing behavior.

---

# 41. Refactoring Rule

Refactoring is allowed after behavior is protected by tests.

Preferred sequence:

```text
Test
 ↓
Implement
 ↓
Green
 ↓
Refactor
 ↓
Green again
```

Do not perform large architectural refactors in the middle of an unrelated feature phase unless required to preserve the architecture.

Keep changes focused.

---

# 42. Test Reporting

After each phase, the agent must report:

```text
Phase:
What was implemented:

Tests added:
Tests run:

Passing:
Failing:

Critical scenarios verified:

Invariants checked:

Architecture checks:

Security checks:

Known issues:

Deferred items:

Ready for next phase: YES / NO
```

If tests fail, the phase is not complete unless the failure is explicitly classified as an accepted, documented non-blocking issue.

Do not claim completion when critical tests are failing.

---

# 43. Final Sign-Off

Before final delivery, verify all of the following:

```text
□ Full lifecycle integration test passes
□ Authorization tests pass
□ Atomic ballot tests pass
□ Duplicate-vote tests pass
□ Concurrency tests pass
□ Election state tests pass
□ Expiry tests pass
□ Permission/security tests pass
□ Booth isolation tests pass
□ Reconnection tests pass
□ Resynchronization tests pass
□ Response-loss test passes
□ WebSocket event tests pass
□ Turnout tests pass
□ Results tests pass
□ Import/allocation tests pass
□ UI acceptance checks pass
□ First-run installation setup and single-admin invariant tests pass
□ Dynamic administrator resolution verified (no hardcoded 'admin' assumptions)
□ Database constraints verified
□ Ballot secrecy verified
□ No critical security issues remain
□ No critical test failures remain
□ Architecture remains compliant
□ Documentation is current
```

---

# 44. Final Testing Contract

The implementation agent must follow this rule:

> **Critical election behavior is test-first and invariant-driven. Straightforward presentation work is acceptance-tested without forcing artificial TDD overhead.**

The agent must not optimize for:

```text
maximum number of tests
```

It must optimize for:

```text
maximum confidence in critical correctness
```

The final standard is:

```text
Requirements satisfied
        +
Architecture preserved
        +
Invariants preserved
        +
Critical tests passing
        +
Security verified
        +
Failure paths verified
        +
Code reasonably clean
        +
Documentation current
        =
DONE
```

This document defines the testing and Definition of Done contract for Electra.
