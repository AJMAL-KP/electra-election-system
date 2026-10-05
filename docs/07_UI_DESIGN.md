# 07 — UI Design & Theme Contract

> **Status:** LOCKED  
> **Authority:** Guided by image assets in `references/` and locked requirements in `electra-locked-in.txt`.

---

## 1. Product Positioning & Value Proposition

Electra is a **controlled, real-world election platform** deployed over a dedicated Local-Area Network (LAN), designed for institutions and campuses.

### Core Highlight: Authentic Physical Election Workflow
- **Not a generic online voting tool**: Unlike Google Forms, email links, or generic web polls, Electra provides an authentic, physical election day experience.
- **Physical Booth Kiosks**: Controlled in-person voting terminals bound to physical booths.
- **Officer Check-In Desks**: Authentic voter verification and single-use physical booth authorization.
- **Live Operational Simulation**: Real-time turnout, booth monitoring, and live tallying.
- **Copy Direction**: Showcase the real-world election use case and atmosphere. Never expose developer jargon (such as PostgreSQL, ASGI channel layers, server authority internals) in public user-facing copy.

---

## 2. Reference Assets Authority & Primacy Rule

All UI development across the entire project must strictly adhere to the visual theme established here and the approved visual reference designs in:

```text
references/
├── 01_landing.png            # Public Landing Page: Centered hero, floating nav, dynamic ticker
├── 02_setup&login.png        # First-Run Setup & Unified Login: Centered card, dotted canvas
├── 03_home.png               # Authenticated Home: Simple, centered landing workspace
├── 04_voter_registry.png     # Master Voter Registry: Persistent registry, dense grouped data
├── 05_election_history.png   # Election History: Past elections, archived results
├── 06.1_voter_list.png       # Stage 1 — Election Voters: Dense/grouped election voter roster
├── 06.2_election_details.png # Stage 2 — Election Details & Candidates: Coherent single workspace
├── 06.3_booth&allocation.png # Stage 3 — Booths & Allocation: Borderless booth cards, allocation
├── 06.4_review.png           # Stage 4 — Review & Start: Validation summary, readiness status
├── 07_live.png               # Live Election Dashboard: Real-time operational monitoring
└── 08_result.png             # Election Results: Position breakdowns, winner focus, print
├── officer_01.png            # Officer Voting Desk: Voter search, authorization, dense voter list
├── kiosk_01.png              # Kiosk Ballot: EVM-style voting interface
└── kiosk_02.png              # Kiosk Ballot Review: Position-by-position review and confirmation
```

**Primacy Rule:** The generated reference designs in `references/` are the **visual source of truth** for the new UI/UX direction. Whenever any visual or styling question occurs, these references define the intended layout, whitespace, typography, and component styling.

The important characteristics preserved across all pages:
- Identical typography hierarchy across pages
- Identical whitespace rhythm
- Center-focused compositions with natural proportional breadths (NOT rigid fixed-width center windows)
- Restrained monochrome palette
- Subtle status treatment and muted accents
- Unified button language
- Four-step progress-stepper treatment for setup
- Borderless and minimal treatment
- Strict rejection of generic SaaS dashboard/card systems

---

## 3. UI/UX Design Direction

The UI direction is intentionally **NOT** a conventional SaaS admin dashboard.

### 3.1 Explicit Prohibitions (What NOT to Build)
1. **NO Permanent Sidebar Navigation**: Do not introduce left or right navigation sidebars in the election setup flow or admin workspaces.
2. **NO Generic Dashboard Grids**: Avoid multi-column card-packed layouts with arbitrary analytics boxes.
3. **NO Excessive Cards or Card-Within-Card Layouts**: Do not box every single element, field, or metric inside separate nested cards.
4. **NO Emoji-Based UI**: Never use emojis for navigation, statuses, or decorative icons.
5. **NO "Vibe Coded" Dashboard Styling**: Avoid neon gradients, glassmorphism excess, arbitrary colorful widgets, or floating novelty badges.
6. **NO Unnecessary Summary Cards**: Metrics must be integrated cleanly into the page flow rather than displayed in rows of generic KPI stat tiles.
7. **NO Large Boxed Containers**: Content should group naturally using subtle lines, transparent sections, and typography rather than heavy bordered boxes.

