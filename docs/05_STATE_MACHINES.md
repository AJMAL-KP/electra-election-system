# Electra — State machines / flows


> **Status:** LOCKED  
> **Purpose:** Define the small set of authoritative state machines and critical flows that implementation agents must follow.  
> **Source of truth:** `electra-locked-in.txt`  
> **Scope rule:** Do not invent additional persistent states, transition paths, or lifecycle concepts without an explicit specification change.

---

## 1. Important correction: use the locked states

The example state names sometimes used during discussion are **not** the locked Electra states.

Do **not** implement these as the canonical states:

- Kiosk: `CONNECTING`, `AUTHORIZED`, `SUBMITTING`, `SUCCESS`, `RESETTING`
- Authorization: `CREATED`, `CONSUMED`, `EXPIRED`, `REVOKED`
- Election: `SCHEDULED`, `LIVE`

The locked source defines the following states instead:

### Installation initialization

```text
UNINITIALIZED
INITIALIZED
```

### Kiosk runtime

```text
OFFLINE
NOT_READY
READY
UNLOCKED
VOTING
LOCKED
```

### Voter authorization

```text
ACTIVE
USED
CANCELLED
```

There is **no automatic expiry state**.

### Election

```text
DRAFT
ACTIVE
CLOSED
RESULTS_PUBLISHED
```

There is **no `SCHEDULED` or separate `READY` election state**.

Configuration readiness is determined by validation before starting an election.

---

# 1.1 Installation Initialization Flow

Electra requires an explicit first-run initialization flow to establish the single human Administrator for the installation.

## States

```text
UNINITIALIZED
      │
      │ first-run administrator created
      ▼
 INITIALIZED
```

| State | Meaning |
|---|---|
| `UNINITIALIZED` | No Administrator account exists. Any HTTP request redirects to the first-run setup interface. |
| `INITIALIZED` | The single Administrator has been created. First-run setup is permanently disabled; normal login is required. |

## Canonical Flow

```text
                    HTTP Request
                         │
                         ▼
             Check installation state
                         │
         ┌───────────────┴───────────────┐
         ▼                               ▼
   UNINITIALIZED                    INITIALIZED
         │                               │
Prompt first-run setup                   ▼
         │                      Normal session login
Admin enters credentials           (ADMIN / OFFICER / KIOSK)
         │
Create User (role = ADMIN)
         │
         ▼
    INITIALIZED
         │
         ▼
Redirect to Administrator Login
```

## Invariants

- The Administrator identity is dynamic: it is never bound to a hardcoded username (e.g. `"admin"`) or fixed user ID (e.g. `1`).
- Once `INITIALIZED`, the first-run setup endpoint is disabled and cannot be re-entered.
- The installation allows exactly one human Administrator; public self-registration is rejected.

---

# 2. Kiosk State Machine

## 2.1 States

| State | Meaning |
|---|---|
| `OFFLINE` | Server does not currently have a connected kiosk WebSocket/session for the kiosk. |
| `NOT_READY` | Kiosk is connected but is not currently ready for authorization. |
| `READY` | Kiosk WebSocket is connected and the kiosk has reported fullscreen/readiness. It can receive an authorization. |
| `UNLOCKED` | Server has created a valid active voter authorization and the kiosk has been instructed to begin the voting flow. |
| `VOTING` | Voter is actively using the ballot. |
| `LOCKED` | Kiosk is not available for another voter until the current workflow is resolved/reset. |

`READY` is a server-derived operational state.

It means:

1. the kiosk WebSocket is connected;
2. the kiosk has reported readiness/fullscreen;
3. the kiosk is available for authorization.

**There is no application heartbeat.** WebSocket connection lifecycle and readiness signals provide liveness.

---

## 2.2 Canonical kiosk flow

```text
                 WebSocket connects
                       │
                       ▼
                   NOT_READY
                       │
          fullscreen/readiness reported
                       │
                       ▼
                     READY
                       │
             officer authorizes voter
                       │
                       ▼
                   UNLOCKED
                       │
             kiosk begins ballot flow
                       │
                       ▼
                    VOTING
                       │
             final ballot submitted
                       │
                       ▼
                    LOCKED
                       │
              reset/return to ready
                       │
                       ▼
                     READY
```

`OFFLINE` is entered whenever the server loses the kiosk's active WebSocket/session.

```text
READY / NOT_READY / UNLOCKED / VOTING / LOCKED
                         │
                   connection lost
                         ▼
                      OFFLINE
                         │
                  kiosk reconnects
                         ▼
                 authoritative resync
                         │
                         ▼
          server determines correct state
```

The reconnecting browser must never decide its own authoritative state.

---

## 2.3 Kiosk transitions

