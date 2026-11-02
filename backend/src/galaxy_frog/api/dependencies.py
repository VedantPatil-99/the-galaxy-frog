"""FastAPI dependencies that expose application-scoped infrastructure."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from galaxy_frog.adapters.vision.reasoning import LocalReasoningFrameVerifier
from galaxy_frog.application.ingestion.dispatch import DispatchSignatureVerifier, JobDispatcher
from galaxy_frog.application.retrieval.reasoning import AdaptiveVisualReasoning
from galaxy_frog.application.retrieval.search import SearchTranscript
from galaxy_frog.application.retrieval.temporal import RetrieveTemporalEvidence
from galaxy_frog.application.retrieval.text import RetrieveTranscript
from galaxy_frog.application.retrieval.visual import AdaptiveVisualRetrieval
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import probe_database
from galaxy_frog.db.ingestion_repository import PostgresIngestionRepository
from galaxy_frog.db.ocr_search import PgVectorOCRSearch, PostgresOCRLexicalSearch
from galaxy_frog.db.reasoning_cache import PostgresVisualReasoningCache
from galaxy_frog.db.retrieval_traces import PostgresRetrievalTraces
from galaxy_frog.db.transcript_lexical_search import PostgresTranscriptLexicalSearch
from galaxy_frog.db.transcript_search import PgVectorTranscriptSearch
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.db.vision_repository import SqlAlchemyVisionRepository
from galaxy_frog.db.visual_search import PgVectorVisualSearch
from galaxy_frog.domain.retrieval.pipeline import RetrievalConfig
from galaxy_frog.domain.retrieval.ports import TextEmbeddingProvider
from galaxy_frog.domain.retrieval.reranking import TextReranker
from galaxy_frog.domain.vision.visual_ports import VisualEmbeddingProvider, VisualReasoningProvider

type DatabaseProbe = Callable[[], Awaitable[None]]


def build_retrieval_service(
    request: Request, repository: SqlAlchemyVideoRepository
) -> SearchTranscript:
    provider = cast(
        Callable[[], TextEmbeddingProvider], request.app.state.embedding_provider_factory
    )()
    retrieval = RetrieveTranscript(
        lexical=PostgresTranscriptLexicalSearch(repository.session),
        dense=PgVectorTranscriptSearch(
            session=repository.session, videos=repository, provider=provider
        ),
        reranker=cast(TextReranker, request.app.state.reranker),
        config=RetrievalConfig(
            fusion_limit=cast(Settings, request.app.state.settings).retrieval_fusion_limit
        ),
    )
    visual_provider = cast(
        VisualEmbeddingProvider | None,
        getattr(request.app.state, "visual_embedding_provider", None),
    )
    return SearchTranscript(
        retrieval=RetrieveTemporalEvidence(videos=repository, retrieval=retrieval),
        traces=PostgresRetrievalTraces(repository.session),
        embedding_spec=provider.spec,
        reasoning=AdaptiveVisualReasoning(
            provider=cast(
                VisualReasoningProvider | None,
                getattr(request.app.state, "visual_reasoning_provider", None),
            ),
            cache=PostgresVisualReasoningCache(repository.session),
            verifier=LocalReasoningFrameVerifier(),
            external_enabled=cast(
                Settings, request.app.state.settings
            ).external_frame_processing_enabled,
        ),
        visual=AdaptiveVisualRetrieval(
            ocr_lexical=PostgresOCRLexicalSearch(repository.session),
            ocr_dense=PgVectorOCRSearch(session=repository.session, provider=provider),
            visual=PgVectorVisualSearch(repository.session, visual_provider)
            if visual_provider
            else None,
            provider=visual_provider.identity if visual_provider else None,
        ),
    )


def get_job_dispatcher(request: Request) -> JobDispatcher:
    """Return the configured provider-independent dispatch adapter."""

    return cast(JobDispatcher, request.app.state.job_dispatcher)


def get_dispatch_signature_verifier(request: Request) -> DispatchSignatureVerifier:
    """Require the configured verifier for an internal authenticated callback."""

    verifier = cast(
        DispatchSignatureVerifier | None,
        getattr(request.app.state, "dispatch_signature_verifier", None),
    )
    if verifier is None:
        from galaxy_frog.api.errors import ApiError

        raise ApiError(
            status_code=503,
            code="QSTASH_NOT_CONFIGURED",
            message="The QStash callback is not configured.",
            retryable=False,
            suggested_action="Configure the QStash dispatcher and signing keys before delivery.",
        )
    return verifier


def _database_engine(request: Request) -> AsyncEngine:
    engine = cast(AsyncEngine | None, getattr(request.app.state, "database_engine", None))
    if engine is None:
        from galaxy_frog.api.errors import ApiError

        raise ApiError(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="The database dependency is not configured.",
            retryable=False,
            suggested_action="Configure DATABASE_URL before importing videos.",
        )
    return engine


def get_database_probe(request: Request) -> DatabaseProbe | None:
    """Return a request-safe database probe without exposing the engine to handlers."""

    engine = cast(AsyncEngine | None, getattr(request.app.state, "database_engine", None))
    if engine is None:
        return None

    async def probe() -> None:
        await probe_database(engine)

    return probe


async def get_video_repository(request: Request) -> AsyncGenerator[SqlAlchemyVideoRepository]:
    """Yield a request-scoped repository without exposing sessions to route handlers."""

    factory = async_sessionmaker(
        _database_engine(request),
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        yield SqlAlchemyVideoRepository(session)


async def get_ingestion_repository(
    request: Request,
) -> AsyncGenerator[PostgresIngestionRepository]:
    """Yield the durable job repository through the same request-scoped session boundary."""

    factory = async_sessionmaker(
        _database_engine(request),
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        yield PostgresIngestionRepository(session)


async def get_vision_repository(
    request: Request,
) -> AsyncGenerator[SqlAlchemyVisionRepository]:
    """Yield a request-scoped vision repository without exposing sessions to route handlers."""

    factory = async_sessionmaker(
        _database_engine(request),
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        yield SqlAlchemyVisionRepository(session)
