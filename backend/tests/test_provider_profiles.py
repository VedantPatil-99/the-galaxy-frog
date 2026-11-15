"""Provider catalog and profile contract tests."""

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Insert

from galaxy_frog.api.app import create_app
from galaxy_frog.api.dependencies import get_video_repository
from galaxy_frog.application import provider_registry
from galaxy_frog.application.provider_registry import builtin_profiles, profile_validation_error
from galaxy_frog.config import Settings
from galaxy_frog.db.models import ProviderProfileVersionRow
from galaxy_frog.db.provider_profiles import PostgresProviderProfiles
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.providers import Capability, ProfileRef, ProviderMode, ProviderProfile


class ProfileSession:
    def __init__(self) -> None:
        self.rows: list[ProviderProfileVersionRow] = []
        self.conflict = False
        self.broken = False

    async def scalars(self, _statement: object) -> list[ProviderProfileVersionRow]:
        self._check()
        return self.rows

    async def scalar(self, statement: object) -> int | ProviderProfileVersionRow | None:
        self._check()
        if str(statement).startswith("SELECT"):
            return self.rows[-1].version if self.rows else None
        if self.conflict:
            return None
        assert isinstance(statement, Insert)
        compiled = statement.compile()
        row = ProviderProfileVersionRow(**compiled.params)
        self.rows.append(row)
        return row

    async def get(self, _model: object, key: tuple[str, int]) -> ProviderProfileVersionRow | None:
        return next((row for row in self.rows if (row.profile_id, row.version) == key), None)

    async def commit(self) -> None:
        pass

    def _check(self) -> None:
        if self.broken:
            raise SQLAlchemyError


@pytest.mark.parametrize(
    ("body", "status", "expected"),
    [
        (
            b'{"models":[{"model":"local","digest":"pinned"},'
            b'{"model":"proxied","digest":"pinned","remote_host":"cloud.example"},'
            b'{"model":"routed","digest":"pinned","remote_model":"upstream"},'
            b'{"model":"tag:cloud","digest":"pinned"},'
            b'{"model":"missing","digest":null}]}',
            200,
            {"local": "pinned"},
        ),
        (b" " * 262_145, 200, {}),
        (b"{}", 503, {}),
        (b"invalid JSON", 200, {}),
    ],
    ids=["only-local", "oversized", "http-error", "invalid-json"],
)
@pytest.mark.asyncio
async def test_ollama_probe_allows_only_local_models_with_revisions(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    status: int,
    expected: dict[str, str],
) -> None:
    requests: list[str] = []

    def tags(request: Request) -> Response:
        requests.append(str(request.url))
        return Response(status, content=body)

    def client(*, timeout: float, trust_env: bool) -> AsyncClient:
        assert not trust_env
        return AsyncClient(transport=MockTransport(tags), timeout=timeout, trust_env=trust_env)

    monkeypatch.setattr(provider_registry.httpx, "AsyncClient", client)
    assert await provider_registry.probe_ollama_models("https://remote.example") == {}
    assert await provider_registry.probe_ollama_models("http://127.0.0.1:11434") == expected
    assert requests == ["http://127.0.0.1:11434/api/tags"]


@pytest.mark.asyncio
async def test_catalog_distinguishes_configured_and_documented_without_leaking_credentials() -> (
    None
):
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        ollama_base_url="https://example.org",
        cloudflare_asr_enabled=True,
        cloudflare_account_id="a" * 32,
        cloudflare_api_token=SecretStr("cloudflare-secret"),
        bedrock_text_enabled=True,
        aws_bearer_token_bedrock=SecretStr("bedrock-secret"),
        external_frame_processing_enabled=True,
        ocr_external_frames_permitted=True,
        llama_cloud_api_key=SecretStr("ocr-secret"),
        gemini_api_key=SecretStr("gemini-secret"),
        gemini_visual_model="gemini-test",
        gemini_visual_revision="pinned",
        vlm_provider="gemini",
        visual_embedding_enabled=True,
    )
    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://test"
    ) as client:
        response = await client.get("/v1/providers/capabilities")
    assert response.status_code == 200
    entries = {item["provider_id"]: item for item in response.json()}
    assert entries["ollama-embedding"]["readiness"] == "setup_required"
    assert entries["amazon-transcribe"]["readiness"] == "catalog_only"
    for provider_id in (
        "cloudflare-whisper",
        "bedrock-nova-lite",
        "llamaparse-ocr",
        "gemini-vision",
    ):
        entry = entries[provider_id]
        assert entry["readiness"] == "configured"
        assert entry["device"] == "cloud"
        assert entry["pricing"]["source_url"].startswith("https://")
        assert isinstance(entry["pricing"]["stale"], bool)
    assert "-secret" not in response.text
    assert "a" * 32 not in response.text


