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
- Future/Non-goals: ingestion redesign, OCR, frames/scenes, visual embeddings, VLMs, LangGraph, learning artifacts, cloud deployment, provider-selection UI, and the later evaluation platform.

## Working Method & Delivery Rules

1. Inspect existing code, docs, and git status.
2. State the active checkpoint and affected files.
3. Implement the smallest coherent work packet.
4. Run the relevant lint, type, test, and build checks.
5. Verify behavior, not only command exit codes.
6. Update `PLANS.md`, ADRs when decisions change, and the matching Notion checkpoint.
7. Make small, atomic Conventional Commits and push completed work regularly. Do not create PRs. Provide ready-to-copy PR base, title, and description directly in chat when appropriate. Distinguish local completion from hosted CI and merge status.
8. Use Git Bash syntax in commands given to the user.
9. Ask a short questionnaire when a genuine product or implementation decision is unclear, but continue independent work while waiting for an answer.

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
