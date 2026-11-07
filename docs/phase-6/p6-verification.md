# Phase 6 verification

Checked on 2026-11-07 on `feat/explain-this-moment`, based on `55216fc`. P6.1–P6.3 are
implemented. P6.4 remains open for a visual moment played and explained in the embedded player.

## Implementation

- `ab22e64` selects transcript, OCR, and frame evidence from the clipped 15-second window on
  either side of the paused playhead. Original evidence IDs, intervals, and capture times are
  retained.
- `3e96986` adds bounded text generation with exact source-quote validation, selective image
  reasoning, explicit fallback, and a saved trace. Active speech without a visual reference
  uses the text path. A speech gap or an explicit visual reference can use one verified frame.
  The VLM requires stored frame hashes. With no eligible frame or an unavailable VLM, the
  response explains the limitation and retains available evidence.
- `819fd34` exposes `POST /v1/videos/{video_id}/explain-moment` through FastAPI and the existing
  Next.js proxy. The player reads the paused YouTube time when clicked, displays the returned
  window, and seeks to the validated citation timestamp. External frame processing is off for
  requests from the player. The server requires both configuration and request permission before
  external frame processing.

## Local verification

- `bun run check` passed generated contracts, Ruff, strict Pyright, ESLint, TypeScript, 38 web
  tests, ten benchmark checks, and the production build. The backend suite passed 1,153 tests
  with 17 opt-in skips and 100% statement and branch coverage. `bun run precommit` passed.
- Eight real PostgreSQL tests passed across the moment, visual, and lexical suites. The moment
  test exercised the HTTP endpoint and confirmed that a saved trace is scoped to its video.
- **Spoken browser check.** On `wjZofJX0v4M`, the first paused request sent `24798` ms and
  examined `[9798,39798)` ms. Clicking the citation moved the paused player to `27571` ms. The
  second request examined `[12571,42571)` ms. Neither request invoked the VLM. Traces
  `c72590be-d438-4e5b-8eb2-740ff3a4c2ad` and `97449b35-32f8-4e9d-b591-f51434fe4c8c`.
- **Visual provider check.** The live API examined `[16700,46700)` ms at `31700` ms on
  `_TJ61eCWQvQ`. Local `qwen3-vl:2b-instruct` cited frame
  `ebc456b6-bbcb-4b30-ac0f-d91f0291e918` at `26033` ms. The frame contains both phrases in the
  final answer, “They tried” and “TOMORROW IS A MYSTERY.” The original prompt also produced an
  unsupported guess about the setting. The revised prompt omitted that guess on the retest.
  Final trace `26b02a96-eb87-4edf-8783-cbef0c17ff49`.
- **Visual browser check.** The indexed fixture returned “This video is unavailable” inside the
  YouTube embed. Complete the remaining gate with a playable indexed video by pausing on a
  visual moment, requesting an explanation, and checking that the frame citation seeks to its
  stored capture time. Local API verification covers the VLM and frame citation for the
  unavailable fixture.

## Hosted status

The work is on the feature branch. Hosted CI and merge are pending. The repository CI workflow
runs on pull requests and pushes to `main`.
