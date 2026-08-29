# Galaxy Frog agent instructions

These instructions apply to the entire repository.

## Product invariant

Galaxy Frog is a temporal multimodal retrieval product, not a generic video chatbot. Every later retrieval and answer path must preserve evidence provenance and time intervals.

## Technology boundaries

- Browser and UI: Next.js, React, TypeScript, Tailwind CSS, and shadcn/ui with Base UI primitives.
- Server and AI: Python, FastAPI, Pydantic, and later LangGraph only where stateful orchestration is justified.
- Next.js must never call an LLM, embedder, reranker, parser, OCR engine, VLM, or the database directly.
- FastAPI is the source of truth for HTTP schemas. Frontend API types must be generated from OpenAPI, not handwritten in parallel.
- Do not add AWS dependencies during local foundation work. Cloud providers must remain optional adapters.
- No secrets in source control. Add examples to `.env.example`; use local environment files for values.

## Phase discipline

Implement only the active checkpoint in `PLANS.md`. For Phase 0, do not implement:

- YouTube ingestion or `yt-dlp`;
- FFmpeg, frame sampling, or audio extraction;
- Whisper or any ASR pipeline;
- embeddings, vector retrieval, pgvector indexes, reranking, or RRF;
- OCR, VLM, LLM, or RAG execution;
- chapters, notes, flashcards, or quizzes.

## Working method

1. Inspect existing code, docs, and git status.
2. State the active checkpoint and affected files.
3. Implement the smallest coherent work packet.
4. Run the relevant lint, type, test, and build checks.
5. Verify behavior, not only command exit codes.
6. Update `PLANS.md`, ADRs when decisions change, and the matching Notion checkpoint.

## Engineering rules

- Prefer a modular monolith until measurements justify distributed services.
- Keep domain code independent of web frameworks and provider SDKs.
- Use typed configuration and dependency injection rather than module-level clients.
- Use structured, stable error codes at the API boundary.
- Make fallbacks explicit and observable; never silently change provider or answer quality.
- Preserve user changes and avoid unrelated refactors.
- Add or update tests with behavior changes.

## Definition of done

A work packet is complete only when its acceptance checks pass, documentation is synchronized, and no later-phase capability has leaked into the checkpoint.