### 3.2 Design Principles (What TO Build)
1. **Centered Onboarding-Style Experience**:
   - The setup flow (Stages 1–4) is a focused, centered, onboarding-style sequence.
   - It feels like **one continuous application** rather than four disjointed admin screens.
   - Four-step progress indicator at the top anchors the administrator's context.
2. **Dense Data When Appropriate**:
   - "Onboarding style" does **NOT** mean every piece of data must be inside a card or that every operation becomes an isolated page.
   - Dense tables, grouped voter rosters, and compact lists are expected and supported where the workflow demands high information density (e.g., voter selection, allocation).
3. **Refined Typography & Whitespace**:
   - Centered Electra branding (`electra` in lowercase bold).
   - Editorial, refined serif headings (`font-serif`, balanced line heights) for page titles and section headers.
   - Clean, highly legible sans-serif interface text (**Inter** or system sans-serif) for tabular data, labels, buttons, and operational copy.
   - Generous, intentional whitespace separating functional areas.
4. **Restrained Monochrome Palette with Muted Accents**:
   - **Background**: `#FFFFFF` or very subtle light gray (`#FAFAFA` / `#F8FAFC`).
   - **Text**: Deep charcoal / black (`#0F172A`, `#111827`) for high legibility; muted slate (`#64748B`, `#475569`) for secondary copy.
   - **Lines & Borders**: Subtle, whisper-thin gray dividers (`#E2E8F0`, `#F1F5F9`).
   - **Minimal Borders**: Favor borderless/transparent content groupings with subtle hairline dividers.
   - **Status Accents**: Soft, muted pastel badges (soft green for active/ready, soft amber for draft/attention, soft slate for offline/closed). Never neon or high-saturation colors.
5. **Lightweight Action Language**:
   - High-contrast, pill or gently rounded primary buttons (`#0F172A` / `#000000` background with white text).
   - Minimalist ghost or secondary outline buttons for secondary actions.
   - Simple, discrete action buttons for printing rather than bulky utility panels.

---

## 4. Information Architecture & Page Relationships

The administrator working flow is structured as follows:

```text
                  HOME
                    ↓
              START ELECTION
                    ↓
          ┌─────────────────────────────────────────┐
          │ 1. ELECTION VOTERS                      │
          │    ↓                                    │
          │ 2. ELECTION DETAILS & CANDIDATES        │
          │    ↓                                    │
          │ 3. BOOTHS & ALLOCATION                  │
          │    ↓                                    │
          │ 4. REVIEW & START                       │
          └─────────────────────────────────────────┘
                    ↓
          LIVE ELECTION DASHBOARD
                    ↓
              END ELECTION
                    ↓
                RESULTS
                    ↓
                  HOME
```

### Separate Persistent Application Areas
- **Master Voter Registry**: Persistent administrative area accessible from Home. It is **not** part of the four setup stages and does not represent the running election's voter set.
- **Election History**: Persistent administrative area accessible from Home to view past elections and archived results.
- **Active Election Constraint**: Exactly **ONE active/running election** exists at any time across the entire installation.

---

## 5. Screen Specifications

### 5.1 Public Landing Page (`/`) — `references/01_landing.png`
- **Purpose**: Public entryway to the Electra installation over the local network.
- **Layout**: Center-focused responsive composition with natural breadth hierarchy (not a fixed-width window, zero headers/footers).
- **Header**: Floating centered rounded pill (~1020px breadth) with `Electra` branding and text links (`How it works`, `Features`, `About`).
- **Hero**: Focused, comfortably constrained (~620px) heading (`Election experience for`) with a smooth vertical rolling words ticker (`students`, `children`, `teenagers`, `non-voters`).
- **Call to Action**: High-contrast black pill button labeled **`Get started`**, dynamically directing to `/accounts/setup/` (if uninitialized) or `/accounts/login/` (if initialized).
- **Visual Anchor**: Wide application preview window (~1260px) peeking up from the lower viewport edge.

