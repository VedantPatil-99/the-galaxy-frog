"""Application quota accounting is bounded, conservative, and time-window aware."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from galaxy_frog.application.provider_policy import ProviderFailure
from galaxy_frog.application.provider_registry import provider_catalog
from galaxy_frog.config import Settings
from galaxy_frog.db.provider_quota import ProviderQuotaReservationRow, quota_usage, reserve_quota
from galaxy_frog.domain.providers import QuotaLimits


def test_quota_windows_preserve_uncertain_calls_and_boundary() -> None:
    now = datetime(2026, 11, 18, 8, 0, 10, tzinfo=UTC)
    start = now.replace(second=0)
    rows = tuple(
        ProviderQuotaReservationRow(id=uuid4(), group_id="test", created_at=when, input_tokens=10)
        for when in (now - timedelta(seconds=60), now - timedelta(seconds=59), start, now)
    )
    usage = quota_usage(rows, now, start, start + timedelta(days=1))
    assert usage.requests_last_minute == 3
    assert usage.reserved_input_tokens_last_minute == 30
    assert usage.requests_today == 2


def test_project_identity_groups_keys_and_models_without_exposing_group() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        gemini_api_key=SecretStr("interactive"),
        gemini_project_id="frog-interactive",
        gemini_study_api_key=SecretStr("different-key-same-project"),
        gemini_study_project_id="frog-interactive",
        gemini_generation_model="gemini-3.5-flash-lite",
        gemini_study_model="gemini-3.8-flash",
        gemini_generation_enabled=True,
        gemini_study_enabled=True,
    )
    entries = {entry.provider_id: entry for entry in provider_catalog(settings)}
    assert entries["gemini-text"].readiness == entries["gemini-study"].readiness == "configured"
    assert entries["gemini-text"].quota_group == entries["gemini-study"].quota_group
    assert "quota_group" not in entries["gemini-text"].model_dump_json()
    other = settings.model_copy(update={"gemini_study_project_id": "frog-background"})
    study = next(e for e in provider_catalog(other) if e.provider_id == "gemini-study")
    assert study.quota_group != entries["gemini-text"].quota_group
    assert "different-key-same-project" not in study.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("tokens", [0, -1, 11])
async def test_oversized_reservation_is_rejected_before_database(tokens: int) -> None:
    session = AsyncMock()
    with pytest.raises(ProviderFailure, match="PROVIDER_INPUT_LIMIT"):
        await reserve_quota(
            session,
            "test",
            QuotaLimits(requests_per_minute=1, input_tokens_per_minute=10, requests_per_day=1),
            tokens,
        )
    session.execute.assert_not_awaited()


def test_cloudflare_account_id_rejects_malformed_value() -> None:
    with pytest.raises(ValidationError, match="32-character account ID"):
        Settings(cloudflare_account_id="invalid-id")
