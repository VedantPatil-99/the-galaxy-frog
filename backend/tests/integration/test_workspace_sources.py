"""Exercise explicit assignments and private reads through FastAPI and PostgreSQL."""

import asyncio
import json
import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from tests.test_workspace_auth import auth_settings

from galaxy_frog.adapters.embeddings.ollama import OllamaBgeM3EmbeddingProvider
from galaxy_frog.adapters.generation.ollama import OllamaGenerationProvider
from galaxy_frog.api.app import create_app
from galaxy_frog.api.routes import workspace as workspace_routes
from galaxy_frog.api.routes import workspace_semantic
from galaxy_frog.api.workspace_auth import get_workspace_identity_verifier
from galaxy_frog.config import Settings
from galaxy_frog.db.models import VideoFrameRow, VideoRow
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.db.workspace import WorkspaceAccountRow, WorkspaceNotebookRow, WorkspaceRepository
from galaxy_frog.db.workspace_documents import (
    DocumentImport,
    WorkspaceDocumentRepository,
    WorkspaceDocumentRow,
)
from galaxy_frog.db.workspace_page_ocr import WorkspacePageOCRRepository, WorkspacePageOCRRow
from galaxy_frog.db.workspace_semantic import WorkspaceSemanticRepository
from galaxy_frog.db.workspace_sources import (
    LegacySourceAssignmentError,
    WorkspaceLegacySourceRow,
    WorkspaceSourceRepository,
)
from galaxy_frog.db.workspace_text import WorkspaceTextSourceRow
from galaxy_frog.domain.generation.models import GenerationProviderError
from galaxy_frog.domain.generation.workspace_answer import (
    WorkspaceAnswerDraft,
    WorkspaceAnswerPassage,
    WorkspaceGeneratedDraft,
    WorkspaceModelMetrics,
)
from galaxy_frog.domain.retrieval.errors import EmbeddingProviderError
from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    SafeVideoMetadata,
    SourceReference,
    VideoSourceKind,
)
from galaxy_frog.domain.workspace import NotebookDraft, NotebookNotFound, VerifiedWorkspaceIdentity
from galaxy_frog.domain.workspace_documents import ParsedDocument, StoredDocumentSection
from galaxy_frog.domain.workspace_text import prepare_workspace_text
from galaxy_frog.entrypoints.workspace_assign_legacy import assign

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1", reason="requires migrated PostgreSQL"
)

JPEG = b"\xff\xd8\xfffixture\xff\xd9"
OWNER_EMAIL = "owner@example.com"
OTHER_EMAIL = "other@example.com"
OWN = {"authorization": "Bearer owner-token"}
OTHER = {"authorization": "Bearer other-token"}
CUE_ID = "c" * 64
UNIT_ID = "a" * 64