### 5.2 First-Run Setup & Unified Login (`/accounts/setup/`, `/accounts/login/`) — `references/02_setup&login.png`
- **Purpose**: Initial administrator provisioning and subsequent session authentication for Administrator, Officer Stations, and Voting Kiosks.
- **Layout**: Center-focused wide landscape card (~600px breadth, ~300-360px height, 14px border-radius, subtle border, soft drop shadow) with no awkward vertical gaps above inputs.
- **Setup Interface**: Header `Electra`, subtitle `Configure the administrator account to initialize the system.`, fields for administrator username, email, password, and confirm password. Transitions installation from `UNINITIALIZED` to `INITIALIZED`.
- **Login Interface**: Header `Electra`, subtitle `Sign in to access the election management system.`, fields for `Username` and `Password`, full-width high-contrast button `Sign in`.
- **Security**: Strict `@never_cache` and `pageshow` reload listener to prevent back-forward caching.

### 5.3 Authenticated Home (`/admin/` or `/`) — `references/03_home.png`
- **Purpose**: Clean, centered administrative landing hub after login. Not a generic SaaS dashboard.
- **Layout**: Centered composition with generous whitespace and refined serif heading.
- **Primary Actions**:
  - **Start Election**: Prominent primary CTA initiating the four-stage setup flow.
  - **Master Voter Registry**: Clean entry card/link to manage the persistent institutional voter registry.
  - **Election History**: Clean entry card/link to inspect previous elections and past tallies.
- **Status Indicator**: If an election is currently `ACTIVE`, Home displays a direct resume banner to the Live Election Dashboard.

### 5.4 Master Voter Registry (`/voters/registry/`) — `references/04_voter_registry.png`
- **Purpose**: Persistent installation-wide voter registry. Completely separate from election setup.
- **Workflow**:
  - View master voters in dense, grouped display (by Department, Class, Semester, or configured academic hierarchy).
  - Search and filter by name, primary registry identity, or group.
  - Add individual voter, edit voter fields, remove voters.
  - Assign or change academic groups.
  - Bulk import via CSV/Excel with column mapping and preview.
- **Data Boundary**: Does not reflect election participation (`ElectionVoter`); belongs to the institution.

### 5.5 Election History (`/elections/history/`) — `references/05_election_history.png`
- **Purpose**: Archive of past completed elections.
- **Workflow**:
  - List previous elections with date, voter turnout summary, and closure timestamp.
  - Open past election details and inspect full published results.
  - Re-verify tallies and print historical result certificates.
- **Constraint**: Only past (`CLOSED` / `RESULTS_PUBLISHED`) elections are archived here. Only one election can be active at any time.

---

## 6. Four-Stage Election Setup Flow

The election setup wizard guides the administrator from an empty draft to a fully verified, start-ready election. It features a centered four-step stepper at the top of each page.

### 6.1 Stage 1 — Election Voters (`references/06.1_voter_list.png`)
- **Purpose**: Select and configure the voters who belong to this particular election (`ElectionVoter`).
- **Key Concepts**:
  - The master voter registry is **NOT** the election voter list.
  - Election voters are obtained by:
    1. Selecting eligible voters from the persistent master voter registry;
    2. Direct bulk import into the election.
- **UI Elements**:
  - Four-step progress stepper: **Step 1: Election Voters** active.
  - Identifier configuration & academic group hierarchy context.
  - Dense, grouped voter display organized by academic hierarchy (e.g., department/semester or class/section).
  - Search input, group filters, and bulk selection checkboxes.
  - Add/remove controls to enroll or disenroll voters for this election.
  - Summary stats: Total enrolled voters, breakdown by group.
  - Bottom navigation: Cancel/Save draft, Continue to Step 2.

### 6.2 Stage 2 — Election Details & Candidates (`references/06.2_election_details.png`)
- **Purpose**: Single coherent workspace configuring election meta, positions, and candidates.
- **Key Design Rule**: This is **ONE single setup page**, NOT split across multiple wizard steps.
- **UI Elements**:
  - Four-step progress stepper: **Step 2: Details & Candidates** active.
  - **Election Information**:
    - Election Name (required).
    - Optional Description.
    - Start and End timestamps (election window).
  - **Positions & Candidates Workspace**:
    - Create, edit, and remove positions.
    - Set position display order and candidate limits.
    - Add candidates to positions: candidates are selected from the enrolled election voter set.
    - Candidate symbol must be editable (upload or selection).
    - Candidate photo and presentation details.
    - Candidate addition and editing can use clean modal/dialog interactions where appropriate.
    - Remove candidates or reorder positions directly in the workspace.
  - Bottom navigation: Back to Step 1, Continue to Step 3.

