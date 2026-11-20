# Galaxy Frog workspace redesign and system plan

Status: Implementation started on `feat/workspace`. W0 through W1.2 are complete, including live Supabase email-session acceptance. W1.3 has local ownership and private source reads, with hosted preview Storage acceptance pending. The workspace shell follows in W2.
Saved: 2026-11-18 (Asia/Calcutta).
Updated: 2026-11-20 (Asia/Calcutta) — local Auth connection, live acceptance status, and numeric-code setup alternative.
This document records the agreed future workspace program. It is separate from Phase 8.

## 1. Direction and boundaries

Phase 8 merge was confirmed by the user on 2026-11-19. Workspace work starts from merged `origin/main` at `4299de6`; do not repeat the Phase 8 status audit. Keep the completed provider/consent behavior intact.

Follow [the design direction](WORKSPACE_DESIGN.md) and [the staged setup instructions](WORKSPACE_SETUP.md). A separate `galaxy-frog` Supabase project has been created with explicit approval at a quoted $0/month, and its approved local Auth connection passed live email-session acceptance through FastAPI. Storage configuration/acceptance and the browser sign-in UI remain pending in their later packets.

The redesigned product will provide:

- Mixed-source notebooks containing videos, PDFs, DOCX, text, Markdown, and pasted text.
- Persistent, grouped chats with branches and recoverable generation runs.
- Automatic, notebook-scoped memory with visible management controls.
- Editable notes, summaries, reports, flashcards, quizzes, and mind maps.
- A Gallery containing source visuals and pinned items.
- Optional web research using Tavily, with Gemini generating answers.
- Accounts, private ownership, restricted sharing, and revocable public links.
- Switchable Notebook and Codex-style layouts.
- Responsive light and dark themes, accessibility testing, and optional animated companions.

Retain the existing Python/FastAPI retrieval foundation and Next.js presentation boundary. Expand the modular monolith with workspace services rather than replacing it with microservices.

### Required local skills and design process

Before designing or implementing any workspace work packet, inspect `.agents/skills/` and read the relevant `SKILL.md` files and their required supporting references. Apply the skills during planning, implementation, and verification; merely mentioning them does not satisfy this requirement.

- Use [frontend-design](../.agents/skills/frontend-design/SKILL.md) for every UI design or implementation packet: product-specific reasoning, layout alternatives, typography, a coherent token system, and critique before and after building.
- Use [design-taste-frontend](../.agents/skills/design-taste-frontend/SKILL.md) alongside it for visual quality, composition, plain interface copy, responsive behavior, restrained motion, and applicable assets and verification scripts.
- Use other relevant installed skills for their actual scope, including [gemini-api-dev](../.agents/skills/gemini-api-dev/SKILL.md) when implementing Gemini integration. Provider work remains in Python/FastAPI.

The user requirements, repository architecture, and workspace interaction contract take precedence over conflicting skill recipes. This is a working application: do not impose marketing-page heroes, one-section-per-screen layouts, decorative effects, randomized font/palette choices, or static-export requirements on its workspace. Preserve evidence captions, citation labels, and operational status information. Keep the existing application stack and Base UI accessibility primitives. Record which skill checks apply and explain any exclusions; do not claim an inapplicable or unrun checker passed.

**Rebuild the workspace UI through product reasoning.** Rethink information architecture, navigation, panel composition, hierarchy, interaction patterns, and visual identity across the planned workspace. The existing UI is evidence of current behavior and usability problems, not a layout to reproduce with new colors. Reuse sound components and preserve working behavior, provenance, and API boundaries while rebuilding the presentation in the approved delivery order.

Before workspace shell implementation:

