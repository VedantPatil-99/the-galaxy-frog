# Repository scripts

Deterministic cross-workspace automation belongs here, including OpenAPI export, generated-client
verification, and local smoke checks.

`smoke-test.sh` verifies the running browser-to-FastAPI path, PostgreSQL-backed readiness, and the
structured deliberate-error response. Start the full local stack before running it with
`bun run smoke` from Git Bash.

`phase-1-smoke.ts` exercises the complete transcript-first HTTP slice through the Next.js proxy.
Set `PHASE1_VIDEO_URL` and `PHASE1_QUESTION` to a user-approved captioned video, then run
`bun run smoke:phase1` from Git Bash. It verifies transcript availability, timestamp evidence, and
duplicate-free re-import; it never downloads media itself.

Do not run `bun run smoke` from Command Prompt on Windows: its `bash` resolution can select the WSL
launcher. Use Git Bash, or invoke `./scripts/smoke-test.sh` directly.

Scripts must not become an alternative application layer.