### 6.3 Stage 3 — Booths & Allocation (`references/06.3_booth&allocation.png`)
- **Purpose**: Single setup page configuring physical booths, station device pairing, voter allocation, and lightweight printing.
- **Key Design Rule**: This is **ONE single setup page**. Booths are represented as clean, borderless/transparent sections/cards rather than a generic admin table.
- **UI Elements**:
  - Four-step progress stepper: **Step 3: Booths & Allocation** active.
  - **Booths & Devices Section**:
    - Create, edit, and delete polling booths.
    - Each booth clearly displays its paired:
      - **Officer Station**: Device identifier, status, masked credential/pass, pass rotation button.
      - **Voting Kiosk**: Device identifier, status, masked credential/pass, pass rotation button.
    - Secured/masked credential presentation with one-click credential rotation.
  - **Voter Allocation Section**:
    - Allocate enrolled election voters across configured booths.
    - Support automatic balanced allocation.
    - Support manual adjustment and reallocation between booths.
    - Allocation summary: counts per booth, group breakdown.
  - **Lightweight Printing Actions**:
    - Printing is intentionally lightweight and discreet.
    - **Do NOT create a large "print materials" panel, card, or checklist.**
    - Provide simple, elegant print action buttons:
      - `Print voter list`
      - `Print booth slips`
  - Bottom navigation: Back to Step 2, Continue to Step 4.

### 6.4 Stage 4 — Review & Start (`references/06.4_review.png`)
- **Purpose**: Final pre-election verification and readiness screen before activation.
- **UI Elements**:
  - Four-step progress stepper: **Step 4: Review & Start** active.
  - **Top Navigation Row**: Contains **&times; Exit** on the left, centered page headline, and **&larr; Back** alongside the primary **Start election** button on the right. No sticky bottom bar is used, keeping the review layout clean and scrollable.
  - **Comprehensive Election Summary (Left Accordions)**:
    - Election meta (name, scheduled hours, description).
    - Total election voters enrolled and academic groups.
    - Positions and candidate roster count with candidate names.
    - Booths count and allocation completeness (100% voters allocated check).
    - Validation checklist status.
  - **Validation & Device Readiness Status**:
    - Clear distinction between:
      - `Valid / Ready` (green accent)
      - `Incomplete / Invalid` (amber/red accent)
      - `Device Not Ready` (Officer/Kiosk offline or not logged in)
    - Automated configuration checks (duplicate rules, orphan positions, unallocated voters).
    - Runtime check: verify Officer Stations and Kiosks are logged in and connected.
  - **Primary Actions**:
    - **Save as Draft**: Available via the Exit modal workflow, recording progress in `DRAFT` state (`Election.is_saved_draft = true`) for later resumption.
    - **Start Election**: Prominent button in the top navigation row that opens the cutoff confirmation modal, allowing the admin to set `ends_at` and trigger the atomic transition from `DRAFT` to `ACTIVE`.
    - Once started, the configuration is frozen per system invariants.

---

## 7. Live Election Dashboard (`references/07_live.png`)

- **Purpose**: Real-time operational monitoring during active voting.
- **Key Design Rule**: The Live Dashboard is **NOT part of the four-step setup wizard**. It is a dedicated operational monitoring screen entered after starting the election.
- **UI Elements**:
  - Header: Election Name, live status pill (`ACTIVE`), and countdown timer (`Time Remaining`).
  - Top Metrics:
    - Total eligible voters.
    - Total ballots cast (voted).
    - Real-time overall turnout percentage.
  - Booth-Wise Operational Grid:
    - Booth identifier.
    - Officer Station connection status (`Logged In`, `Active`).
    - Voting Kiosk status (`Connected`, `Ready`, `Voting`, `Locked`).
    - Fullscreen state indicator.
    - Booth-specific turnout count and percentage.
    - Credential management controls (rotate pass / revoke if necessary).
  - Real-Time Updates: Backed by Django Channels WebSocket events (`ballot.recorded`, `turnout.updated`, `kiosk.ready`).
  - Primary Action: High-contrast **`End Election`** button (triggers confirmation modal, transitioning `ACTIVE → CLOSED`).