1. Map the core journeys: import/select sources, ask questions, inspect evidence, edit/save artifacts, and resume work. Identify the information and actions each step needs.
2. Compare at least two materially different layout concepts within the Notebook/Focus contract. Use wireframes and realistic Galaxy Frog content; explain the tradeoffs in reading space, evidence visibility, navigation, and small-screen behavior.
3. Choose a reasoned direction and specify its palette, typography roles, spacing, density, component states, and motion. Explain how each major choice serves the product and remove generic or decorative defaults.
4. Build the selected direction in coherent packets, then inspect real browser screenshots and test the journeys in both themes and at desktop and mobile widths. Refine from observed behavior and visual critique.

### What the design should refer to

Use this plan's workflows and interaction contract, actual repository behavior, realistic source/evidence/artifact content, and the existing Galaxy Frog brand assets as the primary brief. The skills' references, examples, and assets provide methods and quality guidance rather than a finished Galaxy Frog design.

Use the plan's notebook-style and Codex-style interaction models as references for source organization, focused work, navigation, and inspectors. Any additional visual references must be explicitly identified with the specific principle being studied. The taste skill names Figma and x.ai as craft benchmarks; they are not workspace templates. Derive Galaxy Frog's own visual system instead of copying another product's screen, branding, or signature effects.

### Typography requirements