@pytest.mark.asyncio
async def test_profile_edit_and_version_conflict() -> None:
    app = create_app(Settings())
    session = ProfileSession()
    db = cast(AsyncSession, session)
    app.dependency_overrides[get_video_repository] = lambda: SqlAlchemyVideoRepository(db)
    policy = {"mode": "manual", "candidates": {"generation": ["bedrock-nova-lite", "ollama-text"]}}
    edit = {"expected_version": 0, "policy": policy}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        profiles = await client.get("/v1/provider-profiles")
        assert {item["profile_id"] for item in profiles.json()} == set(builtin_profiles())
        created = await client.put("/v1/provider-profiles/custom", json=edit)
        assert created.status_code == 200
        assert created.json()["version"] == 1
        assert (await client.get("/v1/provider-profiles")).json()[1]["profile_id"] == "custom"
        conflict = await client.put("/v1/provider-profiles/custom", json=edit)
        assert conflict.json()["error"]["code"] == "PROVIDER_PROFILE_VERSION_CONFLICT"
        session.conflict = True
        raced = await client.put(
            "/v1/provider-profiles/custom", json={**edit, "expected_version": 1}
        )
        assert raced.status_code == 409
        invalid = await client.put(
            "/v1/provider-profiles/custom",
            json={**edit, "policy": {**policy, "mode": "automatic_cloud_permitted"}},
        )
        assert invalid.json()["error"]["code"] == "PROVIDER_PROFILE_INVALID"
        session.broken = True
        assert (await client.get("/v1/provider-profiles")).status_code == 503
        assert (await client.put("/v1/provider-profiles/custom", json=edit)).status_code == 503

    repository = PostgresProviderProfiles(db)
    local = await repository.get(ProfileRef(profile_id="local", version=1))
    custom = await repository.get(ProfileRef(profile_id="custom", version=1))
    assert local is not None and local.policy.mode is ProviderMode.LOCAL_ONLY
    assert custom is not None and custom.created_at is not None
    assert await repository.get(ProfileRef(profile_id="custom", version=2)) is None


@pytest.mark.parametrize("chain", [(), ("ollama-text", "ollama-text"), ("a", "b", "c", "d", "e")])
def test_provider_profiles_reject_invalid_chains(chain: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="distinct candidates"):
        ProviderProfile(mode=ProviderMode.MANUAL, candidates={Capability.GENERATION: chain})


def test_evaluation_requires_complete_exact_chains() -> None:
    with pytest.raises(ValidationError, match="exact pins"):
        ProviderProfile(mode=ProviderMode.MANUAL, candidates={}, evaluation=True)
    with pytest.raises(ValidationError, match="single provider"):
        ProviderProfile(
            mode=ProviderMode.MANUAL,
            candidates={Capability.GENERATION: ("ollama-text", "bedrock-nova-lite")},
            evaluation=True,
        )


@pytest.mark.parametrize(
    ("capability", "candidate", "mode", "message"),
    [
        (Capability.TRANSCRIPTION, "amazon-transcribe", ProviderMode.MANUAL, "selected capability"),
        (Capability.GENERATION, "missing-model", ProviderMode.MANUAL, "selected capability"),
        (Capability.STUDY, "ollama-text", ProviderMode.MANUAL, "selected capability"),
        (
            Capability.VISION,
            "gemini-vision",
            ProviderMode.LOCAL_ONLY,
            "outside the selected mode",
        ),
    ],
)
def test_policy_rejects_unsupported_candidates(
    capability: Capability, candidate: str, mode: ProviderMode, message: str
) -> None:
    policy = ProviderProfile(mode=mode, candidates={capability: (candidate,)})
    error = profile_validation_error(policy, provider_registry.provider_catalog(Settings()))
    assert error and message in error


def test_saved_evaluation_policy_requires_every_revision_and_collection() -> None:
    policy = builtin_profiles()["evaluation"]
    catalog = provider_registry.provider_catalog(Settings())
    assert profile_validation_error(policy, catalog) == (
        "Evaluation requires model revisions and both embedding collection IDs."
    )
    pins = {chain[0]: "pinned" for chain in policy.candidates.values()}
    collections = {Capability.TEXT_EMBEDDING: uuid4(), Capability.VISUAL_EMBEDDING: uuid4()}
    exact = policy.model_copy(
        update={"pinned_revisions": pins, "embedding_collections": collections}
    )
    assert profile_validation_error(exact, catalog) is None
    hosted = exact.model_copy(
        update={"candidates": {**exact.candidates, Capability.GENERATION: ("bedrock-nova-lite",)}}
    )
    assert profile_validation_error(hosted, catalog) == "Evaluation requires local providers."


@pytest.mark.asyncio
async def test_builtin_profile_overrides_are_versioned() -> None:
    session = ProfileSession()
    repo = PostgresProviderProfiles(cast(AsyncSession, session))
    local = builtin_profiles()["local"]
    saved = await repo.append("local", 1, local)
    assert saved.version == 2
    assert saved.created_at is not None and saved.created_at < datetime.now(UTC)