| From | To | Trigger / guard | Authority |
|---|---|---|---|
| `OFFLINE` | `NOT_READY` | Valid kiosk session/WebSocket connects | Server |
| `NOT_READY` | `READY` | Kiosk reports required readiness/fullscreen | Server |
| `READY` | `UNLOCKED` | Successful authorization transaction commits | Server |
| `UNLOCKED` | `VOTING` | Kiosk enters ballot workflow | Kiosk UI + server-authorized state |
| `VOTING` | `LOCKED` | Complete ballot is successfully committed | Server |
| `READY` | `OFFLINE` | WebSocket/session disconnects | Server |
| `NOT_READY` | `OFFLINE` | WebSocket/session disconnects | Server |
| `UNLOCKED` | `OFFLINE` | Connection is lost | Server |
| `VOTING` | `OFFLINE` | Connection is lost | Server |
| `LOCKED` | `OFFLINE` | Connection is lost | Server |
| `LOCKED` | `READY` | Successful reset/resynchronization after completed workflow | Server |

Implementation must not create arbitrary direct transitions such as:

```text
OFFLINE → VOTING
READY → VOTING
READY → LOCKED
OFFLINE → READY
```

unless the server-side flow explicitly establishes the required intermediate conditions.

---

# 3. Fullscreen / Readiness Flow

Fullscreen is a readiness signal, not an operating-system security boundary.

```text
Kiosk connected
     │
     ▼
NOT_READY
     │
     ├── fullscreen/readiness entered ──► READY
     │
     └── fullscreen not available ──────► NOT_READY
```

If fullscreen is exited during operation:

```text
READY / UNLOCKED / VOTING
          │
     fullscreen exit
          │
          ▼
      NOT_READY
```

The system must not treat browser fullscreen as OS-level kiosk lockdown.

An Officer must not authorize a voter unless the kiosk is in the required ready condition.

---

# 4. Authorization State Machine

## 4.1 States

```text
ACTIVE
  ├──► USED
  └──► CANCELLED
```

`ACTIVE` is the usable authorization state.

`USED` and `CANCELLED` are terminal states.

There is no:

- `CREATED` runtime state separate from `ACTIVE`;
- `CONSUMED` state;
- `EXPIRED` state;
- automatic timeout/expiry state;
- `REVOKED` state.

---

## 4.2 Authorization creation flow

Authorization creation is a transactional server operation.

```text
Officer requests authorization
          │
          ▼
Lock required records
(Election → ElectionVoter → Kiosk)
          │
          ▼
Validate:
- election is active
- election time is valid
- voter is enrolled
- voter belongs to the booth
- voter has not voted
- kiosk is connected/ready
- no active authorization exists
          │
          ▼
Create ACTIVE authorization
          │
       COMMIT
          │
          ▼
Send kiosk unlock event
```

The kiosk unlock event must happen **after commit**, using the post-commit event mechanism.

---

## 4.3 Authorization consumption

A successful ballot submission causes:

```text
ACTIVE
   │
   │ successful atomic ballot transaction
   ▼
USED
```

The same transaction also:

- records all Vote rows for the ballot;
- marks `ElectionVoter.has_voted = true`;
- locks/resets the kiosk workflow as specified.

If any pre-commit validation or write fails, the transaction rolls back and the authorization remains `ACTIVE`.

---

## 4.4 Authorization cancellation

An authorization may be explicitly cancelled by the permitted operational flow:

```text
ACTIVE
   │
   │ cancellation
   ▼
CANCELLED
```

A cancelled authorization cannot be reused.

`USED` and `CANCELLED` cannot transition back to `ACTIVE`.

---

# 5. Election State Machine

## 5.1 States

```text
DRAFT
  │
  │ validated start
  ▼
ACTIVE
  │
  │ close / expiry
  ▼
CLOSED
  │
  │ publish results
  ▼
RESULTS_PUBLISHED
```

There is no:

```text
DRAFT → SCHEDULED → LIVE
```

Use the actual election timestamps (`starts_at`, `ends_at`) plus server-side validation instead.

---

## 5.2 DRAFT

`DRAFT` is the configuration state.

The election may be configured while in `DRAFT`.

### Setup Wizard Stages (`Election.setup_stage`)

Election setup is organized into four sequential wizard stages:
1. **Stage 1: Voters** (`setup_stage = 1`, `references/06.1_voter_list.png`): Enroll voters from the central registry into the election roster.
2. **Stage 2: Details & Candidates** (`setup_stage = 2`, `references/06.2_election_details.png`): Define election name, description, scheduled voting period, positions, and candidates.
3. **Stage 3: Booths & Allocation** (`setup_stage = 3`, `references/06.3_booth&allocation.png`): Configure polling booths, paired officer and kiosk devices, masked cleartext credential distribution, and allocate voters to booths.
4. **Stage 4: Review & Start** (`setup_stage = 4`, `references/06.4_review.png`): Review complete configuration via accordion cards, verify validation status, and launch live election via the top navigation action.