- **Use [Doto from Google Fonts](https://fonts.google.com/specimen/Doto) somewhere visibly and intentionally in the shipped redesign.** Give it a defined role, such as the Galaxy Frog wordmark treatment, a workspace welcome heading, or another recurring display element. It must be part of the implemented interface, not only a mockup or an unused font declaration.
- Verify Doto's web-use license, available files, and loading approach before integrating it. If the required asset or license is unavailable, surface that dependency rather than silently substituting another face.
- The existing Geist, Geist Mono, and Space Grotesk choices may be replaced. Select distinctive, readable typefaces for Galaxy Frog; avoid very generic fonts, default AI-generated pairings, and an "AI slop" visual treatment. Explain the chosen families and roles instead of selecting them because they are popular defaults.
- Keep long answers, sources, citations, documents, and controls easy to read. Limit the number of families, use a deliberate type scale, and reserve expressive display typography for suitable sizes and contexts. Check actual font loading, fallback metrics, layout shift, supported characters, and readability in both themes and at narrow widths.

**Working names**

- **Galaxies:** notebooks/projects.
- **Source Pond:** imported resources, with "Sources" as its explanatory label.
- **Chats:** conversations and chat groups.
- **Frog Lab:** generation tools and saved outputs.
- **Gallery:** source visuals and pinned images.
- **Frog Memory:** remembered notebook context.
- **Froggy, Crabby, Kitty:** optional assistant companions.

Keep familiar action labels such as **Add sources**, **New chat**, **Copy**, **Share**, and **Delete**. Store conventional names such as `notebook`, `source`, and `artifact` in APIs; branding belongs in presentation copy.

Initial exclusions: simultaneous collaborative editing, connected-drive synchronization, audio/video generation, arbitrary executable artifacts, and automatic provider substitution.

## 2. Workspace experience and interaction contract

### Layouts and navigation

Provide two views over the same workspace state:

- **Notebook:** collapsible notebook/chat rail, Source Pond on the left, conversation or document in the middle, Frog Lab on the right.
- **Focus:** notebook/chat navigation on the left, conversation in the middle, and a right-hand inspector for sources, documents, Gallery, or generation tools.

Switching layouts must preserve drafts, source selections, open documents, player position, and running jobs. Persist panel sizes and layout preferences per account.

At narrow widths, use one primary pane with clearly labeled navigation between Sources, Chat, and Frog Lab. Do not squeeze the desktop panels onto a phone.

Use the repository's existing shadcn **Base UI**, `base-nova`, Phosphor icons, and theme foundations. Add current Sidebar, Tabs, Resizable, Input Group, Command, Sheet, Dialog, Tooltip, Empty, Progress, and Toast components where appropriate. [Component catalog](https://ui.shadcn.com/docs/components)

### Sources and Gallery

- Support single and bulk imports with individual queued, processing, ready, partial, and failed states.
- Show source type, indexing coverage, processing location, and actionable recovery.
- Selecting a source changes the next request's context; it does not re-import it.
- Snapshot the selected source versions when a run starts. Later selection changes must not alter that run.
- Source preview opens the correct video moment, PDF page, document section, or saved web reference.
- Removing a source from a Galaxy and permanently deleting an uploaded resource are separate actions with clear consequences.
- Gallery shows available video frames, PDF pages/figures, and pinned items with their source links. Missing visual coverage must be visible.
- Gallery supports filtering, keyboard navigation, preview, pin/unpin, and authorized downloads. Removing a pin does not delete the source asset.

### Chats and prompt box

- New chat starts an empty conversation using the current source selection and notebook settings. It does not copy previous messages or grants.
- Support chat groups within each Galaxy, rename, move, pin, archive, delete, and search.
- Deleting a group moves its chats to Ungrouped.
- Editing an earlier prompt creates a branch. Regeneration creates a new answer version without overwriting the original.
- Save prompt drafts and restore them after navigation or refresh. Clear account-scoped browser caches on logout/account change.
- Composer settings include source selection, permitted provider profile, response length, language, thinking level where supported, and research mode.
- Research modes are **Sources**, **Web**, and **Sources + Web**. Sources is the default.
- Enter sends on desktop; Shift+Enter inserts a newline. Respect IME composition and use the send button on mobile.
- While a response runs, retain the draft editor and show Stop. Prevent duplicate sends.

### Answers, documents, and small controls

- Reveal **validated answer blocks**, not unverified token fragments. Keep focus stable and offer "Jump to latest" when the user has scrolled away.
- Citation activation opens the relevant evidence without replacing the conversation.
- Copy answer produces readable Markdown with references; code-block copy copies only code.
- Clipboard failures provide a selectable-text fallback.
- Saving an answer to Frog Lab creates an artifact linked to that answer and its source versions.
- Closing an output tab closes the view, not the artifact or background job.
- Autosave displays Saving, Saved, Offline, or Conflict. Conflicts never silently overwrite another revision.
- Regenerating an edited document creates a new version. Preserve the edited version.
- User edits retain source links but remove any implication that the edited wording has already been verified.
- Share opens a content-and-audience dialog. Publishing requires an explicit action; opening the dialog never publishes.
- Downloads and exports show failures and retry actions instead of false success notifications.

### Companions and accessibility

Use small original companion illustrations with contextual idle, processing, success, and help states. Changing companions affects appearance only.

Use Motion for restrained transitions, respect reduced-motion preferences, and provide a hide-companions setting. Important status information must also appear as text. [Motion accessibility](https://motion.dev/docs/react-accessibility)

Target **WCAG 2.2 AA**, verified through automated and manual checks:

- Keyboard-accessible resize handles, menus, dialogs, tabs, maps, and source controls.
- Visible focus, logical focus order, and focus restoration after closing overlays.
- Accessible names for icon buttons and non-color status indicators.
- Appropriate contrast, zoom/reflow, and touch-target sizing.
- No drag-only operations.
- Mind maps also expose a navigable outline.
- Announce meaningful progress changes without reading every streamed update.

## 3. Technology, persistence, and API design

### Rendering stack

Use a renderer registry keyed by a validated artifact type.

- **Chat answers:** `react-markdown`, `remark-gfm`, math support through KaTeX, and Shiki code highlighting. Render citations through application components. Disable raw HTML and arbitrary generated JSX/MDX execution. [Markdown renderer](https://github.com/remarkjs/react-markdown)
- **Editable documents:** Tiptap's open-source editor with headings, lists, tables, code, equations, and custom citation nodes. Persist a versioned, allowlisted document structure validated by Pydantic. [Tiptap](https://tiptap.dev/docs/editor/getting-started/overview)
- **Flashcards and quizzes:** typed React components backed by validated artifact data.
- **Mind maps:** React Flow using validated nodes, edges, source references, and saved layout. Include its keyboard features and an outline alternative. [Accessibility support](https://reactflow.dev/learn/advanced-use/accessibility)
- **PDF preview:** PDF.js, with its worker bundled by the app.
- **Exports:** Markdown, JSON, print-ready PDF, and DOCX. Use Python document generation for DOCX rather than requiring a paid editor extension. Preserve citations; document any unsupported formatting conversion.

### Authoritative storage and client state

Keep PostgreSQL authoritative for:

- Accounts and notebook ownership.
- Source memberships and immutable source versions.
- Chat groups, chats, messages, branches, and revisions.
- Generation runs and ordered run events.
- Artifact versions and document revisions.
- Memory entries, memory changes, and context summaries.
- Sharing grants and published snapshots.
- Usage reservations, provider attempts, and application caches.

Use private object storage for originals, previews, and exports.

Use **TanStack Query** for remote data and invalidation, and a small **Zustand** store for transient workspace state. Neither replaces server persistence. Disable automatic retries for generation mutations; retry only safe reads under a bounded policy.

### Mixed-source ingestion and citations

Reuse existing video ingestion and add document adapters:

- PDF text extraction with `pypdf`; bounded page rendering for previews and visual processing.
- DOCX extraction with `python-docx`.
- Plain text, Markdown, and pasted text with stable block identifiers.
- Scanned pages use a configured OCR adapter under explicit processing permission; otherwise show OCR-required coverage.

Apply file-type verification, size/page limits, archive expansion limits, parser timeouts, and private storage controls. Do not install system-level converters or download model weights automatically.

Introduce a discriminated citation model:

- Video: original unit/cue IDs and half-open millisecond intervals.
- PDF: source version, page, and supporting excerpt or visual reference.
- DOCX/text: stable section/block reference.
- Web: URL, retrieval date, source snapshot, and supporting excerpt.

Do not fabricate page numbers for DOCX or timestamps for documents. Unresolved temporal questions still require source/event clarification.

### Models and retrieval profiles

Support both explicit cloud embedding profiles:

- **Gemini Embedding 2**, default for new cloud Galaxies.
- **Cohere Embed v4**, selectable when configured with appropriate access.

Use separate collections, initially at 1,536 dimensions, with provider/model/configuration identity recorded. Preserve existing BGE/SigLIP collections independently.

Index a Galaxy into its selected collection only. Switching models requires an explicit reindex operation and cost preview. Equal vector dimensions do not imply compatibility.

Cohere evaluation keys are for development evaluation; hosted production requires approved production access. Bedrock remains an optional adapter with regional, model-access, pricing, and credit-eligibility gates. [Cohere policy](https://cohere.com/pricing)

Use Gemini Flash-Lite for routine generation and explicit Flash profiles for harder work. Preserve Phase 8 consent, manual selection, fallback visibility, quota handling, and actual-provider reporting.

### Context, memory, and caching

Treat these as separate systems:

- **Chat history:** durable messages and branches.
- **Context summaries:** bounded summaries of older conversation turns.
- **Frog Memory:** automatically extracted notebook-specific information.
- **Caches:** disposable acceleration data.

Automatic memory updates run as bounded child jobs of permitted chat runs. They must respect provider consent, cancellation, and usage limits. Memory never becomes a substitute for source evidence.

Provide review, edit, disable, delete, and "why remembered" controls. Account-wide preferences remain separate from notebook knowledge.

When a chat is deleted, retain its learned memories according to the chosen policy, but delete the conversation and its rolling context. Clearly offer "Also forget learned memories." Mark retained provenance when its originating chat no longer exists.

Cache keys include owner, notebook, source versions, context/memory versions, provider/model, prompt version, and output schema. Start with exact caching; defer semantic answer caching.

Keep caches tenant-scoped. Source changes, deletion, profile changes, and permission changes invalidate relevant reuse.

### Web research

Use **Tavily for retrieval and Gemini for synthesis**.

- Default to one basic search and five results.
- Deeper searches and page extraction require an explicit action.
- Do not send private files, memory, or full chat history to the search provider.
- Preserve the public search query, retrieval time, supporting excerpts, and citations.
- Distinguish snippet-backed evidence from extracted-page evidence.
- "Save as source" creates a versioned web source; results are not automatically added to the notebook.
- Avoid shared raw-result caches across customers without the provider's confirmation.

As verified during planning on 2026-11-18, Tavily documents 1,000 free monthly credits and one credit per basic search. Its storage guidance permits retained results while preserving publisher-rights obligations. Reverify pricing and terms before implementation. [Pricing](https://docs.tavily.com/documentation/api-credits), [storage guidance](https://help.tavily.com/articles/1612400809-storing-caching-retaining-output)

### Accounts, sharing, and public interfaces

Use Supabase Auth with Google sign-in and email codes. FastAPI verifies identity and ownership; Next.js handles presentation and session forwarding, not application database access.

Begin with invite-only creators. Read-only sharing supports:

- Named, signed-in viewers.
- Revocable unlisted links.

Publish selected immutable snapshots. Exclude private memory, hidden chats, credentials, and diagnostic traces. Sharing does not grant permission to invoke models or access unrelated source files.

Add FastAPI resources for notebooks, source uploads/memberships, chat groups, chats/messages, runs/events, artifacts/revisions, memory, and shares. Generate frontend types from OpenAPI.

Every generation request uses an idempotency key and captures source versions, branch/context identity, provider profile, and consent.

Persist ordered events for progress, validated blocks, artifacts, usage, completion, failure, and cancellation. Serve progressive updates through authenticated fetch streaming, with event replay and polling recovery. Reconnection must not restart generation or duplicate charges.

## 4. Delivery and cloud operation

Deliver focused, atomic packets in this order:

1. **Proposal and baseline:** save this document, reconcile the completed Phase 8 baseline, apply the required local skills, compare layout concepts, and record the reasoned design direction and approved layout/component/typography specifications, including Doto's role.
2. **Ownership foundation:** authentication, notebook ownership, source authorization, private storage, and explicit legacy-data migration.
3. **Workspace shell:** implement the selected design direction across both layouts, responsive navigation, themes, tabs, empty/error states, keyboard behavior, and the chosen typography system including visible Doto usage.
4. **Mixed-source pipeline:** document imports, stable citations, cloud embedding profiles, and indexing coverage.
5. **Persistent chats:** groups, branches, drafts, durable runs, validated blocks, replay, and context summaries.
6. **Frog Memory:** automatic extraction, retention controls, provenance, and deletion behavior.
7. **Frog Lab:** editable documents, study tools, reports, mind maps, versions, and exports.
8. **Gallery and web research:** source visuals, pins, Tavily retrieval, and saved web references.
9. **Sharing and hosted beta:** published snapshots, revocation, usage limits, deployment checks, and companion polish.
10. **Public release gate:** measured capacity, abuse prevention, email delivery, accessibility, and recovery checks.

Use one codebase with separate API and worker entrypoints.

**Cloud roles**

- Vercel: Next.js frontend, within applicable plan conditions.
- Supabase: authentication, PostgreSQL/pgvector, and private application storage.
- Cloud Run: primary FastAPI service and hosted worker services.
- Cloud Tasks: authenticated work dispatch; PostgreSQL remains authoritative for jobs and leases.
- AWS: optional bounded Fargate tasks, temporary S3 assets, minimal image/log storage, and optional Bedrock access.
- Existing local services: development and explicitly selected local profiles.

The hosted core must work while the developer's PC is off. Accept source operations only when a compatible hosted capability is ready; retain explicit ASR/OCR limits.

Set the initial **AWS application allowance to $15 per month from eligible credits**. Reserve estimated cost before dispatch, enforce task deadlines and concurrency, and account for ancillary storage/log/network costs. Disable new AWS work before the configured credit expiry. Keep the core app independent of AWS bursts.

AWS allowance enforcement is an application guard, not a guaranteed billing cutoff. Verify the actual remaining credit, eligible services, account plan, and exact February expiry before enabling AWS usage. [Credit application](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/useconsolidatedbilling-credits.html)

Free-first means no automatic paid upgrades. Pause work when configured allowances are exhausted. Verify email delivery configuration before enabling email-code sign-in; Supabase's built-in email allowance is insufficient for general public onboarding. [Auth email limits](https://supabase.com/docs/guides/auth/rate-limits)

## 5. Acceptance, migration, and handoff

The implementation is complete only when these scenarios pass:

- Import each supported source type, including scanned, malformed, oversized, duplicate, and partially indexed inputs.
- Answer across selected sources with correct video, page, block, and web references.
- Reject incompatible embedding collections and preserve provenance during explicit reindexing.
- Refresh, disconnect, reconnect, stop, retry, and reopen runs without duplicate dispatch.
- Switch layouts while editing, viewing evidence, or generating without losing state.
- Exercise every copy, share, download, tab, group, source-selection, and prompt-setting control.
- Verify rich document autosave, conflict recovery, regeneration, and DOCX/Markdown/JSON/print exports.
- Confirm automatic memory scope, consent, retained-memory deletion notices, and manual forgetting.
- Prevent cross-user access through source IDs, cache keys, run events, exports, previews, and share links.
- Confirm immediate share-grant revocation on subsequent requests.
- Exercise exhausted quota, expired credentials, unavailable providers, and unknown cloud outcomes.
- Verify keyboard-only use, screen-reader flows, reduced motion, contrast, mobile layouts, and zoom/reflow.
- Verify the implemented UI against the documented design reasoning and browser screenshots: coherent hierarchy, product-specific layouts, readable typography, visibly loaded Doto in its assigned role, and no generic template or AI-slop styling.
- Demonstrate hosted processing while the PC is off and normal operation with AWS bursts disabled.

Maintain generated-contract freshness, frontend lint/type/build checks, behavior-focused tests, PostgreSQL integration tests, and the repository's required backend coverage gate. Separate fake-provider results from live hosted acceptance.

Legacy records require an explicit ownership migration. Never assign existing private data automatically to the first user who signs up. Preserve existing source identifiers and citations.

Use the user's globally configured Git identity for commits on this laptop. Per-command
author and committer overrides are unnecessary.

Keep commits focused and preserve the installed skills/MCP configuration. Do not access Notion. Phase 8 documentation timing remains unchanged.

**Active checkpoint:** W1.3 adds private storage authorization and explicit legacy video ownership before the workspace shell in W2. The operator migration and notebook-scoped API preserve existing citation and frame provenance. The optional Supabase preview reader requires live acceptance with a private bucket. See [ADR 0011](adr/0011-private-legacy-source-reads.md) and track the gates in [PLANS.md](../PLANS.md#active-program--workspace-redesign).

Supabase plugin use requires the user's approval. Read-only organization/project discovery was approved on 2026-11-19; project creation, SQL, migrations, and authentication configuration require approval for the specific proposed action. Installation of a plugin alone does not authorize those changes.

On 2026-11-19, the user separately approved creating `galaxy-frog` in their organization, in Mumbai, only at $0. Supabase quoted $0/month and the project was created successfully. This did not authorize hosted SQL, migrations, authentication-setting changes, or paid upgrades.

On 2026-11-20, the user approved connecting Galaxy Frog using their creator email and sending a test sign-in email, then approved a retry and read-only Auth log inspection. A further fresh-link test after the user paused Raycast previews passed at 16:52:35 IST: real creator identity, private notebook access, anonymous denial, the legacy-resource lock, private-response caching protection, and test-session logout all passed. Three email requests were accepted in total. The project URL/publishable key and creator allowlist are configured in ignored `.env`; verification used the existing localhost application database. No SMTP provider, email hook, or Auth-template change was needed. Further email requests, hosted database SQL, migrations, and authentication-setting/template changes still need approval for their specific scope.
