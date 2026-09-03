# Ollama setup for Phase 1

Galaxy Frog Phase 1 uses a user-managed Ollama service for local BGE-M3 embeddings and local Qwen3
4B grounded generation. Codex must not install Ollama, pull model weights, update GPU drivers, or
start machine-level services automatically.

## Windows prerequisites

- Windows 10 22H2 or newer.
- Enough local storage for Ollama and several gigabytes of model weights.
- A current supported GPU driver when GPU acceleration is desired.
- Git Bash for the repository commands in this guide.

Use the official documentation for current platform requirements:

- <https://docs.ollama.com/windows>
- <https://docs.ollama.com/gpu>

## Install and verify Ollama manually

Download and run the official Windows installer, launch Ollama from the Start menu, and open a new
Git Bash terminal so the updated `PATH` is visible. Then verify the CLI and local API:

```bash
ollama --version
curl --fail-with-body http://127.0.0.1:11434/api/tags
```

The Windows tray application owns the local service. Do not run a second `ollama serve` process
while the tray application is already running.

## Pull the exact Phase 1 models

```bash
ollama pull bge-m3
ollama pull qwen3:4b
ollama list
```

The model suffix matters: `qwen3:4b` is the approved Phase 1 generator. Do not silently substitute
another embedding or generation model. Repeating `ollama pull` after an interrupted transfer reuses
completed model layers.

Configure the ignored local `.env` file with the matching server-only values:

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434
EMBEDDING_MODEL=bge-m3
EMBEDDING_MODEL_REVISION=ollama
GENERATION_MODEL=qwen3:4b
```

Never expose provider configuration as a `NEXT_PUBLIC_` value. The browser calls FastAPI through
the thin Next.js proxy; only FastAPI calls Ollama.

## Verify the embedding contract

BGE-M3 must return exactly 1,024 values:

```bash
curl -sS http://127.0.0.1:11434/api/embed \
  -H 'Content-Type: application/json' \
  -d '{"model":"bge-m3","input":"Galaxy Frog readiness check"}' \
  | python -c "import json,sys; print(len(json.load(sys.stdin)['embeddings'][0]))"
```

Expected output:

```text
1024
```

Galaxy Frog records the embedding provider, model, revision, dimension, normalization, and input
fingerprint. Indexed and query embeddings must always use the same collection identity.

## Start the complete local stack

Start Docker Desktop manually and wait for the engine before running database commands:

```bash
docker info
bun run infra:up
bun run db:migrate
bun run dev
```

Open <http://localhost:3000>, import a public captioned YouTube video, ask a question answerable from
its captions, and click a returned citation to verify player seeking.

## Verification commands

Use `--no-cov` for a deliberately targeted test. The full suite owns the repository-wide 100%
coverage gate:

```bash
uv run --directory backend pytest --no-cov tests/unit/providers/test_ollama.py
bun run check
bun run precommit
```

With the application and its dependencies running:

```bash
export PHASE1_VIDEO_URL='https://www.youtube.com/watch?v=APPROVED_VIDEO_ID'
export PHASE1_QUESTION='What specific claim does the speaker make?'
bun run smoke:phase1
```

## Troubleshooting

### Model pull reports `unexpected EOF`

Retry the exact pull command. A completed model does not appear in `/api/tags` until its manifest is
written. If repeated retries fail, restart the Ollama tray application, verify free disk space, test
without a VPN, review proxy settings, and inspect `%LOCALAPPDATA%\Ollama\server.log`. Ollama uses
`HTTPS_PROXY` for outbound model pulls and warns that `HTTP_PROXY` can interrupt local clients.

### `/api/tags` returns `{"models":[]}`

The Ollama service is reachable, but no pull has completed. Finish both model pulls and rerun
`ollama list`.

### Grounded generation returns `503` with both models installed

The current Qwen3 tag supports a separate thinking channel. Galaxy Frog explicitly sends
`"think": false` so schema-constrained JSON is returned in Ollama's `response` field. Confirm that
the running API includes the latest adapter, restart `bun run dev`, and retry the question without
re-importing the video.

### `bun run smoke` selects WSL instead of Git Bash

Run smoke commands in Git Bash, not Command Prompt. The Phase 0 script can also be invoked directly:

```bash
./scripts/smoke-test.sh
```

### Inspect GPU offload

After generating once, inspect the loaded model and NVIDIA driver:

```bash
ollama ps
nvidia-smi
```

Shared GPU memory is system RAM and is slower than dedicated VRAM. Partial CPU/GPU offload is valid
but slower than a model that fits entirely in dedicated VRAM.

## Cloud boundary

Ollama Cloud can proxy supported generation models through the signed-in local service, but it is
not the approved Phase 1 runtime. Phase 1 keeps BGE-M3 and Qwen3 4B local. Moving generation to the
cloud changes privacy, availability, authentication, model-retirement, and verification assumptions
and must be an explicit later decision. Never change the embedding model for an existing collection.
