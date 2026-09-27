"""HTTP behavior tests for the transcript-first public API."""

# pyright: reportPrivateUsage=false

from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.adapters.embeddings.ollama import EmbeddingProviderError
from galaxy_frog.adapters.generation.ollama import GenerationProviderError
from galaxy_frog.api.app import create_app
from galaxy_frog.api.dependencies import get_video_repository
from galaxy_frog.api.retrieval import get_retrieval_traces, get_search_service
from galaxy_frog.api.routes import videos as video_routes
from galaxy_frog.application.retrieval.search import SearchTranscript
from galaxy_frog.application.retrieval.temporal import RetrieveTemporalEvidence
from galaxy_frog.application.retrieval.text import RetrieveTranscript
from galaxy_frog.application.videos.import_video import ImportVideo
from galaxy_frog.db.transcript_search import PgVectorTranscriptSearch, TranscriptIndexError
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.generation.models import (
    AnswerConfidence,
    GenerationCitation,
    GenerationDraft,
)
from galaxy_frog.domain.media import AudioFallbackReason
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec, RetrievedEvidence
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.retrieval.traces import RetrievalTrace, TraceLimitExceeded
from galaxy_frog.domain.transcription import (
    TranscriptionCheckpoint,
    TranscriptionComputeType,
    TranscriptionCue,
    TranscriptionDevice,
    TranscriptionProviderSpec,
    TranscriptionResult,
)
from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue, TranscriptOrigin
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    CaptionTrack,
    SafeVideoMetadata,
    SourceCaptionCue,
    SourceReference,
    VideoSourceKind,
)
from galaxy_frog.domain.videos.records import TranscriptRecord, VideoRecord


class MemoryRepository:
    record: VideoRecord | None = None
    transcript: TranscriptRecord | None = None

    async def find_by_source(self, reference: SourceReference) -> VideoRecord | None:
        return self.record if self.record and self.record.metadata.reference == reference else None

    async def save_import(
        self,
        metadata: SafeVideoMetadata,
        cues: tuple[TranscriptCue, ...],
        units: tuple[RetrievalUnit, ...],
        *,
        transcription_run_id: UUID | None = None,
    ) -> VideoRecord:
        del transcription_run_id
        self.record = VideoRecord(uuid4(), metadata)
        self.transcript = TranscriptRecord(self.record, cues, units)
        return self.record

    async def get_video(self, video_id: UUID) -> VideoRecord | None:
        return self.record if self.record and self.record.video_id == video_id else None

    async def get_transcript(self, video_id: UUID) -> TranscriptRecord | None:
        return self.transcript if self.record and self.record.video_id == video_id else None


