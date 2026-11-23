# 13. Local answers from selected notebook passages

Accepted 2026-11-22.

W3.5 adds one-shot answers from selections in the notebook keyword search. The client sends
the search terms, question, and up to six selected evidence IDs. FastAPI authenticates the
owner and reloads the current notebook results before resolving those IDs. The local Ollama
model receives up to 1,200 characters per passage with request-local references. Requests
with stale selections or unresolved temporal language fail before generation.

Each generated block must cite a request-local reference and quote a contiguous excerpt
from the text given to the model. FastAPI rejects the whole draft if any citation fails.
It builds the public citations from server-owned source versions, PDF pages, block IDs,
and original video intervals and cue lineage. The check verifies citation provenance and
quote fidelity. Claims still require reading the cited evidence for support.

Reuse the configured loopback Ollama adapter and honor an optional model revision pin. Record
evidence reload, generation, and validation durations. Ollama can also report model load,
prompt evaluation, and output token timings. The browser shows its own end-to-end request
duration. [Ollama generate API](https://docs.ollama.com/api/generate)