### Save as Draft (`Election.is_saved_draft`)

At any stage of the wizard, the administrator may choose **Save as Draft** to record progress (`is_saved_draft = true`) and return to the main dashboard. The wizard may be resumed at any time from its stored `setup_stage`.

Before changing:

```text
DRAFT → ACTIVE
```

the backend must validate the required configuration, including:

### Election

- name exists;
- start/end times are valid;
- current time is compatible with start;
- at least one position exists;
- every position has candidates.

### Booths

- required booth configuration exists;
- every booth has exactly one Officer device;
- every booth has exactly one Kiosk device;
- required credentials are active.

### Voters

- voters are enrolled;
- every eligible `ElectionVoter` has exactly one booth;
- eligibility configuration is valid.

### Runtime readiness

- configured Officer/Kiosk stations have logged in active sessions as required by the start procedure.

---

## 5.3 ACTIVE

In `ACTIVE`:

- voting operations are allowed only while the election is still within its valid time window;
- server-side checks remain authoritative;
- election configuration is frozen;
- authorization and ballot operations are transactional;
- candidate-wise results are not exposed as active-election results.

Time enforcement is server-side.

If:

```text
now >= ends_at
```

the election cannot accept a new authorization or ballot.

---

## 5.4 Closing

Closing is a server-side state transition:

```text
ACTIVE → CLOSED
```

The closure operation must lock the Election row.

This makes the race between:

```text
vote submission
```

and

```text
election closure
```

deterministic.

No vote may be accepted based only on a stale client-side belief that the election is still open.

### Automatic Post-Closure Device Credential Purge

Upon transitioning to `CLOSED`:
- Any dangling active `VoterAuthorization` records are immediately cancelled (`CANCELLED`).
- All technical device credentials (`Device`, `DeviceSession`, and underlying `User` accounts) associated with the closed election are automatically purged from the database.
- Devices are strictly bound to booths and are never in an "unassigned" state; any invalid or revoked device session routes canonically to `session_revoked.html`.

---

## 5.5 Results publication

After closure:

```text
CLOSED → RESULTS_PUBLISHED
```

Results are calculated from recorded Vote rows.

The results view is organized as:

```text
Position
   └── Candidate
          └── Vote count
```

Candidate-wise results must not be exposed as the active-election results view while the election is still `ACTIVE`.

---

# 6. Ballot Submission Flow

The ballot is a logical unit even when the kiosk UI uses multiple pages.

```text
Kiosk unlocked
      │
      ▼
VOTING
      │
      │ voter selects candidates
      │ selections remain temporary in browser
      ▼
Final ballot submission
      │
      ▼
HTTP request
      │
      ▼
Lock:
Election → ElectionVoter → Authorization
      │
      ▼
Validate:
- election active
- election time valid
- authorization ACTIVE
- authorization belongs to kiosk
- voter has not voted
- every position belongs to election
- voter is eligible for selected position/candidate
- candidate belongs to position
- exactly one candidate selected per position
      │
      ▼
Create all Vote rows
      │
      ▼
has_voted = true
      │
      ▼
Authorization = USED
      │
      ▼
Kiosk = LOCKED
      │
     COMMIT
      │
      ├──► HTTP success
      ├──► ballot.recorded event
      └──► turnout.updated event
```

The actual vote submission is HTTP.

**Do not submit the vote over WebSocket.**

---

# 7. Ballot Failure / Retry Flow

## 7.1 Failure before commit

If validation or database work fails before commit:

```text
No Vote rows created
has_voted = false
Authorization = ACTIVE
```

The kiosk must not display success.

The voter may retry according to the operational flow.

---

## 7.2 Commit succeeds but response is lost

A network failure can occur after the database commit but before the browser receives the HTTP response.

Authoritative state is then:

```text
Vote rows exist
has_voted = true
Authorization = USED
```

The kiosk must not assume the vote failed merely because its response was lost.

On reconnect/resync, the server returns the authoritative state and the kiosk displays the appropriate success/completed state.

This prevents duplicate submission.

---

# 8. Kiosk Reconnection / Resynchronization

A reconnecting kiosk must rebuild its state from the server.

```text
WebSocket reconnect
       │
       ▼
Authenticate Django session
       │
       ▼
Identify kiosk
       │
       ▼
Derive booth from server-side device binding
       │
       ▼
Fetch:
- election state
- kiosk runtime state
- active authorization, if any
       │
       ▼
Determine authoritative state
       │
       ▼
Send state to browser
```

The browser must not restore sensitive workflow state solely from stale local memory.

The server is authoritative for:

- kiosk identity;
- booth;
- election;
- authorization state;
- whether the voter has voted;
- whether the kiosk is available;
- whether the election is open.

---

# 9. Device Session Flow

Only one active session is allowed for a Device.