class FixtureSource:
    source_kind = VideoSourceKind.YOUTUBE

    def canonicalize(self, locator: str) -> SourceReference:
        assert locator == "https://youtu.be/dQw4w9WgXcQ"
        return SourceReference(
            self.source_kind,
            "dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

    async def fetch_metadata(self, reference: SourceReference) -> SafeVideoMetadata:
        return SafeVideoMetadata(reference, "Fixture video", 30_000)

    async def list_caption_tracks(self, reference: SourceReference) -> tuple[CaptionTrack, ...]:
        del reference
        return (CaptionTrack("manual:en", "en", CaptionKind.MANUAL),)

    async def fetch_caption_cues(
        self,
        reference: SourceReference,
        track: CaptionTrack,
    ) -> tuple[SourceCaptionCue, ...]:
        del reference, track
        return (SourceCaptionCue(0, 1000, 5000, "Exact grounded transcript evidence."),)


class MemorySearch:
    def __init__(self, repository: MemoryRepository) -> None:
        self.repository = repository

    async def ensure_indexed(self, video_id: UUID) -> UUID:
        assert await self.repository.get_video(video_id) is not None
        return uuid4()

    async def is_indexed(self, video_id: UUID) -> bool:
        return await self.repository.get_video(video_id) is not None

    async def search(
        self, video_id: UUID, query: str, *, limit: int, window: TimeWindow | None = None
    ) -> tuple[RetrievedEvidence, ...]:
        assert query and limit in (8, 30)
        transcript = await self.repository.get_transcript(video_id)
        if transcript is None or not transcript.units:
            return ()
        return tuple(
            RetrievedEvidence(video_id, unit, 1.0)
            for unit in transcript.units
            if window is None or window.overlaps(unit.start_ms, unit.end_ms)
        )[:limit]


class MemoryTraces:
    def __init__(self) -> None:
        self.records: dict[UUID, RetrievalTrace] = {}

    async def save(self, trace: RetrievalTrace) -> None:
        self.records[trace.trace_id] = trace

    async def get(self, video_id: UUID, trace_id: UUID) -> RetrievalTrace | None:
        trace = self.records.get(trace_id)
        return trace if trace is not None and trace.video_id == video_id else None


class FixtureGeneration:
    async def generate(
        self,
        question: str,
        evidence: tuple[RetrievedEvidence, ...],
    ) -> GenerationDraft:
        assert question
        item = evidence[0]
        return GenerationDraft(
            "Grounded answer.",
            AnswerConfidence.HIGH,
            (GenerationCitation(item.unit.unit_id, "grounded transcript"),),
        )


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, MemoryRepository]:
    application = create_app()
    repository = MemoryRepository()
    search = MemorySearch(repository)

    async def repository_dependency() -> AsyncGenerator[SqlAlchemyVideoRepository]:
        yield cast(SqlAlchemyVideoRepository, repository)

    application.dependency_overrides[get_video_repository] = repository_dependency
    application.state.video_sources = (FixtureSource(),)
    application.state.generation_provider_factory = FixtureGeneration
    traces = MemoryTraces()
    traced_search = SearchTranscript(
        retrieval=RetrieveTemporalEvidence(
            videos=repository, retrieval=RetrieveTranscript(lexical=search, dense=search)
        ),
        traces=traces,
        embedding_spec=EmbeddingCollectionSpec("fixture", "fixture", "1", 2, "l2"),
    )
    application.dependency_overrides[get_search_service] = lambda: traced_search
    application.dependency_overrides[get_retrieval_traces] = lambda: traces

    def transcript_search(
        _request: Request, _repository: SqlAlchemyVideoRepository
    ) -> MemorySearch:
        return search

    monkeypatch.setattr(video_routes, "_transcript_search", transcript_search)
    return application, repository


@pytest.mark.asyncio
async def test_import_read_question_and_reimport_flow(
    api: tuple[FastAPI, MemoryRepository],
) -> None:
    application, repository = api
    imported = await ImportVideo(
        sources=(FixtureSource(),),
        repository=repository,
        transcript_search=MemorySearch(repository),
    ).execute("https://youtu.be/dQw4w9WgXcQ")
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        video_id = imported.video.video_id
        detail = await client.get(f"/v1/videos/{video_id}")
        transcript = await client.get(f"/v1/videos/{video_id}/transcript")
        answer = await client.post(
            f"/v1/videos/{video_id}/questions", json={"question": "What is grounded?"}
        )

    assert detail.json()["index_ready"] is True
    transcript_body = transcript.json()
    assert transcript_body["cues"][0]["start_ms"] == 1000
    assert transcript_body["cues"][0]["origin"] == "caption"
    assert transcript_body["cues"][0]["track_id"] == "manual:en"
    assert transcript_body["transcription"] is None
    assert answer.json()["evidence"][0]["cue_ids"]
    assert answer.json()["evidence"][0]["modality"] == "transcript"


