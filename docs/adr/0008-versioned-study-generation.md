# ADR 0008 — Versioned study generation

**Status:** Accepted for Phase 7

Study generation may require several calls to the local text model. Keeping those calls inside
a browser request would tie execution to the proxy timeout and make refresh and recovery unreliable.

FastAPI queues a PostgreSQL run with a client request ID. The existing Python worker runs a
separate study polling loop with independent sessions. A per-video and per-kind constraint
prevents concurrent generation for the same material. A lease token fences every progress and
terminal update. The worker can reclaim an expired run once. Publication inserts the immutable
version and completes the run in the same transaction.

Study generation reads a consistent snapshot of the video's existing cues, OCR, and chapter
signals. A study material pins a saved chapter version. Model output proposes text and citations.
Only quotes verified against the supplied evidence are stored, along with original lineage,
intervals, and a separate valid seek point. Short prompt labels are resolved back to original
IDs before storage. Summary and notes still require review for semantic support.

The model receives text through the configured loopback Ollama endpoint. Phase 7 exports
render the selected saved version, so later generation and provider availability cannot change
a historical export.