---

## 8. Election Results (`references/08_result.png`)

- **Purpose**: Final election outcome display after election closure.
- **UI Elements**:
  - Header: Election title, closure timestamp, total votes recorded, overall turnout.
  - Position Breakdowns:
    - Position title and total votes cast for this position.
    - Candidate list with individual vote counts and vote share percentages.
    - Visual identification/focus on the **Winner** (refined highlight styling).
  - Actions:
    - **Print Results**: Generates a clean, institutional paper tally sheet.
    - **Return to Home**: Navigates back to the authenticated Home landing page.
- **Integrity Rule**: Results are calculated strictly from recorded `Vote` records only after the election is `CLOSED`.

---

## 9. Officer Voting Desk (`references/officer_01.png`)

- **Purpose**: Primary operational interface for the polling officer to identify allocated voters at their designated booth and authorize them for voting on the linked kiosk.
- **Viewport & Responsive Architecture**:
  - On standard desktop terminals, the viewport is strictly fixed (`height: 100vh; overflow: hidden;`), with the header, search, tabs, and details panel stationary and only the voter roster scrollable.
  - **Responsive Behavior on Smaller Screens / Tablets (`<= 920px`)**: The workspace adapts to a vertical single-column flow with full-page scrolling: the **Voter Details & Action Panel is displayed first** (`order: 1`), followed by the **Voter Search, Filter Tabs, and Scrollable Roster below it** (`order: 2`), enabling comfortable physical verification on touch or tablet terminals.
- **Header Structure**:
  - Clean, compact editorial header featuring `Voting Desk` in *Newsreader* serif directly at the top, pushing workspace content upwards to maximize voter roster visibility.
  - Subtitle with current active or draft election name.
  - Contextual meta line: `Booth XX · Officer` alongside kiosk runtime readiness indicator (`● Kiosk Ready` / `● Kiosk Offline`) and live last-updated timestamp (`Updated: <time>`).
  - Subtle top-right navigation action: `Log out`.
- **Voter Search & Filter Controls**:
  - Minimal search input: `"Search voter by name or ID..."` with live keystroke filtering.
  - Three state filter tabs:
    - **Not voted**: Filters to allocated voters who have not yet cast their ballot (`has_voted = false` and no active authorization).
    - **Voted**: Filters to voters who have completed ballot submission (`has_voted = true`).
    - **All**: Displays all allocated voters for this booth.
  - Note: There is no separate "In progress" filter tab because only one voter may have an active authorization at any moment, and that voter is highlighted and locked into the right-hand inspection panel. Group filtering dropdowns are removed from the live desk to maximize focus and speed.
- **Allocated Voter List (Left Column)**:
  - Scrollable card/row list for allocated booth voters.
  - Displays: Voter ID, Full Name, and voting status pill (`NOT VOTED`, `VOTED`, or `ACTIVE`).
  - Keyboard navigation or row click selects the voter and immediately populates the right panel.
- **Voter Details & Action Panel (Right Column)**:
  - Fixed right-hand panel with small uppercase section title: `VOTER DETAILS`.
  - Large *Newsreader* serif voter name and voter ID headline.
  - Academic metadata grid/table: Roll Number, Academic Group, and Gender.
  - Dynamic status indicator box: Displays current state (`NOT VOTED - Eligible to vote`, `AUTHORIZATION IN PROGRESS`, or `VOTED`).
  - Primary Action Button: Full-width high-contrast **`Authorize voter`** button.
    - Disabled if the kiosk is offline or not ready.
    - Disabled if another authorization is active on this booth.
    - Disabled if the voter has already voted or the election is not active.
    - On click, issues an atomic server-side POST request to create a `VoterAuthorization` (`ACTIVE`), locking the record and emitting the `kiosk.unlock` WebSocket event.