def test_asr_transcript_response_exposes_safe_execution_provenance() -> None:
    source = SourceReference(
        VideoSourceKind.YOUTUBE,
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    provider_cue = TranscriptionCue(
        0,
        0,
        4000,
        "नमस्ते।",
        0.91,
        "mean_word_probability",
    )
    result = TranscriptionResult(
        job_id=uuid4(),
        attempt=1,
        source=source,
        fallback_reason=AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        audio_start_ms=0,
        audio_end_ms=4000,
        spec=TranscriptionProviderSpec(
            "faster-whisper",
            "1.2.1",
            "small",
            "model-revision",
            TranscriptionDevice.CUDA,
            TranscriptionComputeType.INT8_FLOAT16,
        ),
        language_code="hi",
        language_confidence=0.88,
        language_confidence_method="provider_language_probability",
        cues=(provider_cue,),
        processing_seconds=1.25,
        transcribed_at=datetime(2026, 9, 10, 12, tzinfo=UTC),
    )
    run_id = uuid4()
    cue = TranscriptCue.from_transcription(run_id=run_id, result=result, cue=provider_cue)
    unit = RetrievalUnit("e" * 64, 0, 4000, cue.text, (cue.cue_id,))
    video = VideoRecord(uuid4(), SafeVideoMetadata(source, "ASR video", 4000))
    checkpoint = TranscriptionCheckpoint(run_id, uuid4(), result)

    response = video_routes._transcript_response(
        TranscriptRecord(video, (cue,), (unit,), checkpoint),
        index_ready=True,
    )

    assert response.cues[0].origin == "asr"
    assert response.cues[0].caption_kind is None
    assert response.cues[0].confidence == 0.91
    assert response.transcription is not None
    assert response.transcription.model == "small"
    assert response.transcription.device == "cuda"
    assert response.transcription.language_code == "hi"
    assert response.transcription.fallback_reason == "captions_unavailable"
    assert (response.transcription.audio_start_ms, response.transcription.audio_end_ms) == (
        0,
        4000,
    )


@pytest.mark.asyncio
async def test_unknown_video_returns_stable_error(api: tuple[FastAPI, MemoryRepository]) -> None:
    application, _repository = api
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/v1/videos/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "VIDEO_NOT_FOUND"


@pytest.mark.asyncio
async def test_unknown_video_transcript_and_question_return_stable_errors(
    api: tuple[FastAPI, MemoryRepository],
) -> None:
    application, _repository = api
    video_id = uuid4()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        transcript = await client.get(f"/v1/videos/{video_id}/transcript")
        question = await client.post(
            f"/v1/videos/{video_id}/questions", json={"question": "Question?"}
        )

    assert transcript.status_code == 404
    assert question.status_code == 404


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code"),
    [
        (EmbeddingProviderError("embedding"), 503, "EMBEDDING_UNAVAILABLE"),
        (GenerationProviderError("generation"), 503, "GENERATION_UNAVAILABLE"),
        (TranscriptIndexError("citation"), 422, "RETRIEVAL_INTEGRITY_FAILED"),
    ],
)
@pytest.mark.asyncio
async def test_question_provider_failures_are_structured(
    api: tuple[FastAPI, MemoryRepository],
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    application, repository = api
    await ImportVideo(sources=(FixtureSource(),), repository=repository).execute(
        "https://youtu.be/dQw4w9WgXcQ"
    )
    assert repository.record is not None

    if isinstance(failure, GenerationProviderError):

        async def fail_generation(
            self: FixtureGeneration,
            question: str,
            evidence: tuple[RetrievedEvidence, ...],
        ) -> GenerationDraft:
            del self, question, evidence
            raise failure

        monkeypatch.setattr(FixtureGeneration, "generate", fail_generation)
    else:

        async def fail_search(
            self: MemorySearch,
            video_id: UUID,
            query: str,
            *,
            limit: int,
            window: TimeWindow | None = None,
        ) -> tuple[RetrievedEvidence, ...]:
            del self, video_id, query, limit
            raise failure

        monkeypatch.setattr(MemorySearch, "search", fail_search)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/v1/videos/{repository.record.video_id}/questions",
            json={"question": "Question?"},
        )

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code


def test_transcript_search_factory_uses_application_provider() -> None:
    application = FastAPI()
    provider = object()
    application.state.embedding_provider_factory = lambda: provider
    request = Request({"type": "http", "app": application, "headers": []})
    repository = SqlAlchemyVideoRepository(cast(AsyncSession, object()))

    search = video_routes._transcript_search(request, repository)

    assert isinstance(search, PgVectorTranscriptSearch)


async def seed_api(repository: MemoryRepository) -> UUID:
    imported = await ImportVideo(sources=(FixtureSource(),), repository=repository).execute(
        "https://youtu.be/dQw4w9WgXcQ"
    )
    return imported.video.video_id


