# P3.6 evidence UI verification

Branch `feat/retrieval-evidence-ui`, based on P3.5 `45e41d7`. Recorded 2026-09-28.
Implementation and automated gates pass. Manual browser acceptance and the P3.7 exit gate remain
open. No PR is created automatically; hosted CI and merge are separate gates.

## Implemented behavior

- Retrieval-only requests use search without question generation. The UI consumes generated FastAPI
  types and the existing proxy; it contains no retrieval/provider/database logic.
- Evidence shows merged display windows separately from original cue/unit intervals, caption/ASR
  provenance, stage/fusion/reranker scores, warnings, degraded status, and the saved trace.
- Semantic event suggestions require a selection. The selected ID is sent back to FastAPI for
  validation. Missing events show an unresolved state rather than unrestricted results.
- Cue, group, and anchor preview controls reuse the existing player-seek commands. Editing a query,
  switching mode, or importing another video clears stale evidence. Pending requests disable inputs.
- Failed requests display their saved trace ID when the backend supplies one.

## Verified results and limitations

- `bun run check` passed: generated contracts, lint/format/types, 33 frontend tests, 664 backend
  tests with 14 opt-in skips, 100% statement/branch coverage, and the production build.
- Pre-commit and `git diff --check` passed. Three Windows CLI shims failed to launch; their package
  scripts now invoke the installed JavaScript entrypoints through Bun. No installation or security
  policy change was needed.
- Live lexical search and video-scoped trace reads passed through Next.js on the existing attention
  transcript (`a982b566-e047-4a2e-a4c4-8d93ad84f1c0`).
- Real hybrid search for `what happened before the introduction` returned five semantic choices and
  required selection. Selecting the second choice resolved to `[0, 42983)` milliseconds.
- `attention before 00:30` completed CUDA reranking without fallback. Its trace recorded 101.2 seconds
  reranker elapsed time, 25.3 seconds model loading, and 1116 MiB peak reserved memory. Original
  boundary-crossing evidence produced the explicit `temporal_boundary_overlap` warning.
- Two broad strict reranker requests reached the 120-second deadline and returned persisted failure
  traces. Standalone 30-candidate probes passed in 47.7 and 50.8 seconds. This does not establish
  reliable full-mode application latency; P3.7 must investigate and report it without hiding failures.
- A subsequent broad 30-candidate request completed without fallback in 44.6 seconds (44.1 seconds
  reranker elapsed, 22.9 seconds model loading, 1116 MiB peak reserved memory). Its trace is
  `5d3a8ea3-6a92-4c2c-88b7-fdeb4b84e254`. The earlier timeouts remain part of the record; the cause
  of startup-latency variation is not established.
- Browser automation failed before opening a tab with `helper_unknown_error: setup refresh had
  errors`. Visual layout, clicks, and actual YouTube player seeking have not been verified in this
  packet. HTTP and rendered-component tests do not substitute for those checks.

## Manual browser procedure

Keep PostgreSQL, Ollama with existing BGE-M3, FastAPI, and Next.js running. The cached completed
video below does not need a worker or re-ingestion. Full-mode checks additionally require the
provisioned CUDA environment; question generation uses the existing Qwen3 model.

If servers are not already running, use separate Git Bash terminals:

```bash
cd '/e/Projects/Galaxy Frog Root/galaxy-frog'
export RERANKER_PYTHON='E:/Projects/Galaxy Frog Root/galaxy-frog/tmp/reranker-probe/.venv/Scripts/python.exe'
uv run --directory backend python -m uvicorn galaxy_frog.api.app:app --host 127.0.0.1 --port 8000
```

```bash
cd '/e/Projects/Galaxy Frog Root/galaxy-frog'
bun run dev:web
```

1. Open `http://localhost:3000` and import `https://www.youtube.com/watch?v=eMlx5fFNoYc`.
   Confirm the completed job is reused and the transcript appears.
2. Enable **Evidence only**, choose **Exact terms**, and search `attention`. Confirm evidence
   appears without an answer. Expand original intervals/provenance/ranking and inspect the saved trace.
3. Click an original cue timestamp. Confirm the player seeks to that cue and plays. Check an expanded
   group timestamp and an anchor preview separately.
4. Choose **Hybrid** and search `what happened before the introduction`. Confirm choices appear
   before results, choose a moment, and check that the next result has the selected temporal scope.
5. With **Exact terms**, search `what happened before the zzzmissingevent`. Confirm an unresolved
   message with no unrestricted evidence. Search `attention before 00:00` and confirm an empty range.
6. Choose **Hybrid + reranking** and search `attention before 00:30`. Allow up to the configured
   provider deadline. Inspect recorded GPU/provider/ranking metadata if successful; any fallback
   must display a warning and degraded status. Record failures and their trace IDs rather than retrying
   until a passing result can be reported alone.
7. Disable **Evidence only**, ask a transcript-grounded question, and confirm answer citations seek
   correctly. Narrow the window and check that the layout remains usable.

Report which steps passed and any error/trace ID. Do not change Windows security settings to make
the agent's browser sandbox work. Exports must be repeated when opening a new terminal.
