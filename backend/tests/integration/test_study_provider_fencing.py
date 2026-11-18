"""Real PostgreSQL fences selected Study calls without dispatching to any cloud."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.integration.test_postgres_lexical_search import (
    _seed,  # pyright: ignore[reportPrivateUsage]
)

from galaxy_frog.application.provider_policy import ProviderFailure
from galaxy_frog.application.provider_registry import provider_catalog
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.models import ProviderAttemptRow, ProviderConsentRow, VideoRow
from galaxy_frog.db.provider_quota import ProviderQuotaReservationRow
from galaxy_frog.db.provider_runtime import PostgresProviderRuntime, create_consent
from galaxy_frog.db.study_repository import StudyLeaseLost, StudyRepository, StudyRunRow
from galaxy_frog.domain.providers import (
    ConsentScope,
    ExternalData,
    ProfileRef,
    ProviderConsentRequest,
    ProviderOperation,
    ProviderSelection,
)
from galaxy_frog.domain.study import StudyKind

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1", reason="requires configured PostgreSQL"
)


@pytest.mark.asyncio
async def test_study_selection_is_persisted_and_lost_lease_blocks_new_dispatch() -> None:
    settings = Settings(
        gemini_study_api_key=SecretStr("synthetic-key"),
        gemini_study_project_id="test-" + uuid4().hex[:16],
        gemini_study_model="gemini-3.5-flash-lite",
        gemini_study_enabled=True,
    )
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    profile = ProfileRef(profile_id="gemini", version=1)
    video_id = consent_id = run_id = None
    entry = next(item for item in provider_catalog(settings) if item.provider_id == "gemini-study")
    try:
        async with sessions() as session:
            video_id, _units = await _seed(session, ("The answer is gradient descent.",))
            grant = await create_consent(
                session,
                ProviderConsentRequest(
                    profile=profile,
                    operation=ProviderOperation.STUDY,
                    source=str(video_id),
                    scopes=(ConsentScope(provider_id="gemini-study", data=ExternalData.TEXT),),
                ),
            )
            consent_id = grant.consent_id
            selection = ProviderSelection(profile=profile, consent_id=consent_id)
            request_id = uuid4()
            run = await StudyRepository(session).enqueue(
                video_id,
                StudyKind.CHAPTERS,
                request_id,
                provider_selection=selection,
            )
            run_id = run.run_id
            assert run.provider_selection == selection
            replay = await StudyRepository(session).enqueue(
                video_id,
                StudyKind.CHAPTERS,
                request_id,
                provider_selection=selection,
            )
            assert replay.run_id == run_id
            token = uuid4()
            await session.execute(
                update(StudyRunRow)
                .where(StudyRunRow.id == run_id)
                .values(
                    state="running",
                    lease_token=token,
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
                )
            )
            await session.commit()
        runtime = PostgresProviderRuntime(
            sessions,
            engine=engine,
            profile=profile,
            operation=ProviderOperation.STUDY,
            run_id=run_id,
            source=str(video_id),
            consent_id=consent_id,
            study_lease_token=token,
        )
        await runtime.verify_consent(entry, dispatch=True, input_tokens=100)
        async with sessions() as session:
            await session.execute(
                update(ProviderConsentRow)
                .where(ProviderConsentRow.id == consent_id)
                .values(revoked_at=datetime.now(UTC))
            )
            await session.commit()
        with pytest.raises(ProviderFailure, match="PROVIDER_CONSENT_REQUIRED"):
            await runtime.verify_consent(entry, dispatch=True, input_tokens=100)
        async with sessions() as session:
            await session.execute(
                update(StudyRunRow).where(StudyRunRow.id == run_id).values(lease_token=uuid4())
            )
            await session.commit()
        with pytest.raises(StudyLeaseLost):
            await runtime.verify_consent(entry, dispatch=True, input_tokens=100)
    finally:
        async with sessions() as session:
            if run_id:
                await session.execute(
                    delete(ProviderAttemptRow).where(ProviderAttemptRow.run_id == run_id)
                )
            if consent_id:
                await session.execute(
                    delete(ProviderConsentRow).where(ProviderConsentRow.id == consent_id)
                )
            if video_id:
                await session.execute(delete(VideoRow).where(VideoRow.id == video_id))
            await session.execute(
                delete(ProviderQuotaReservationRow).where(
                    ProviderQuotaReservationRow.group_id == entry.quota_group
                )
            )
            await session.commit()
        await engine.dispose()