@pytest.mark.asyncio
async def test_search_never_generates_and_trace_reads_are_video_scoped(
    api: tuple[FastAPI, MemoryRepository],
) -> None:
    application, repository = api
    video_id = await seed_api(repository)

    def forbidden_generator() -> FixtureGeneration:
        raise AssertionError("retrieval-only must not construct a generator")

    application.state.generation_provider_factory = forbidden_generator
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/v1/videos/{video_id}/search", json={"query": "grounded", "mode": "hybrid"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "resolved" and not body["degraded"]
        unit = body["evidence"][0]["units"][0]
        assert unit["start_ms"] == 1000 and unit["end_ms"] == 5000
        assert body["evidence"][0]["cues"][0]["cue_id"] == unit["cue_ids"][0]
        trace_id = body["trace_id"]
        trace = await client.get(f"/v1/videos/{video_id}/retrieval-traces/{trace_id}")
        assert trace.status_code == 200
        payload = trace.json()["payload"]
        assert trace.json()["version"] == 1
        assert payload["retrieval"]["stages"][0]["results"][0]["unit_id"] == unit["unit_id"]
        assert payload["groups"][0]["cues"][0]["origin"] == "caption"
        assert "Exact grounded transcript evidence." not in trace.text
        foreign = await client.get(f"/v1/videos/{uuid4()}/retrieval-traces/{trace_id}")
        absent = await client.get(f"/v1/videos/{video_id}/retrieval-traces/{uuid4()}")
        assert foreign.status_code == absent.status_code == 404
        assert foreign.json()["error"]["code"] == "RETRIEVAL_TRACE_NOT_FOUND"


@pytest.mark.asyncio
async def test_question_anchor_selection_blocks_generation_until_resolved(
    api: tuple[FastAPI, MemoryRepository],
) -> None:
    application, repository = api
    video_id = await seed_api(repository)
    generated = 0

    def generator() -> FixtureGeneration:
        nonlocal generated
        generated += 1
        return FixtureGeneration()

    application.state.generation_provider_factory = generator
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        question = {"question": "What was said during the introduction?", "mode": "hybrid"}
        response = await client.post(f"/v1/videos/{video_id}/questions", json=question)
        body = response.json()
        assert generated == 0
        assert body["retrieval_status"] == "anchor_selection_required"
        assert body["answer"] == "" and body["evidence"] == []
        anchor = body["retrieval"]["anchors"][0]["anchor_id"]
        chosen = await client.post(
            f"/v1/videos/{video_id}/questions", json={**question, "selected_anchor": anchor}
        )
        assert chosen.status_code == 200 and generated == 1
        assert chosen.json()["retrieval_status"] == "resolved"
        assert chosen.json()["evidence"][0]["start_ms"] == 1000
        assert repository.transcript is not None
        repository.transcript = replace(repository.transcript, units=(), cues=())
        missing = await client.post(f"/v1/videos/{video_id}/questions", json=question)
        assert missing.json()["retrieval_status"] == "anchor_unresolved" and generated == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"query": "before the introduction", "selected_anchor": "0" * 64}, 422, "INVALID_ANCHOR"),
        ({"query": "between two events"}, 422, "TEMPORAL_QUERY_UNSUPPORTED"),
        ({"query": "grounded", "allow_fallback": False}, 503, "reranker_not_configured"),
    ],
)
async def test_search_failures_include_durable_safe_traces(
    api: tuple[FastAPI, MemoryRepository], body: dict[str, object], status: int, code: str
) -> None:
    application, repository = api
    video_id = await seed_api(repository)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(f"/v1/videos/{video_id}/search", json=body)
        assert response.status_code == status
        error = response.json()["error"]
        assert error["code"] == code
        trace = await client.get(
            f"/v1/videos/{video_id}/retrieval-traces/{error['details']['trace_id']}"
        )
        assert trace.status_code == 200 and trace.json()["payload"]["failure_code"] == code
        assert trace.json()["payload"]["configuration"]["retrieval"]["rrf_constant"] == 60
        assert trace.json()["payload"]["analysis"]["version"] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (SQLAlchemyError("private credentials"), 503, "DATABASE_UNAVAILABLE"),
        (TraceLimitExceeded("private payload"), 422, "RETRIEVAL_TRACE_LIMIT_EXCEEDED"),
    ],
)
async def test_trace_storage_failure_does_not_claim_durable_success(
    api: tuple[FastAPI, MemoryRepository],
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    status: int,
    code: str,
) -> None:
    application, repository = api
    video_id = await seed_api(repository)

    async def fail_save(self: MemoryTraces, trace: RetrievalTrace) -> None:
        raise failure

    monkeypatch.setattr(MemoryTraces, "save", fail_save)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/v1/videos/{video_id}/search", json={"query": "grounded", "mode": "lexical"}
        )
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert "private" not in response.text


