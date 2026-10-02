# 07 — UI Design & Theme Contract

> **Status:** LOCKED  
> **Authority:** Guided by image assets in `references/` and locked requirements in `electra-locked-in.txt`.

---

## 1. Product Positioning & Value Proposition

Electra is a **controlled, real-world election simulation platform** deployed over a dedicated Local-Area Network (LAN), designed for institutions and campuses.

### Core Highlight: Authentic Physical Election Simulation
- **Not a generic online voting tool**: Unlike Google Forms, email links, or generic web polls, Electra simulates the authentic, physical election day experience.
- **Physical Booth Kiosks**: Controlled in-person voting terminals bound to physical booths.
- **Officer Check-In Desks**: Authentic voter verification and single-use physical booth authorization.
- **Live Operational Simulation**: Real-time turnout, precinct monitoring, and live tallying.
- **Copy Direction**: Showcase the real-world election use case and atmosphere. Never expose developer jargon (such as PostgreSQL, ASGI channel layers, server authority internals) in public user-facing copy.

---

## 2. Reference Assets Authority & Primacy Rule

All UI development across the entire project must strictly adhere to the visual theme established here and the reference images in:

```text
references/
├── landing.png        # Landing Page: Hero typography, floating header, rolling words ticker
├── login.png          # Auth & Setup: Dotted grid background, centered card (20px radius), Continue CTA
└── homepage_ref.jpg   # Admin Dashboard: Post-login analytics, candidate cards, live stats
```

**Primacy Rule:** This UI theme (color palette, Inter typography, dotted grid background, minimalist cards, and aesthetic direction) is the **primary UI authority for the entire project**. Whenever any visual or styling conflict occurs between documents or legacy templates, **this theme takes precedence**. If any visual requirement is ambiguous, **stop and ask the user for clarification** before proceeding.

---

## 3. Explicit DOs and DONTs

### DO
1. **Focus on Typography First**:
   - Use modern, high-grade sans-serif typography (**Inter**).
   - Display headings: Heavy weights (`700`, `800`), tight tracking (`-0.03em` to `-0.04em`), balanced line heights (`1.1` to `1.2`).
   - Eyebrows / Overlines: Uppercase, tracked out (`0.18em` to `0.22em`), bold (`700`), small (`11px` to `12px`).
   - Body copy: Spacious, high legibility (`15px` to `17px`), line height `1.6` to `1.65`.
2. **Adhere to the Locked Minimalist Palette**:
   - **White**: `#FFFFFF`
   - **Black / Deep Charcoal**: `#0F172A`, `#1E293B`, `#111827`
   - **Light Grey**: `#F8FAFC`, `#F1F5F9`, `#E2E8F0`, `#64748B`, `#475569`
   - **Blue**: `#2563EB`, `#1D4ED8`
   - **Light Blue**: `#EFF6FF`, `#DBEAFE`
   - *Slight semantic variations* are allowed for operational status indicators (e.g. green/amber) on dashboard cards.
3. **Single-Screen Landing Page**:
   - Strictly one section, non-scrollable (`height: 100vh`/`100dvh`, `overflow: hidden`).
   - Zero headers and zero footers.
   - High-contrast, pill call-to-action button directing to Administrator Setup or Sign In.
4. **Admin Dashboard Structure (per `homepage_ref.jpg`)**:
   - Clean left navigation sidebar with search and grouped links.
   - Top election banner with live countdown timer and action buttons.
   - Structured candidate cards with metrics, win rates, and candidate avatars.
   - Candidate performance table and demographic/turnout widgets.

### DONT
1. **NO Logos, Icons, or Emojis on the Landing Page**:
   - Absolutely no SVG emblems, icons, arrows, or emojis on the public landing screen. Rely purely on typography, proportions, and negative space.
2. **NO Purple or Neon Gradients**:
   - Never use purple, magenta, or neon gradients anywhere in the application.
3. **NO Developer/Technical Jargon in User-Facing Copy**:
   - Avoid database engine mentions, ASGI/WebSocket technical descriptions, or internal state machine names in end-user text.
4. **NO Generic Web Poll / Online Survey Feel**:
   - Interface copy and layout must always reflect an institutional polling station platform.
5. **NO Multi-Section / Scrolling Public Landing Page**:
   - Do not add multiple feature rows, pricing tables, or scrollable marketing blocks.

---

## 4. Screen Specifications

### 4.1. Public Landing Page (`/`)
- **Direct Reference Authority**: Exact implementation of `references/Screenshot 2026-10-02 204102.png`.
- **Viewport Constraints**: Single-screen, strictly non-scrollable (`height: 100vh`/`100dvh`, `overflow: hidden`).
- **Floating Header Navigation**:
  - Centered floating card with rounded corners (`22px`), white surface, soft drop shadow.
  - Brand: `electra` (pure typography, lowercase bold, zero icons/emojis).
  - Nav Links: `Working`, `Features`, `About` (clean typography text links; action button `Start now` handles entry).
- **Hero Typography & Dynamic Ticker**:
  - Static Line: `Real election experience for` (heavy bold display `font-weight: 800`).
  - Constantly Scrolling Up Words Ticker: `students`, `childrens`, `teenagers`, `non voters` (smooth infinite vertical roll).
- **Description**: Concise, centered one-line description emphasizing real-world physical election simulation.
- **Call to Action**: High-contrast black pill button labeled **`Start now`**, directing dynamically to `/accounts/setup/` or `/accounts/login/`.
- **Bottom Anchor**: Dark device top frame peeking up from the lower viewport edge, replicating the exact visual balance of the reference screenshot without introducing vertical scrolling.

### 4.2. Setup Interface (`/accounts/setup/`)
- **Direct Reference Authority**: Follows `references/login.png`.
- Centered white card (`border-radius: 20px`, subtle border, soft shadow) over a dotted grid canvas (`#FAFAFA`).
- Typography: Inter bold display `Set up Electra`, clean input fields with 10px radius, full-width black button `Continue`.
- Transitions the installation from `UNINITIALIZED` to `INITIALIZED`, then redirects to `accounts:login`.

### 4.3. Unified Login (`/accounts/login/`)
- **Direct Reference Authority**: Exact implementation of `references/login.png`.
- Unified single login interface for all roles (Administrator, Officer Stations, Voting Kiosks).
- Typography: Inter bold display `Sign in to Electra`, subtitle `Welcome back. Enter your account or device credentials.`
- Dotted grid background canvas (`#FAFAFA`), clean inputs (`Identifier` and `Password`), full-width black button `Continue`.
- Prevents bfcache back-forward caching via `@never_cache` and `pageshow` reload listener.

### 4.4. Authenticated Admin Dashboard (`homepage_ref.jpg`)
- Multi-column administrative workspace:
  - Sidebar: Navigation (`Analytics`, `Polls`, `Settings`, `Support`).
  - Top Hero Banner: Active Election overview, live countdown timer.
  - Analytics & Leaderboards: Real-time turnout donut chart, candidate performance cards.
  - Candidate Grid / Table: Filterable roster with election-day metrics.

---

## 5. Development Workflow Rules

1. Before modifying templates or stylesheets, review this contract and the files in `references/`.
2. Do not invent design systems or introduce foreign CSS frameworks (Tailwind utility tokens must map strictly to this palette).
3. Always ask the user if any layout or presentation choice is ambiguous.