```text
Login attempt
     │
     ▼
Lock Device
     │
     ├── active DeviceSession exists
     │          │
     │          ▼
     │       reject login
     │
     └── no active session
                │
                ▼
        create DeviceSession
                │
                ▼
             logged in
```

Logout:

```text
logged in
   │
   ▼
DeviceSession inactive
```

Credential rotation/revocation invalidates the active device session and closes its WebSocket connection.

---

# 10. Election Time / Expiry Flow

Election time does not introduce another persistent election state.

Instead:

```text
Election.status = ACTIVE
        │
        ├── now < ends_at
        │       └── voting operations may proceed
        │
        └── now >= ends_at
                └── authorization/ballot operations rejected
```

`close_if_expired()` may be invoked by election requests or connected-client deadline handling.

When the election is closed, the server broadcasts the election-closed event.

No background scheduler such as Celery or APScheduler is required.

---

# 11. WebSocket Event Flow

WebSocket is used for real-time state propagation, not transactional voting.

Important event categories include:

```text
connected
disconnected

ready
not_ready

fullscreen_enter
fullscreen_exit

unlock
lock

authorization_created
authorization_cancelled

ballot_recorded
turnout_updated

election_started
election_closed

resync
```

Events that represent committed business changes must be emitted after the corresponding database transaction commits.

Use:

```python
transaction.on_commit(...)
```

for post-commit event dispatch.

---

# 12. State Authority Rules

These rules are mandatory.

## Server owns authoritative state

The server/database is authoritative for:

- Election status;
- election time validity;
- ElectionVoter.has_voted;
- VoterAuthorization status;
- Vote creation;
- kiosk/device identity;
- booth binding;
- device session validity;
- kiosk operational readiness;
- election closure.

## Browser does not own authoritative state

Browser state may represent temporary UI state such as:

- current ballot page;
- unsubmitted candidate selections;
- loading/submitting UI;
- visual presentation.

It must not decide:

- whether a voter is authorized;
- whether a vote was recorded;
- whether an election is open;
- whether an authorization is still valid;
- whether a kiosk belongs to a booth.

## WebSocket does not own transactional state

WebSocket messages communicate state.

They do not replace database transactions.

The vote itself is submitted through HTTP and committed transactionally.

---

# 13. Invalid Transition Rules

Implementation must reject or prevent transitions that violate the locked lifecycle.

### Kiosk

```text
OFFLINE → VOTING              INVALID
READY → VOTING                INVALID without authorization flow
READY → LOCKED                INVALID without completed workflow
```

### Authorization

```text
USED → ACTIVE                 INVALID
CANCELLED → ACTIVE            INVALID
USED → CANCELLED              INVALID
CANCELLED → USED              INVALID
```

### Election

```text
CLOSED → ACTIVE               INVALID
RESULTS_PUBLISHED → ACTIVE    INVALID
RESULTS_PUBLISHED → CLOSED    INVALID
DRAFT → CLOSED                INVALID
DRAFT → RESULTS_PUBLISHED     INVALID
```

Do not silently add transition shortcuts to make UI behavior easier.

If a new transition is required, treat it as a specification change.

---

# 14. State-Machine Implementation Rules

1. Keep the number of states small.
2. Use the exact locked state names.
3. Do not create states merely to represent temporary UI labels.
4. Do not confuse UI states with persisted domain states.
5. Server-side services own authoritative domain transitions.
6. Critical transitions must use the required transaction/locking rules.
7. Emit WebSocket events only after successful commits where the event represents committed business state.
8. Reconnection must resynchronize from server state.
9. Client-local state must never override server state.
10. Do not introduce heartbeat, scheduler, offline voting, or OS-level kiosk-lockdown logic.
11. Do not invent automatic authorization expiry.
12. Do not add `SCHEDULED`, `LIVE`, or election `READY` states.
13. Do not add `EXPIRED`, `CONSUMED`, or `REVOKED` authorization states.
14. Do not use `CONNECTING`, `AUTHORIZED`, `SUBMITTING`, `SUCCESS`, or `RESETTING` as canonical persisted kiosk states.
15. Temporary UI phases may exist internally in browser code only when they map cleanly onto the locked server/domain lifecycle and do not become alternative authoritative state machines.

---

# 15. Definition of Correctness

The implementation is state-correct when:

- every authoritative state has a defined owner;
- every important transition has a defined trigger and guard;
- invalid transitions cannot occur through normal application paths;
- authorization creation is atomic;
- ballot recording is atomic;
- election closure races are deterministic;
- duplicate voting is prevented;
- reconnecting kiosks recover authoritative state;
- post-commit events cannot announce rolled-back changes;
- the UI cannot manufacture authorization, vote success, election openness, or booth identity;
- the implementation contains no unapproved extra lifecycle states.

This document is the state/flow contract for the implementation agent.
