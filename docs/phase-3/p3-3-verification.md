# P3.3 local reranker verification

Local acceptance on `feat/local-transcript-reranking`, based on P3.2 `bf6c1d9`.
Hosted CI and merge remain separate pending gates; no PR was created automatically.

## Provisioned environment

The user provisioned an isolated Python 3.14.7 environment and downloaded the model. No driver,
machine Python, security policy, or model weights were installed/changed by the agent.

- torch `2.11.0+cu128`, Transformers `5.17.0`, CUDA `12.8`.
- Hugging Face Hub `1.30.0`, tokenizers `0.23.2`, safetensors `0.8.0`.
- NVIDIA RTX 2050, 4096 MiB VRAM, driver `572.61`.
- Model `BAAI/bge-reranker-v2-m3`, revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`.
- The user's initial two-pair probe produced scores `3.375` and `-11.03125`, inference times
  `1397.0` and `82.0` ms, and `1114` MiB peak reserved memory.

The optional `reranking` extra pins the verified runtime versions and CUDA wheel source. Default
backend/CI installations do not install it. Production use requires manual provisioning and cached
weights; an explicit alternate interpreter is supported through `RERANKER_PYTHON`.

## Actual provider probe

The implemented adapter, including child creation, JSON transport, validation, and cleanup, passed
the full 30-candidate offline probe:

| Measurement | Observed |
|---|---:|
| Candidate count | 30 |
| Long-passage original/input tokens | 4811 / 512 |
| Relevant/irrelevant logits | 3.375 / -11.03125 |
| Model loading | 22,934 ms |
| Processing including model loading | 26,709 ms |
| Total parent-observed time | 44,198 ms |
| Peak allocated VRAM | 1103.17 MiB |
| Peak reserved VRAM | 1118 MiB |
| GPU usage checked after process exit | 0 MiB |

Each request owns a separate model process, so model startup is repeated. These figures establish
bounded execution and expose its latency cost, not a performance improvement. Two synthetic pairs
and repeated multilingual passages do not measure retrieval quality. Ollama coexistence, the frozen
benchmark, and complete application latency remain exit-gate checks.

## Automated gates

- 86 focused provider/retrieval/process tests passed.
- Two real CUDA integration checks passed: maximum candidate budget and actual deadline termination.
- `bun run check` passed: generated contracts, lint, format, types, 591 backend tests, 100%
  statement/branch coverage, 26 frontend tests, and production build. The default suite skips 12
  opt-in tests; native reranker tests ran separately. Database integrations were not repeated because
  this packet changes no SQL or persistence behavior.
- Pre-commit and lock consistency checks passed.

Tests cover offline settings, pinned identities, truncation metadata, bounds, concurrency rejection,
cancellation cleanup, process errors/timeouts, unavailable dependencies/CUDA/model, OOM, invalid
output, finite scores, evidence identity, fused fallback, and strict no-fallback execution.

## Reproduce from Git Bash

No PostgreSQL, API, web, worker, or Ollama service is needed for the isolated reranker check. Close
Ollama and GPU-heavy apps first. Use the existing provisioned environment; do not recreate it.

```bash
cd '/e/Projects/Galaxy Frog Root/galaxy-frog'
export RERANKER_PYTHON='tmp/reranker-probe/.venv/Scripts/python.exe'
uv run --directory backend python -m galaxy_frog.entrypoints.reranker_probe --full
RUN_RERANKER_INTEGRATION=1 uv run --directory backend python -m pytest --no-cov \
  tests/integration/test_reranker_provider.py
bun run check
bun run precommit
```

Paths in `RERANKER_PYTHON` resolve relative to the repository root. If absent, the provider uses
the backend interpreter; that environment must have the manually installed optional dependencies.
`RERANKER_TIMEOUT_SECONDS` defaults to 120 and is bounded above by 600. Model identity, CUDA FP16,
512-token inputs, batch one, and concurrency one are fixed by this packet. No automatic CPU/model/
cloud substitution or model download occurs. Failures surface as stable reranker reason codes.
