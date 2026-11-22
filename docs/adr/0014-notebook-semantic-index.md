# 14. Revision-bound notebook semantic indexing

Accepted 2026-11-22.

W3.6 adds an explicit local BGE-M3 profile using the installed Ollama service. Each notebook
selects one active embedding collection. Its identity includes provider, model, installed
digest, dimension, and normalization. The service checks the digest before and after embedding.
The collection tables also support other vector dimensions for future profiles.

Indexing proceeds in user-initiated batches of at most 16 current passages. The index hashes
source coordinates and text to identify the original evidence. It embeds up to 2,000 characters
of each passage. Searches resolve the full original evidence from the notebook and rank its
vectors by exact cosine distance. Coverage and results omit stale passage identities. The same
projection supplies PDF pages, immutable blocks, and original video intervals and cue lineage
to keyword and semantic search. [pgvector distance operators](https://github.com/pgvector/pgvector#querying)

A rebuild requires explicit selection of the new model revision. The first successful batch
atomically replaces the active collection and its derived vectors. A short notebook lock
and an expected collection ID keep concurrent rebuilds consistent. The provider call runs
outside the database transaction. Failed embedding leaves the prior index available.

Semantic search requires a compatible indexed collection and rejects unresolved temporal
scope before embedding. The user selects a result to open its original evidence. The browser
shows indexed passage counts and measured request waits. Cloud provider consent and cost
previews remain in the following W3 packet. [Ollama embeddings](https://docs.ollama.com/api/embed)