@pytest.mark.asyncio
async def test_question_uses_reranked_fallback_metadata_and_rejects_outside_cue_quote(
    api: tuple[FastAPI, MemoryRepository],
) -> None:
    application, repository = api
    video_id = await seed_api(repository)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        fallback = await client.post(
            f"/v1/videos/{video_id}/questions", json={"question": "grounded"}
        )
        assert fallback.status_code == 200 and fallback.json()["degraded_mode"]
        assert fallback.json()["retrieval"]["warnings"][0]["code"] == "reranker_not_configured"
        assert repository.transcript is not None
        record = repository.transcript
        second = replace(
            record.cues[0],
            cue_id="f" * 64,
            source_order=1,
            start_ms=10000,
            end_ms=20000,
            text="Later topic",
        )
        combined = replace(
            record.units[0],
            text=record.units[0].text + " Later topic",
            end_ms=20000,
            cue_ids=(record.cues[0].cue_id, second.cue_id),
        )
        repository.transcript = replace(record, cues=(*record.cues, second), units=(combined,))
        outside = await client.post(
            f"/v1/videos/{video_id}/questions",
            json={"question": "explain after 00:05", "mode": "lexical"},
        )
        assert outside.status_code == 422
        assert outside.json()["error"]["code"] == "CITATION_VALIDATION_FAILED"
        assert outside.json()["error"]["details"]["trace_id"]
        empty = await client.post(
            f"/v1/videos/{video_id}/search", json={"query": "before 0 seconds"}
        )
        assert empty.status_code == 200 and empty.json()["evidence"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"query": " "},
        {"query": "x", "limit": 9},
        {"query": "x", "mode": "cloud"},
        {"query": "x", "selected_anchor": "other-video"},
        {"query": "x", "unknown": True},
    ],
)
async def test_search_contract_rejects_invalid_inputs(
    api: tuple[FastAPI, MemoryRepository], body: dict[str, object]
) -> None:
    application, repository = api
    video_id = await seed_api(repository)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(f"/v1/videos/{video_id}/search", json=body)
    assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_search_response_and_trace_keep_asr_run_and_confidence(
    api: tuple[FastAPI, MemoryRepository],
) -> None:
    application, repository = api
    video_id = await seed_api(repository)
    record = repository.transcript
    assert record is not None
    run_id = uuid4()
    cue = replace(
        record.cues[0],
        origin=TranscriptOrigin.ASR,
        track_id=None,
        caption_kind=None,
        transcription_run_id=run_id,
        confidence=0.91,
        confidence_method="mean_word_probability",
    )
    repository.transcript = replace(record, cues=(cue,))
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/v1/videos/{video_id}/search", json={"query": "grounded", "mode": "lexical"}
        )
        assert response.status_code == 200
        assert response.json()["evidence"][0]["cues"][0]["transcription_run_id"] == str(run_id)
        trace_id = response.json()["trace_id"]
        trace = await client.get(f"/v1/videos/{video_id}/retrieval-traces/{trace_id}")
        lineage = trace.json()["payload"]["groups"][0]["cues"][0]
        assert lineage["origin"] == "asr" and lineage["transcription_run_id"] == str(run_id)
        assert lineage["confidence"] == 0.91 and lineage["track_id"] is None
