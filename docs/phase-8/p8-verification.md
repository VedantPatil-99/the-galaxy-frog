# Phase 8 local verification

Date: 2026-11-19 (Asia/Calcutta)

Branch: `feat/provider-resilience`. Local implementation and exit gate are complete. This is the
final focused Markdown synchronization after the gate. No Phase 8 Notion access or per-packet
Markdown reports were used. No PR was created; hosted CI and merge were not verified here.

## Delivered behavior

- FastAPI catalog, versioned profiles, local/cloud/manual modes, safe readiness and pricing sources.
- Explicit consent, revocation, dispatch records, cross-process admission, deadlines, circuits,
  compatible collection checks and durable requested-versus-actual provider trails.
- Gemini structured answer and Study adapters, independent task credentials, conservative project
  budgets and shared cooldowns, no hidden retry/continuation/model substitution, safe errors.
- Study selections survive queueing and request replay. Current consent and lease fence each cloud
  call. One successful provider remains pinned for the material, with actual prompt/model provenance.
- Settings and Study controls use generated contracts. Backend metadata drives the pre-queue consent
  warning; local fallback does not require a cloud grant. Existing evidence intervals remain intact.

Migrations through `20261118_0017` were applied successfully to the existing PostgreSQL service.
No machine tool, model weight, Python version, driver, CUDA or Windows policy was installed/changed.

## Local gate

- Clean backend run: **1,440 passed, 4 skipped**, **100.00%** statement/branch coverage;
  9,194 statements and 2,242 branches, with `RUN_DATABASE_INTEGRATION=1`.
- Frontend: **50 passed**, no failures.
- Ruff lint and format, strict Pyright and TypeScript, and ESLint passed.
- Canonical OpenAPI and generated TypeScript drift checks passed.
- Next.js 16.3.3 production build passed using `--webpack`.
- Frozen Phase 3 and Phase 5 benchmark integrity checks passed (8 and 3 tests respectively).
- PostgreSQL tests proved atomic shared quota, Pacific DST resets, cooldown recovery, health/budget
  display metadata, source/profile-bound consent, revocation and Study lease fencing.
- SDK-over-fake-HTTP tests proved structured validation, privacy options, one dispatch on provider
  error, no consent-denied dispatch, quota categories and sanitized tracebacks. Fakes establish
  those controlled behaviors; they do not establish hosted acceptance.
- The final local secret scan checked 2,857 text files and 1,296 existing Git blobs. No configured
  credential values or token/private-key patterns were found outside the two protected environment
  files; no files in that scan were unreadable. Dependency/runtime directories were excluded.

The clean full-suite command was equivalent to:

```bash
RUN_DATABASE_INTEGRATION=1 uv run --directory backend python -m pytest
bun run contracts:check
bun run lint
bun run format:check
bun run typecheck
bun run test:web
bun run test:benchmark
bun run --cwd apps/web build --webpack
```

Windows blocked generated virtual-environment launchers in this Codex session. The actual backend
checks used the existing uv-managed CPython 3.14.7 executable, with backend sources, existing project
site-packages and a project-local SDK test target on `PYTHONPATH`. Async/database checks ran outside
the restricted sandbox. Pre-commit's Ruff/format/Pyright constituent checks ran directly; this
record does not claim that the hook-launcher command or a fresh dependency installation passed.
Commits used a command-local empty hooks path only after those checks; global Git settings and
security policy were not changed.

## Browser acceptance

The built app ran on `127.0.0.1:3001` through the Next.js proxy to FastAPI on `127.0.0.1:8001`.
Gemini answer/Study enable/model settings were process-local overrides; `.env` was not rewritten.

- Settings displayed separate generation/Study capabilities, readiness, revision limitations,
  application budgets/usage/reset times and provider pricing links without credentials.
- Selecting `gemini · v1` displayed explicit manual chains for generation, vision and Study.
- An existing video reopened its transcript and Study controls with the default local profile.
- Choosing Gemini Study left cloud sharing unchecked. An initial check exposed late queue-time
  failure; the final implementation instead displayed the consent warning before queueing.
- The final warning read: “This profile requires cloud sharing. Approve the selected evidence text
  or choose a local profile.” No hosted generation was requested by that browser check.
- Only the exact unclaimed queued run created while finding the issue was removed; the existing
  video, transcript, artifacts and other user work were preserved.

## Real Gemini acceptance

Model returned: `gemini-3.5-flash-lite`. Requests used synthetic evidence only:
“In this synthetic test, the frog's badge number is 42.” No source video was accessed or uploaded.

- Initial interactive call: 3.791 s; 295 input / 81 output tokens; citation validated.
- Initial separate Study-project call: 7.100 s; 130 input / 48 output tokens; citation validated.
- Output-capture Study repeat: 1.193 s; 130 input / 36 output tokens. Validated chapter title:
  “The Frog's Badge Number”; interval `[0, 10000)` ms, with the original evidence quote.
- One output-capture answer attempt returned `PROVIDER_OUTCOME_UNKNOWN`. No automatic retry or
  silent fallback occurred. A subsequent explicit targeted check succeeded in 9.064 s with
  295 input / 80 output tokens: “The frog's badge number is 42.” Its quote was validated.
- Successful calls reported zero thinking/cached-input tokens. Actual account charges were not
  supplied and are unknown. These timings are individual checks, not P50/P95 measurements.

The smoke tool defaults to safe credential-presence metadata; `--live` explicitly authorizes its
synthetic hosted call(s), and `--show-output` explicitly enables validated synthetic output.
It is independent of normal app admission/history. See [the runbook](../runbook.md#explicit-hosted-smoke).

## Limits and remaining evidence

Gemini answer/Study output defaults to 4,096 tokens per call, configurable to 256–16,384. The smoke
uses 1,024. Input is bounded by prompt bytes and conservative shared project reservations, not a
tokenizer-based per-request count. Strict cloud Study defaults to 12,000 evidence characters per
chapter; item counts and excerpts remain independently bounded. See the runbook for exact knobs.

Hosted vision, Cloudflare ASR, LlamaParse and Bedrock were not accepted live in this closeout.
Long-video accuracy, detailed Study completeness, multilingual quality, general speed/reliability
and account billing/free-tier eligibility are not established by these small checks or test coverage.
Existing local model acceptance belongs to the earlier phase records. Phase 9 evaluation and the
saved workspace-redesign proposal remain separate, unimplemented follow-up work.
