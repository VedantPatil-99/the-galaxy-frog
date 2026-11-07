# Phase 7 verification

Local acceptance passed on 2026-11-08 on `feat/product-intelligence`, based on the Phase 6
merge `182233a`. Migration `20261107_0013` was applied to the local PostgreSQL database.

## Implementation

- `260fd1c` adds durable runs and immutable, video-scoped versions. `ad0d02b` adds the worker,
  chaptering, and evidence-validated generation. `9a777da` adds the HTTP contracts, Study workspace,
  and exports. `38a89fe` asks for one cited fact per note and records the revised prompt version.
- PostgreSQL acceptance covered concurrent and duplicate requests, recovery after an expired
  lease, rejection of a stale worker, cross-video isolation, preservation of the last saved
  version after a failed regeneration, and exports of historical versions.
- The OCR-backed PostgreSQL fixture used persisted scene and OCR rows and a controlled model
  response. It retained the original interval and a `1234` ms frame capture for seeking.
  OCR with an unknown capture time was excluded. The fixture did not call an OCR provider.

## Local quality and live acceptance

- `bun run check` passed 1,180 backend tests at 100% statement and branch coverage, 39 web tests,
  ten benchmark checks, generated contracts, lint, types, and the production build.
  `bun run precommit` passed. Ten targeted PostgreSQL tests passed across Phase 7, moment,
  lexical, and visual retrieval.
- On the imported Transformers lecture (`wjZofJX0v4M`), local `qwen3:4b` saved five contiguous
  chapters covering `[0, 1634000)` ms, plus a summary, notes, flashcards, and a quiz. The browser
  reopened the lecture after refresh and recovered progress at 0 of 5 chapters. The quiz marked
  the selected answer correct and displayed its supporting caption at 12:37. A summary item and
  a note about what GPT stands for were checked against their saved caption.
- On the short clip (`jNQXAC9IVRw`), revealing a flashcard displayed its answer and a caption
  starting at `7974` ms. Clicking the citation started playback in the cited passage.
  Clicking the chapter marker returned the actual player to 0 seconds. The revised note on
  the location of the elephants was checked against its source. The short quiz produced no
  supported item and failed visibly. The lecture supplied the successful quiz case.
- Notes v1 and v2 were downloaded through the running Next.js proxy as Markdown and JSON.
  Their version numbers, attachment names, timestamp links, and source IDs were inspected.
  The automated browser download event was unavailable, so the file inspection used HTTP
  downloads from the same proxy URLs.

The first lecture attempt reached the 60-second local model deadline. The release uses a
1,400-character source budget and compact prompt labels to bound the call. Saved citations
carry original IDs, and the workspace reports sampled evidence. This is a smaller input
budget than the initial 12,000-character ceiling in the implementation plan.

Hosted CI and merge remain pending. The repository workflow runs on pull requests and pushes to
`main`.
