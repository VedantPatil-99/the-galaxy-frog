Plan and implement Phase 4 — Scene and OCR pipeline — for Galaxy Frog.

Repository: VedantPatil-99/the-galaxy-frog
Local path: E:\Projects\Galaxy Frog Root\galaxy-frog

The Phase 3 baseline has already been verified, documentation updated, and the `feat/scene-and-ocr-pipeline` branch created. Proceed directly to implementing Phase 4.

## First: verify the project state

1. Read AGENTS.md and follow it as the repository’s current instructions.
2. Confirm you are on the `feat/scene-and-ocr-pipeline` branch. Phase 3 is fully merged and its documentation has already been updated.
3. Read:
   - PLANS.md (Active Phase 4 plan)
   - docs/adr/0006-cloud-ocr-llamaparse.md
   - The Phase 4 and adjacent Phase 5 sections of docs/FULL_PLAN.md
   - docs/PROJECT_HANDOFF.md and README.md
   - Relevant sections of docs/architecture.md and docs/runbook.md
   - Existing ingestion jobs/workers, media processing, transcript indexing, database models and migrations, API schemas, generated frontend contracts, evidence display, tests, and CI checks
4. Inspect the current Notion Phase 4 checkpoint, if accessible. Preserve existing pages and history.

## Phase 4 target

The plan describes a scene and OCR pipeline:

- Detect temporal scenes and sample bounded frames at scene boundaries and periodic intervals.
- Compute perceptual hashes (e.g., using ImageHash) and deduplicate near-identical frames to conserve cloud credits.
- Run Cloud OCR using LlamaParse behind a Python provider interface.
- Persist OCR text, boxes, confidence, timestamps, frame and scene relationships, and source provenance.
- Index OCR text for lexical and dense retrieval without mixing it with transcript evidence.
- Route suitable queries to OCR evidence and display relevant frames with highlighted OCR evidence.
- Preserve the existing transcript retrieval and question behavior.

Treat this as a starting scope to verify against the repository plan and actual architecture. Do not assume implementation details or add later-phase work.

## Important boundaries

- FastAPI/Python owns media processing, OCR providers, retrieval, persistence, ranking, and HTTP schemas.
- Next.js is presentation-only and calls FastAPI through the existing proxy. Generate frontend API types from FastAPI OpenAPI.
- Preserve half-open millisecond intervals, original frame/unit/cue IDs, caption/ASR lineage, OCR frame/scene provenance, and citation validation across storage, retrieval, API, and UI.
- Keep provider-specific code behind Python interfaces. Make every fallback explicit and visible in warnings, degraded status, and traces.
- Reuse the existing durable ingestion/job lifecycle where appropriate. Do not redesign completed Phase 2 ingestion or Phase 3 retrieval.
- An unresolved temporal event must never trigger unrestricted search. Any semantic suggestions require explicit user selection, even if there is only one suggestion.
- Retrieval-only search must not call question generation.
- Do not implement Phase 5 visual embeddings, VLMs, visual reasoning, or other later-phase features.
- Do not install machine-level tools or services, change Windows security policy or drivers, downgrade Python, or download models automatically.
- Check current official documentation for Windows, Python 3.14.7, LlamaParse, and any candidate scene-detection and hashing dependencies (e.g., PySceneDetect, ImageHash). Cite the sources and distinguish verified compatibility from assumptions. If a required installation or system change is needed, stop and give me exact Git Bash setup steps.

## Before starting the next packet, report

Briefly provide:

- The exact scope and non-goals of the upcoming implementation packet (e.g., P4.1 & P4.2 Vision Pipeline Foundation).
- Likely affected files and migrations, based on repository inspection.
- Required running services, required API keys (e.g., `LLAMA_CLOUD_API_KEY`), locally provisioned software/models, and manual setup.
- Acceptance checks, risks, and anything requiring a product decision.

Ask a short questionnaire if a genuine product decision is unresolved. Do not write code for the packet until I approve the proposed plan.

## During implementation

Implement Phase 4 as small, consecutive packets. For each packet:

- Make focused, atomic Conventional Commits and push completed work regularly.
- Do not create pull requests. Give me the proposed base branch, PR title, and complete PR description directly in chat when useful.
- Update PLANS.md, relevant ADRs and runbook/architecture documentation, and the matching existing Notion checkpoint without overwriting historical content, you can update the notion docs at last after my approval.
- Run appropriate lint, type, test, coverage, generated-contract, PostgreSQL integration, and build checks. Preserve the repository’s required 100% backend statement and branch coverage.
- Verify behavior as well as command exit codes. Report anything blocked by Windows Application Control or unavailable services and provide exact Git Bash manual steps so I can run it.
- Before each live test, tell me which services must be running.
- Do not claim a check, hosted CI result, merge, or quality improvement unless it is directly verified.

## Phase 4 acceptance criteria to refine during planning

Design tests and a small reproducible labeled dataset that verify:

- OCR-only on-screen information is retrieved near the correct timestamp.
- OCR evidence is shown separately from transcript evidence with frame, scene, box, confidence, and provenance intact.
- Repeated or near-identical slide frames do not flood results.
- Low-confidence OCR is clearly marked or filtered by an explicit policy.
- Timestamp boundaries, scene/frame association, cross-video isolation, duplicate cues/frames, and original evidence IDs remain correct.
- Ingestion is bounded, resumable, idempotent, cancellable, and cleans up temporary media as expected.
- OCR/provider failure is visible and cannot silently become an unrestricted or misleading search.
- Existing caption/ASR ingestion, citations, retrieval, question generation, and timestamp seeking continue to work.
- OCR retrieval is evaluated separately on frozen examples with suitable ranking, interval-accuracy, deduplication, latency, and memory measures. Report measured results; do not claim improvement without evidence.