@pytest_asyncio.fixture
async def workspace(
    tmp_path: Path,
) -> AsyncGenerator[tuple[httpx.AsyncClient, AsyncEngine, UUID, UUID, tuple[UUID, ...], UUID]]:
    owner, outsider = uuid4(), uuid4()
    identities = {
        "owner-token": VerifiedWorkspaceIdentity(account_id=owner, email=OWNER_EMAIL),
        "other-token": VerifiedWorkspaceIdentity(account_id=outsider, email=OTHER_EMAIL),
    }
    settings = auth_settings(
        database_url=Settings().database_url,
        workspace_creator_emails=[OWNER_EMAIL, OTHER_EMAIL],
        workspace_preview_storage="local",
        supabase_storage_secret_key=None,
        vision_workspace_root=tmp_path / "vision",
    )
    app = create_app(settings)
    verifier = Mock(verify=AsyncMock(side_effect=identities.__getitem__))
    app.dependency_overrides[get_workspace_identity_verifier] = lambda: verifier
    async with app.router.lifespan_context(app):
        engine: AsyncEngine = app.state.database_engine
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        video_ids: list[UUID] = []
        frame_id = uuid4()
        try:
            async with sessions() as session:
                videos = SqlAlchemyVideoRepository(session)
                for kind in (
                    VideoSourceKind.LOCAL_FILE,
                    VideoSourceKind.YOUTUBE,
                    VideoSourceKind.YOUTUBE,
                ):
                    external_id = str(uuid4())
                    is_local = kind is VideoSourceKind.LOCAL_FILE
                    reference = SourceReference(
                        kind,
                        external_id,
                        "file:///private/source.mp4"
                        if is_local
                        else f"https://www.youtube.com/watch?v={external_id}",
                    )
                    cue = TranscriptCue(
                        CUE_ID,
                        reference,
                        "en",
                        0,
                        123,
                        456,
                        "Original evidence",
                        track_id="original-track",
                        caption_kind=CaptionKind.MANUAL,
                    )
                    unit = RetrievalUnit(UNIT_ID, 123, 456, cue.text, (cue.cue_id,))
                    video = await videos.save_import(
                        SafeVideoMetadata(reference, "Evidence fixture", 1000),
                        (cue,) if is_local else (),
                        (unit,) if is_local else (),
                    )
                    video_ids.append(video.video_id)
                preview_dir = settings.vision_workspace_root / str(video_ids[0])
                preview_dir.mkdir(parents=True)
                valid_path = preview_dir / "preview.jpg"
                valid_path.write_bytes(JPEG)
                outside_path = tmp_path / "outside.jpg"
                outside_path.write_bytes(JPEG)
                for sequence, path in enumerate((valid_path, outside_path)):
                    session.add(
                        VideoFrameRow(
                            id=frame_id if sequence == 0 else uuid4(),
                            video_id=video_ids[0],
                            sequence=sequence,
                            source="periodic",
                            capture_ms=123,
                            start_ms=120,
                            end_ms=240,
                            is_duplicate=sequence == 1,
                            image_path=str(path),
                            created_at=datetime.now(UTC),
                        )
                    )
                await session.commit()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield client, engine, owner, outsider, tuple(video_ids), frame_id
        finally:
            async with sessions.begin() as session:
                notebooks = select(WorkspaceNotebookRow.id).where(
                    WorkspaceNotebookRow.owner_id.in_([owner, outsider])
                )
                await session.execute(
                    delete(WorkspacePageOCRRow).where(
                        WorkspacePageOCRRow.version_id.in_(
                            select(WorkspaceDocumentRow.version_id).where(
                                WorkspaceDocumentRow.notebook_id.in_(notebooks)
                            )
                        )
                    )
                )
                await session.execute(
                    delete(WorkspaceDocumentRow).where(
                        WorkspaceDocumentRow.notebook_id.in_(notebooks)
                    )
                )
                await session.execute(
                    delete(WorkspaceTextSourceRow).where(
                        WorkspaceTextSourceRow.notebook_id.in_(notebooks)
                    )
                )
                await session.execute(
                    delete(WorkspaceLegacySourceRow).where(
                        WorkspaceLegacySourceRow.video_id.in_(video_ids)
                    )
                )
                await session.execute(delete(VideoRow).where(VideoRow.id.in_(video_ids)))
                await session.execute(
                    delete(WorkspaceNotebookRow).where(
                        WorkspaceNotebookRow.owner_id.in_([owner, outsider])
                    )
                )
                await session.execute(
                    delete(WorkspaceAccountRow).where(WorkspaceAccountRow.id.in_([owner, outsider]))
                )


