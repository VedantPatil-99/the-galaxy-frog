# Phase 0 execution plan

Phase 0 is split into four checkpoints so each change set has one clear review target.

| Step | Work packets | Main result | Review gate |
|---|---|---|---|
| 1. Scope, repository, invariants | P0.1 + part of P0.6 | Stable repo and architectural rules | Files, scope, and ADRs reviewed |
| 2. Frontend/backend foundations | P0.2 + P0.3 | Both language workspaces run and pass local quality checks | UI and API start independently |
| 3. Contracts/local infrastructure | P0.4 + part of P0.5 | Browser-to-API path, errors, health, OpenAPI, PostgreSQL | Contract demo works end to end |
| 4. CI and exit gate | rest of P0.5 + P0.6 | Automated proof of repository health | All Phase 0 exit checks pass |

## AI implementation packet format

Give an implementation agent one checkpoint at a time with:

1. Goal and explicit non-goals.
2. Files it may create or edit.
3. Required commands and version constraints.
4. Acceptance tests and expected observable behavior.
5. Documentation updates.
6. A stop condition: report results and wait before starting the next checkpoint.

This keeps agent context small and prevents later-phase features from leaking into foundation work.
