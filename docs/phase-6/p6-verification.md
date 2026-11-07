# Phase 6 verification

Locally complete on 2026-11-07 on `feat/explain-this-moment`, based on `55216fc`.
P6.1–P6.4 passed their local acceptance checks.

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
- `ad0bd99` keeps the existing combined-format preference and lets frame extraction fall back
  to one bounded video-only stream. The same height, duration, byte, and time limits apply.

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
- **Visual browser check.** The `_TJ61eCWQvQ` embed reported that playback was unavailable.
  The replacement, `jNQXAC9IVRw`, played in the embed. Its import saved six caption cues and
  reported zero frames because the source exposed video and audio separately. After the format
  fix, rerunning only the existing vision indexing operation for this video saved one hashed
  frame and indexed it with local CUDA SigLIP. The original ingestion record still reflects
  the initial attempt. OCR was unavailable for this fixture.
- In the browser, the paused player submitted `5020` ms in a caption gap and the response
  showed `[0,19000)` ms. Local `qwen3-vl:2b-instruct` cited frame
  `69a38f5f-feca-4fd5-b614-e282a58b3452` at `0` ms. The click moved the paused player to
  `00:00`. Trace `80bdae33-2ab3-4ecc-b31b-1c9050858fe6` confirms a cache miss and external
  processing disabled. The frame shows a person, elephants, and fencing. The answer's claim
  that the elephants were eating was unconfirmed from the still frame.

## Hosted status

The work is on the feature branch. Hosted CI and merge are pending. The repository CI workflow
runs on pull requests and pushes to `main`.