@pytest.mark.asyncio
async def test_local_semantic_index_preserves_sources_and_model_revision(
    workspace: tuple[httpx.AsyncClient, AsyncEngine, UUID, UUID, tuple[UUID, ...], UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, owner, outsider, videos, _ = workspace
    created = await client.post(
        "/v1/workspace/notebooks", headers=OWN, json={"title": "Semantic fixture"}
    )
    notebook = UUID(created.json()["notebook_id"])
    base = f"/v1/workspace/notebooks/{notebook}"
    index = f"{base}/semantic-index"
    search = f"{base}/semantic-search"
    pdf_text = "Sodium chloride dissolves in the ocean."
    ocr_text = "The eyepiece magnifies the specimen."
    notes = "\n\n".join(
        ["A flame provides heat.", *(f"An unrelated astronomy note {i}." for i in range(14))]
    )
    paste = await client.post(
        f"{base}/text-sources",
        headers=OWN,
        json={"title": "Notes", "format": "plain", "text": notes},
    )
    assert paste.status_code == 200
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await WorkspaceSourceRepository(session).assign_legacy_videos(owner, notebook, videos[:1])
        docs = WorkspaceDocumentRepository(session)
        pdf = await docs.save(
            owner,
            notebook,
            DocumentImport(
                "field.pdf",
                "pdf",
                "e" * 64,
                200,
                ParsedDocument(
                    sections=(
                        StoredDocumentSection(text=pdf_text, spans=((0, len(pdf_text)),)),
                        StoredDocumentSection(text="", spans=()),
                    )
                ),
            ),
        )
        page = await docs.section(owner, notebook, pdf.source_id, pdf.version_id, 2)
        ocr = WorkspacePageOCRRepository(session, page)
        assert await ocr.reserve(owner)
        completed = await ocr.finish("succeeded", prepare_workspace_text(ocr_text))
        await session.commit()

    other = await client.post(
        "/v1/workspace/notebooks", headers=OWN, json={"title": "Other semantic notebook"}
    )
    other_base = f"/v1/workspace/notebooks/{other.json()['notebook_id']}"
    assert (
        await client.post(
            f"{other_base}/text-sources",
            headers=OWN,
            json={"title": "Private distractor", "format": "plain", "text": "salty seawater"},
        )
    ).status_code == 200

    configured = Mock(
        return_value=auth_settings(embedding_model="bge-m3", embedding_model_revision="ollama")
    )
    monkeypatch.setattr(workspace_semantic, "workspace_settings", configured)
    catalog = AsyncMock(return_value={"bge-m3:latest": "revision-a"})
    monkeypatch.setattr(workspace_semantic, "probe_ollama_models", catalog)

    async def vectorize(contents: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        await asyncio.sleep(0.005)
        categories = {
            pdf_text: 0,
            "salty seawater": 0,
            ocr_text: 1,
            "look through a microscope": 1,
            "A flame provides heat.": 2,
            "fire is warm": 2,
            "Original evidence": 3,
        }
        return tuple(
            tuple(float(i == categories.get(content, 4)) for i in range(1024))
            for content in contents
        )

    embed = AsyncMock(side_effect=vectorize)
    monkeypatch.setattr(OllamaBgeM3EmbeddingProvider, "embed", embed)
    assert (await client.get(index)).status_code == 401
    assert (await client.get(index, headers=OTHER)).status_code == 404
    assert (
        await client.post(
            search, headers=OWN, json={"query": "after the event", "collection_id": str(uuid4())}
        )
    ).status_code == 422
    assert (
        await client.post(
            search, headers=OWN, json={"query": "salty seawater", "collection_id": str(uuid4())}
        )
    ).status_code == 409
    embed.assert_not_awaited()

    status = (await client.get(index, headers=OWN)).json()
    assert status["indexed"] == 0 and status["total"] == 18 and status["active_collection"] is None
    assert status["available_profile"]["revision"] == "revision-a"
    body: dict[str, object] = {
        "profile": "local-bge-m3",
        "revision": "revision-a",
        "expected_collection": None,
    }
    measurements: list[dict[str, float]] = []
    started = perf_counter()
    first = await client.post(index, headers=OWN, json=body)
    measurements.append(
        {
            "api_ms": (perf_counter() - started) * 1_000,
            **{key: first.json().get(key, 0) for key in ("prepare_ms", "embedding_ms", "save_ms")},
        }
    )
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    assert first.json()["added"] == 16 and first.json()["indexed"] == 16
    collection = first.json()["active_collection"]
    assert (await client.post(index, headers=OWN, json=body)).status_code == 409
    body["expected_collection"] = collection
    started = perf_counter()
    second = await client.post(index, headers=OWN, json=body)
    measurements.append(
        {
            "api_ms": (perf_counter() - started) * 1_000,
            **{key: second.json()[key] for key in ("prepare_ms", "embedding_ms", "save_ms")},
        }
    )
    assert (
        second.status_code == 200 and second.json()["added"] == 2 and second.json()["indexed"] == 18
    )
    assert (await client.post(index, headers=OWN, json=body)).json()["added"] == 0
    assert (await client.get(f"{other_base}/semantic-index", headers=OWN)).json()["indexed"] == 0

    baseline = await client.post(f"{base}/search", headers=OWN, json={"query": "salty seawater"})
    assert baseline.status_code == 200 and baseline.json()["hits"] == []
    samples: list[dict[str, float]] = []
    for _ in range(4):
        for query, kind, page_number in (
            ("salty seawater", "document", 1),
            ("look through a microscope", "page_ocr", 2),
            ("fire is warm", "text", None),
        ):
            started = perf_counter()
            response = await client.post(
                search, headers=OWN, json={"query": query, "collection_id": collection}
            )
            result = response.json()
            samples.append(
                {
                    "api_ms": (perf_counter() - started) * 1_000,
                    "embedding_ms": result["embedding_ms"],
                    "query_ms": result["query_ms"],
                }
            )
            assert response.status_code == 200
            top = result["hits"][0]
            assert top["kind"] == kind and top.get("page_number") == page_number
            if kind == "page_ocr":
                assert top["block"] == completed.blocks[0].model_dump(mode="json")
            video = next(hit for hit in result["hits"] if hit["kind"] == "video")
            assert (
                video["unit_id"],
                video["start_ms"],
                video["end_ms"],
                video["cues"][0]["cue_id"],
            ) == (UNIT_ID, 123, 456, CUE_ID)
            assert len(result["hits"]) == 18
    print(
        json.dumps(
            {
                "initial_batches": measurements,
                "passages_indexed": 18,
                "queries": len(samples),
                "top1_correct": len(samples),
                "search_median_ms": {
                    stage: round(median(row[stage] for row in samples), 3) for stage in samples[0]
                },
            }
        )
    )

    catalog.return_value = {"bge-m3": "revision-b"}
    assert (
        await client.post(
            search, headers=OWN, json={"query": "salty seawater", "collection_id": collection}
        )
    ).status_code == 409
    assert (await client.post(index, headers=OWN, json={**body, "revision": "revision-b"})).json()[
        "error"
    ]["code"] == "WORKSPACE_SEMANTIC_REBUILD_REQUIRED"
    embed.side_effect = EmbeddingProviderError("simulated Ollama interruption")
    rebuild = {**body, "revision": "revision-b", "replace": True}
    assert (await client.post(index, headers=OWN, json=rebuild)).json()["error"][
        "code"
    ] == "WORKSPACE_EMBEDDING_UNAVAILABLE"
    assert (await client.get(index, headers=OWN)).json()["active_collection"] == collection
    embed.side_effect = vectorize
    replaced = await client.post(index, headers=OWN, json=rebuild)
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["active_collection"] != collection and replaced.json()["indexed"] == 16
    assert replaced.json()["active_profile"]["revision"] == "revision-b"
    current_id = replaced.json()["active_collection"]
    assert (
        await client.post(
            search, headers=OWN, json={"query": "salty seawater", "collection_id": collection}
        )
    ).status_code == 409

    configured.return_value = auth_settings(embedding_model="other-model")
    assert (await client.get(index, headers=OWN)).json()["available_profile"] is None
    assert (
        await client.post(
            search,
            headers=OWN,
            json={"query": "salty seawater", "collection_id": replaced.json()["active_collection"]},
        )
    ).status_code == 503
    configured.return_value = auth_settings(embedding_model_revision="incorrect-pin")
    assert (await client.get(index, headers=OWN)).json()["available_profile"] is None
    configured.return_value = auth_settings()
    catalog.return_value = {}
    assert (await client.get(index, headers=OWN)).json()["available_profile"] is None
    catalog.return_value = {"bge-m3": "revision-b"}
    assert (
        await client.post(
            index,
            headers=OWN,
            json={**rebuild, "expected_collection": current_id, "revision": "wrong"},
        )
    ).status_code == 409

    catalog.side_effect = [{"bge-m3": "revision-b"}, {"bge-m3": "revision-c"}]
    drift = await client.post(
        search, headers=OWN, json={"query": "salty seawater", "collection_id": current_id}
    )
    assert drift.status_code == 409
    catalog.side_effect = None
    catalog.return_value = {"bge-m3": "revision-c"}

    both_embedding = asyncio.Event()
    arrivals = 0

    async def simultaneous(contents: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both_embedding.set()
        await asyncio.wait_for(both_embedding.wait(), timeout=5)
        return await vectorize(contents)

    embed.side_effect = simultaneous
    concurrent = {**rebuild, "revision": "revision-c", "expected_collection": current_id}
    first_rebuild, second_rebuild = await asyncio.gather(
        client.post(index, headers=OWN, json=concurrent),
        client.post(index, headers=OWN, json=concurrent),
    )
    assert {first_rebuild.status_code, second_rebuild.status_code} == {200, 409}
    active = (await client.get(index, headers=OWN)).json()
    assert active["indexed"] == 16 and active["active_profile"]["revision"] == "revision-c"
    embed.side_effect = vectorize

    async with async_sessionmaker(engine)() as session:
        snapshot = await WorkspaceSemanticRepository(session, owner, notebook).active()
        assert snapshot is not None
        with pytest.raises(NotebookNotFound):
            await WorkspaceSemanticRepository(session, outsider, notebook).store(
                snapshot.collection_id, snapshot.spec, ()
            )
    with monkeypatch.context() as patch:
        patch.setattr(
            WorkspaceSemanticRepository, "active", AsyncMock(side_effect=[snapshot, None])
        )
        replaced_while_searching = await client.post(
            search,
            headers=OWN,
            json={
                "query": "salty seawater",
                "collection_id": active["active_collection"],
            },
        )
        assert replaced_while_searching.status_code == 409

    monkeypatch.setattr(
        WorkspaceSemanticRepository, "store", AsyncMock(side_effect=NotebookNotFound)
    )
    assert (
        await client.post(
            index,
            headers=OWN,
            json={**concurrent, "expected_collection": active["active_collection"]},
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_workspace_answer_validates_selected_passages_and_times_each_stage(
    workspace: tuple[httpx.AsyncClient, AsyncEngine, UUID, UUID, tuple[UUID, ...], UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, owner, _, videos, _ = workspace
    created = await client.post(
        "/v1/workspace/notebooks", headers=OWN, json={"title": "Answer fixture"}
    )
    notebook = UUID(created.json()["notebook_id"])
    base = f"/v1/workspace/notebooks/{notebook}"
    pasted = "Original evidence records an oak seedling."
    paste = await client.post(
        f"{base}/text-sources",
        headers=OWN,
        json={
            "title": "Notebook observation",
            "format": "plain",
            "text": pasted,
        },
    )
    pdf_text = "Original evidence calls the specimen an oak seedling."
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await WorkspaceSourceRepository(session).assign_legacy_videos(owner, notebook, videos[:1])
        docs = WorkspaceDocumentRepository(session)
        pdf = await docs.save(
            owner,
            notebook,
            DocumentImport(
                "field.pdf",
                "pdf",
                "c" * 64,
                200,
                ParsedDocument(
                    sections=(StoredDocumentSection(text=pdf_text, spans=((0, len(pdf_text)),)),)
                ),
            ),
        )
        await session.commit()
    results = await client.post(f"{base}/search", headers=OWN, json={"query": "original evidence"})
    hits = {hit["kind"]: hit for hit in results.json()["hits"]}
    assert set(hits) == {"video", "text", "document"}
    assert hits["document"]["source"]["source_id"] == str(pdf.source_id)
    selected = [
        UNIT_ID,
        paste.json()["blocks"][0]["block_id"],
        hits["document"]["block"]["block_id"],
    ]
    request = {
        "query": "original evidence",
        "question": "What was recorded?",
        "evidence_ids": selected,
    }
    path = f"{base}/answers"

    settings = auth_settings(generation_model="fixture:latest", generation_model_revision="pinned")
    monkeypatch.setattr(workspace_routes, "workspace_settings", Mock(return_value=settings))
    models = AsyncMock(return_value={"fixture:latest": "pinned"})
    monkeypatch.setattr(workspace_routes, "probe_ollama_models", models)

    valid_blocks = [
        {
            "text": "The field notes and notebook describe an oak seedling.",
            "citations": [
                {"reference": "E2", "quote": "oak seedling"},
                {"reference": "E3", "quote": "oak seedling"},
            ],
        },
        {
            "text": "The transcript describes this as original evidence.",
            "citations": [
                {"reference": "E1", "quote": "Original evidence"},
            ],
        },
    ]
    draft_payload: dict[str, object] = {"blocks": valid_blocks}

    async def generate(
        question: str, passages: tuple[WorkspaceAnswerPassage, ...]
    ) -> WorkspaceGeneratedDraft:
        assert question == "What was recorded?"
        assert [(item.reference, item.kind, item.text) for item in passages] == [
            ("E1", "video", "Original evidence"),
            ("E2", "text", pasted),
            ("E3", "document", pdf_text),
        ]
        await asyncio.sleep(0.006)
        return WorkspaceGeneratedDraft(
            "fixture:latest",
            WorkspaceAnswerDraft.model_validate(draft_payload),
            WorkspaceModelMetrics(),
        )

    generator = AsyncMock(side_effect=generate)
    monkeypatch.setattr(OllamaGenerationProvider, "generate_workspace_answer", generator)
    assert (await client.post(path, json=request)).status_code == 401
    assert (await client.post(path, headers=OTHER, json=request)).status_code == 404
    assert (
        await client.post(path, headers=OWN, json={**request, "evidence_ids": [UNIT_ID, UNIT_ID]})
    ).status_code == 422
    stale = await client.post(path, headers=OWN, json={**request, "evidence_ids": [str(uuid4())]})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "WORKSPACE_EVIDENCE_SELECTION_STALE"
    temporal = await client.post(
        path, headers=OWN, json={**request, "question": "What happened after the harvest?"}
    )
    assert temporal.status_code == 422
    assert temporal.json()["error"]["code"] == "WORKSPACE_TEMPORAL_SCOPE_REQUIRED"
    models.assert_not_awaited()
    generator.assert_not_awaited()

    for available in ({}, {"fixture:latest": "other-digest"}):
        models.return_value = available
        missing = await client.post(path, headers=OWN, json=request)
        assert missing.status_code == 503
        assert missing.json()["error"]["code"] == "WORKSPACE_ANSWER_MODEL_NOT_READY"
    generator.assert_not_awaited()
    models.return_value = {"fixture:latest": "pinned"}

    warm = await client.post(path, headers=OWN, json=request)
    assert warm.status_code == 200, warm.text
    assert warm.headers["cache-control"] == "no-store"
    samples: list[dict[str, float]] = []
    for _ in range(8):
        started = perf_counter()
        response = await client.post(path, headers=OWN, json=request)
        api_ms = (perf_counter() - started) * 1_000
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "ollama" and body["model"] == "fixture:latest"
        assert [
            citation["evidence"] for block in body["blocks"] for citation in block["citations"]
        ] == [
            hits["text"],
            hits["document"],
            hits["video"],
        ]
        samples.append({"api_ms": api_ms, **body["timings"]})
    assert len(samples) == 8
    print(
        json.dumps(
            {
                "requests": len(samples),
                "validated_citations": len(samples) * 3,
                "passage_characters": len(pasted) + len(pdf_text) + len("Original evidence"),
                "stage_median_ms": {
                    stage: round(median(item[stage] for item in samples), 3) for stage in samples[0]
                },
            }
        )
    )

    for citation in (
        {"reference": "E4", "quote": "oak seedling"},
        {"reference": "E2", "quote": "Brazil nut"},
    ):
        draft_payload["blocks"] = [
            *valid_blocks,
            {"text": "Unverified statement.", "citations": [citation]},
        ]
        failure = await client.post(path, headers=OWN, json=request)
        assert failure.status_code == 502
        assert failure.json()["error"]["code"] == "WORKSPACE_ANSWER_UNVERIFIED"
        assert "Unverified statement" not in failure.text
    draft_payload["blocks"] = []
    abstained = await client.post(path, headers=OWN, json=request)
    assert abstained.status_code == 200 and abstained.json()["blocks"] == []
    generator.side_effect = GenerationProviderError("private upstream content")
    unavailable = await client.post(path, headers=OWN, json=request)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "WORKSPACE_ANSWER_UNAVAILABLE"
    assert "private upstream content" not in unavailable.text


@pytest.mark.asyncio
async def test_migrated_sources_preserve_evidence_and_never_cross_notebook_boundaries(
    workspace: tuple[httpx.AsyncClient, AsyncEngine, UUID, UUID, tuple[UUID, ...], UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, owner, outsider, videos, frame_id = workspace
    created = await client.post("/v1/workspace/notebooks", headers=OWN, json={"title": "Evidence"})
    assert created.status_code == 201
    notebook_id = UUID(created.json()["notebook_id"])
    path = f"/v1/workspace/notebooks/{notebook_id}/sources"
    source = f"{path}/{videos[0]}"
    assert (await client.get(path, headers=OWN)).json() == {"sources": [], "next_after": None}
    assert (await client.get(f"{source}/transcript", headers=OWN)).status_code == 404

    settings = Settings()
    assert await assign(settings, owner, notebook_id, videos[:2], apply=False) == 2
    assert (await client.get(path, headers=OWN)).json()["sources"] == []
    assert await assign(settings, owner, notebook_id, videos[:2], apply=True) == 2
    assert await assign(settings, owner, notebook_id, videos[:2], apply=True) == 0
    page = (await client.get(f"{path}?limit=1", headers=OWN)).json()
    bottom = (await client.get(f"{path}?limit=1&after={page['next_after']}", headers=OWN)).json()
    assert [item["video_id"] for item in page["sources"] + bottom["sources"]] == [
        str(item) for item in sorted(videos[:2])
    ]
    assert bottom["next_after"] is None

    async with async_sessionmaker(engine)() as session:
        notebooks = WorkspaceRepository(session)
        await notebooks.register_account(outsider)
        other_notebook = await notebooks.create_notebook(outsider, NotebookDraft(title="Other"))
        await session.commit()
        assignments = WorkspaceSourceRepository(session)
        with pytest.raises(NotebookNotFound):
            await assignments.assign_legacy_videos(outsider, notebook_id, videos)
        with pytest.raises(LegacySourceAssignmentError, match="distinct video IDs"):
            await assignments.assign_legacy_videos(owner, notebook_id, [videos[0], videos[0]])
        with pytest.raises(LegacySourceAssignmentError, match="do not exist"):
            await assignments.assign_legacy_videos(owner, notebook_id, [videos[2], uuid4()])
        with pytest.raises(LegacySourceAssignmentError, match="already assigned"):
            await assignments.assign_legacy_videos(outsider, other_notebook.notebook_id, videos)
        await session.commit()
        flags = await session.execute(
            text(
                "SELECT relrowsecurity, "
                "(SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND "
                "tablename = 'workspace_legacy_sources') "
                "FROM pg_class WHERE oid = 'public.workspace_legacy_sources'::regclass"
            )
        )
        assert flags.one() == (True, 0)
    other_path = f"/v1/workspace/notebooks/{other_notebook.notebook_id}/sources"
    assert (await client.get(other_path, headers=OTHER)).json()["sources"] == []
    assert (await client.get(path, headers=OTHER)).status_code == 404
    assert (await client.get(f"{path}/{videos[2]}/transcript", headers=OWN)).status_code == 404

    transcript = await client.get(f"{source}/transcript", headers=OWN)
    assert transcript.status_code == 200
    assert transcript.headers["cache-control"] == "no-store"
    body = transcript.json()
    assert body["source"]["canonical_url"] is None and "file:" not in transcript.text
    assert body["cues"][0]["cue_id"] == CUE_ID
    assert body["cues"][0]["origin"] == "caption"
    assert body["cues"][0]["track_id"] == "original-track"
    unit = body["retrieval_units"][0]
    assert (unit["retrieval_unit_id"], unit["start_ms"], unit["end_ms"], unit["cue_ids"]) == (
        UNIT_ID,
        123,
        456,
        [CUE_ID],
    )

    frames = await client.get(f"{source}/frames", headers=OWN)
    assert frames.json() == [
        {
            "frame_id": str(frame_id),
            "sequence": 0,
            "start_ms": 120,
            "end_ms": 240,
            "capture_ms": 123,
        }
    ]
    preview = f"{source}/frames/0/image"
    response = await client.get(preview, headers=OWN)
    assert response.content == JPEG and response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"] == "image/jpeg"
    for suffix in ("transcript", "frames", "frames/0/image"):
        assert (await client.get(f"{source}/{suffix}")).status_code == 401
        assert (await client.get(f"{source}/{suffix}", headers=OTHER)).status_code == 404
        assert (
            await client.get(f"{other_path}/{videos[0]}/{suffix}", headers=OTHER)
        ).status_code == 404
    for sequence in (1, 2):
        denied = await client.get(f"{source}/frames/{sequence}/image", headers=OWN)
        assert denied.status_code == 404
        assert denied.json()["error"]["code"] == "WORKSPACE_PREVIEW_NOT_FOUND"
    assert (
        await client.get(f"/v1/videos/{videos[0]}/frames/0/image", headers=OWN)
    ).status_code == 403

    remote = AsyncMock(return_value=JPEG)
    monkeypatch.setattr(workspace_routes, "read_supabase_preview", remote)
    monkeypatch.setattr(
        workspace_routes,
        "workspace_settings",
        Mock(
            return_value=auth_settings(
                workspace_preview_storage="supabase", supabase_storage_secret_key=None
            )
        ),
    )
    unavailable = await client.get(preview, headers=OWN)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "WORKSPACE_PREVIEW_STORAGE_UNAVAILABLE"
    remote.assert_not_awaited()
    monkeypatch.setattr(
        workspace_routes,
        "workspace_settings",
        Mock(
            return_value=auth_settings(
                workspace_preview_storage="supabase",
                supabase_storage_secret_key="sb_secret_synthetic",
            )
        ),
    )
    assert (await client.get(preview, headers=OTHER)).status_code == 404
    remote.assert_not_awaited()
    assert (await client.get(preview, headers=OWN)).content == JPEG
    remote.assert_awaited_once()


@pytest.mark.asyncio
async def test_keyword_search_opens_owned_evidence_and_measures_labeled_queries(
    workspace: tuple[httpx.AsyncClient, AsyncEngine, UUID, UUID, tuple[UUID, ...], UUID],
) -> None:
    client, engine, owner, _, videos, _ = workspace
    created = await client.post(
        "/v1/workspace/notebooks", headers=OWN, json={"title": "Search fixture"}
    )
    notebook = UUID(created.json()["notebook_id"])
    base = f"/v1/workspace/notebooks/{notebook}"
    pasted = "🌱 Archive language.\n\nOriginal evidence is preserved.\n\nAlpha\n\nBeta"
    paste = await client.post(
        f"{base}/text-sources",
        headers=OWN,
        json={
            "title": "Notes",
            "format": "plain",
            "text": pasted,
        },
    )
    assert paste.status_code == 200
    pdf_text = "Original evidence from a winter sample."
    docx_text = "An independent original evidence record."
    ocr_text = "Original evidence from an optical scan."
    async with async_sessionmaker(engine)() as session:
        await WorkspaceSourceRepository(session).assign_legacy_videos(owner, notebook, videos[:2])
        docs = WorkspaceDocumentRepository(session)
        pdf = await docs.save(
            owner,
            notebook,
            DocumentImport(
                "field-notes.pdf",
                "pdf",
                "a" * 64,
                200,
                ParsedDocument(
                    sections=(
                        StoredDocumentSection(text=pdf_text, spans=((0, len(pdf_text)),)),
                        StoredDocumentSection(text="", spans=()),
                    )
                ),
            ),
        )
        docx = await docs.save(
            owner,
            notebook,
            DocumentImport(
                "field-notes.docx",
                "docx",
                "b" * 64,
                200,
                ParsedDocument(
                    sections=(StoredDocumentSection(text=docx_text, spans=((0, len(docx_text)),)),)
                ),
            ),
        )
        ocr_page = await docs.section(owner, notebook, pdf.source_id, pdf.version_id, 2)
        ocr = WorkspacePageOCRRepository(session, ocr_page)
        assert await ocr.reserve(owner)
        completed = await ocr.finish("succeeded", prepare_workspace_text(ocr_text))

    other = await client.post(
        "/v1/workspace/notebooks", headers=OWN, json={"title": "Other notebook"}
    )
    other_base = f"/v1/workspace/notebooks/{other.json()['notebook_id']}"
    assert (
        await client.post(
            f"{other_base}/text-sources",
            headers=OWN,
            json={
                "title": "Other notes",
                "format": "plain",
                "text": "Original evidence excluded.",
            },
        )
    ).status_code == 200
    path = f"{base}/search"
    assert (await client.post(path, json={"query": "evidence"})).status_code == 401
    assert (await client.post(path, headers=OTHER, json={"query": "evidence"})).status_code == 404
    for invalid in ("  ", "x" * 161, "hidden\0text"):
        assert (await client.post(path, headers=OWN, json={"query": invalid})).status_code == 422

    expected: dict[str, set[tuple[str, str, int | None]]] = {
        "Original evidence": {
            ("video", str(videos[0]), None),
            ("text", paste.json()["source_id"], None),
            ("document", str(pdf.source_id), 1),
            ("document", str(docx.source_id), 1),
            ("page_ocr", str(pdf.source_id), 2),
        },
        "archive language": {("text", paste.json()["source_id"], None)},
        "winter sample": {("document", str(pdf.source_id), 1)},
        "optical scan": {("page_ocr", str(pdf.source_id), 2)},
        "alpha beta": set(),
        "quartz": set(),
    }
    warm = await client.post(path, headers=OWN, json={"query": "original evidence"})
    assert warm.status_code == 200, warm.text
    assert warm.headers["cache-control"] == "no-store"
    results = warm.json()["hits"]
    by_kind = {hit["kind"]: hit for hit in results}
    hit = by_kind["video"]
    assert (hit["unit_id"], hit["start_ms"], hit["end_ms"]) == (UNIT_ID, 123, 456)
    assert hit["source"]["canonical_url"] is None
    assert hit["cues"] == [
        {
            "cue_id": CUE_ID,
            "origin": "caption",
            "track_id": "original-track",
            "transcription_run_id": None,
        }
    ]
    assert by_kind["text"]["block"] == paste.json()["blocks"][1]
    assert by_kind["page_ocr"]["block"] == completed.blocks[0].model_dump(mode="json")
    for hit in results:
        if hit["kind"] == "document":
            source = hit["source"]
            assert hit["page_number"] == (1 if source["format"] == "pdf" else None)
            reopened = await client.get(
                f"{base}/documents/{source['source_id']}/versions/{source['version_id']}/sections/1",
                headers=OWN,
            )
            assert hit["block"] == reopened.json()["blocks"][0]

    api_ms: list[float] = []
    query_ms: list[float] = []
    retrieved = 0
    window = perf_counter()
    for _ in range(5):
        for query, labels in expected.items():
            started = perf_counter()
            response = await client.post(path, headers=OWN, json={"query": query})
            api_ms.append((perf_counter() - started) * 1_000)
            assert response.status_code == 200
            body = response.json()
            query_ms.append(body["query_ms"])
            assert body["profile"] == "postgres-keywords-v1"
            observed = {
                (
                    entry["kind"],
                    entry["source"].get("source_id", entry["source"].get("video_id")),
                    entry.get("section_number"),
                )
                for entry in body["hits"]
            }
            assert observed == labels and len(body["hits"]) == len(labels)
            retrieved += len(observed)
    window = perf_counter() - window
    print(
        json.dumps(
            {
                "requests": len(api_ms),
                "hits": retrieved,
                "fixture_chars": sum(
                    map(len, (pasted, pdf_text, docx_text, ocr_text, "Original evidence"))
                ),
                "api_median_ms": round(median(api_ms), 3),
                "api_p95_ms": round(sorted(api_ms)[28], 3),
                "query_median_ms": round(median(query_ms), 3),
                "query_p95_ms": round(sorted(query_ms)[28], 3),
                "window_seconds": round(window, 4),
                "requests_per_second": round(len(api_ms) / window, 2),
            }
        )
    )
