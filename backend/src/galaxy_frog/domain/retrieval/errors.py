"""Provider-independent retrieval failure contracts."""


class EmbeddingProviderError(RuntimeError):
    """Safe failure raised when the configured embedder cannot produce valid vectors."""


class RetrievalIntegrityError(RuntimeError):
    """A retriever returned evidence outside the requested video or conflicting lineage."""


class TranscriptIndexError(RetrievalIntegrityError):
    """Indexing or search would violate embedding collection compatibility."""


class TemporalResolutionRequired(ValueError):
    """A temporal query must be resolved before unrestricted text retrieval."""
