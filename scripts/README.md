# Repository scripts

Deterministic cross-workspace automation belongs here, including OpenAPI export, generated-client
verification, and local smoke checks.

`smoke-test.sh` verifies the running browser-to-FastAPI path, PostgreSQL-backed readiness, and the
structured deliberate-error response. Start the full local stack before running it with
`bun run smoke` from Git Bash.

Scripts must not become an alternative application layer.