- **Draft State Lockdown & Blur**:
  - If the election is in `DRAFT` status:
    - Officer login is permitted so the officer can inspect setup and connection.
    - The underlying Voting Desk UI renders in the background but is completely blurred (`filter: blur(8px); pointer-events: none;`).
    - A centered institutional notice card displays the *Newsreader* serif `Electra` wordmark, the title `Election Not Active Yet`, explanatory text ("Voting has not started. You will be able to search and authorize voters once the administrator starts the election."), and a solid black **`Log Out Station`** button.
    - All voter authorization actions are strictly disabled on both client and server (`Election = ACTIVE` server-authoritative invariant).
- **Session Revocation & Credential Lifecycle**:
  - Devices are bound 1:1 to booths; there is no "unassigned" device state.
  - Officers can only authenticate using credentials associated with `ACTIVE` or `DRAFT` elections.
  - When an election closes, all device credentials and active sessions for that election are automatically purged.
  - If a device session is terminated, revoked, or invalid, the interface canonicalizes to `session_revoked.html`.
- **Integrity & Ballot Secrecy Rule**:
  - The officer interface reflects authoritative allocation and voting status from the server.
  - The officer station never receives, displays, or logs any candidate selections, maintaining absolute ballot secrecy.

---

## 10. Kiosk Ballot (`references/kiosk_01.png`)

* ****Purpose****: Voting interface through which an authorized voter selects candidates for each election position.

* ****UI Elements****:

  * Context: Electra branding, election title, and booth identifier.
  * Position Progress:
    * Visual progress indicator showing the current election position.
    * Clearly distinguish the active position from completed and upcoming positions.

  * Current Position:
    * Position title.
    * Short instruction explaining that one candidate should be selected.

  * Candidate List:
    * Candidate symbol/icon.
    * Candidate name.
    * Candidate affiliation where applicable.
    * Clear candidate selection control.
    * Clearly visible selected state.

  * Navigation:
    * ****Next →****: Proceeds to the next position after a valid selection.
    * ***Previous***: Returns to the previous position (not in first ballot page).
    

* ****Visual Rules****:

  * Preserve the EVM-inspired voting interaction shown in the reference.
  * The interface should feel like a digital voting booth rather than a conventional web form.
  * Preserve the centered composition, generous whitespace, serif-led typography, and restrained color palette.
  * Candidate rows should remain visually lightweight and spacious.
  * Do not wrap the entire ballot in a generic card.
  * No sidebar, dashboard elements, unnecessary navigation, or application chrome.

* ****Integrity Rule****: The kiosk only permits voting interaction for a valid authorized voting session and does not expose or request information that is outside the kiosk's required voting workflow.

---

## 11. Kiosk Ballot Review (`references/kiosk_02.png`)

* ****Purpose****: Final review screen allowing the voter to verify all selections before submitting the vote.

* ****UI Elements****:
  * Context: Election title, booth identifier, and completed position progress indicator.

  * Review Heading:
    * ****Review your vote****.
    * Clear instruction that selections can be changed before submission.

  * Selection Summary:
    * One row for each election position.
    * Position title.
    * Selected candidate symbol/icon.
    * Selected candidate name.
    * Candidate affiliation where applicable.
    * ****Change**** action for each selection.

  * Actions:
    * ****Go back****: Returns to the ballot-selection flow so the voter can change selections.
    * ****Confirm vote****: Final submission of the completed ballot.

* ****Visual Rules****:
  * Preserve the centered, minimal Electra composition.
  * Use whitespace and subtle separation between position rows rather than cards.
  * Keep **Confirm vote** as the visually prominent primary action.
  * Keep **Go back** as the secondary action.
  * Preserve the same typography, spacing, muted palette, and restrained visual hierarchy as `kiosk_02`.
  * No sidebar, dashboard elements, unnecessary header/footer, or generic card layout.

* ****Integrity Rule****: The review screen displays the voter's current selections for confirmation; the vote is not considered finally submitted until the voter explicitly activates **Confirm vote**.

---

## 12. Development Workflow Rules

1. Before modifying templates or stylesheets, review this contract and the visual references in `references/`.
2. Do not introduce permanent sidebars, generic card dashboards, or multi-step wizard splits that violate the 4-stage setup architecture.
3. Keep the setup flow unified as a centered, continuous onboarding experience.
4. Ensure dense data displays are utilized where appropriate rather than artificially spreading information.
5. All UI changes must preserve backend authority, booth isolation, and ballot secrecy contracts.
