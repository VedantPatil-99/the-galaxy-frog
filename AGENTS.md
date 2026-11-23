# Galaxy Frog agent instructions

These instructions apply to the entire repository.

## Product invariant

Galaxy Frog is a temporal multimodal retrieval product, not a generic video chatbot. Every later retrieval and answer path must preserve evidence provenance and time intervals.

## Architecture and Technology Boundaries

- **Browser and UI:** Next.js, React, TypeScript, Tailwind CSS, and shadcn/ui with Base UI primitives.
- **Server and AI:** Python, FastAPI, Pydantic, and later LangGraph only where stateful orchestration is justified.
- Next.js must never call an LLM, embedder, reranker, parser, OCR engine, VLM, or the database directly. Next.js is presentation-only and calls FastAPI through the existing proxy.
- FastAPI/Python owns retrieval, ranking, providers, persistence, and HTTP schemas.
- FastAPI is the source of truth for HTTP schemas. Frontend API types must be generated from OpenAPI; never hand-maintain a parallel schema.
- Do not add AWS dependencies during local foundation work. Cloud providers must remain optional adapters.
- No secrets in source control. Add examples to `.env.example`; use local environment files for values.

## Domain Invariants

- Preserve half-open millisecond intervals, original unit/cue IDs, caption/ASR source lineage, and citation validation throughout retrieval and display.
- An unresolved temporal event must never become an unrestricted search. Semantic suggestions require explicit user selection, including a single suggestion.
- Retrieval-only search must not invoke question generation.

## Phase Discipline & Future Goals

- Implement only the active checkpoint in `PLANS.md`. Do not implement later phases or refactor completed work unnecessarily.
- Phase 7 may read existing transcripts, embeddings, scenes, and OCR to build chapters and evidence-backed study artifacts. Separate slide/PDF association is deferred.
- Non-goals for Phase 7: ingestion redesign, new OCR/frame/scene/embedding/VLM processing, LangGraph, cloud deployment, provider-selection UI, and the later evaluation platform.

## Workspace redesign skills

Once installed, use Impeccable for workspace UI/UX reasoning, critique, and design quality. Use the applicable installed Matt Pocock skills for software engineering. Follow the repository's architecture, evidence, and verification requirements.

Read the relevant installed skill instructions and supporting references for each workspace packet. Use the [Gemini API skill](.agents/skills/gemini-api-dev/SKILL.md) for Gemini integration in Python/FastAPI. Preserve the installed skill and MCP configuration during workspace implementation.

## Agent skills

### Issue tracker

For engineering tickets and specs, read [Issue tracker](docs/agents/issue-tracker.md).

### Triage labels

Before triaging issues, read [Triage labels](docs/agents/triage-labels.md).

### Domain docs

For domain terminology and architecture decisions, read
[Domain docs](docs/agents/domain.md). Galaxy Frog uses a single domain context.

## Working Method & Delivery Rules

1. Inspect existing code, docs, and git status.
2. State the active checkpoint and affected files.
3. Implement the smallest coherent work packet.
4. Run the relevant lint, type, test, and build checks.
5. Verify behavior, not only command exit codes.
6. Record verification in `PLANS.md`, the workspace measurements, and the relevant GitHub issue. Update ADRs when decisions change. Update Notion only when explicitly requested.
7. Make small, atomic Conventional Commits and push completed work regularly. Do not create PRs. Provide ready-to-copy PR base, title, and description directly in chat when appropriate. Distinguish local completion from hosted CI and merge status.
8. Use Git Bash syntax in commands given to the user.
9. Ask a short questionnaire when a genuine product or implementation decision is unclear, but continue independent work while waiting for an answer.
10. Record relevant measurements as each feature is implemented or tested in [Workspace measurements](docs/WORKSPACE_MEASUREMENTS.md). Include the workload and input sizes or media duration, environment, sample count, and measurement boundaries. Split latency by observable stage and record throughput, accuracy, and other useful metrics when the test supports them. Label unknowns and simulated results. Add entries incrementally and keep private inputs out of the log.

Show measured user-facing durations where they help people understand a wait or result, using a quiet, readable badge. Use Doto where it remains legible at the intended size, or the workspace reading typeface for small numbers.

## Engineering Rules

- Prefer a modular monolith until measurements justify distributed services.
- Keep domain code independent of web frameworks and provider SDKs.
- Use typed configuration and dependency injection rather than module-level clients.
- Use structured, stable error codes at the API boundary.
- Make fallbacks explicit and observable in warnings, degraded status, and saved traces; never silently change provider, CPU/model/cloud substitute, or answer quality.
- Preserve user changes and avoid unrelated refactors.
- Add or update tests with behavior changes.
- Do not install machine-level tools/services, download model weights, change drivers/security policy, downgrade Python, or make system configuration changes. If a required command cannot run, say so and provide exact Git Bash manual steps.

## Definition of Done

A work packet is complete only when its acceptance checks pass, documentation is synchronized, and no later-phase capability has leaked into the checkpoint.
