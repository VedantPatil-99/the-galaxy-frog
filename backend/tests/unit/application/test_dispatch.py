"""Tests for provider-neutral ingestion dispatch."""

from uuid import uuid4

import pytest

from galaxy_frog.adapters.dispatch.local import LocalJobDispatcher
from galaxy_frog.application.ingestion.dispatch import DispatchAction, DispatchMessage


@pytest.mark.asyncio
async def test_local_dispatch_acknowledges_postgres_polled_job() -> None:
    message = DispatchMessage(job_id=uuid4())

    receipt = await LocalJobDispatcher().dispatch(message)

    assert message.requested_action is DispatchAction.PROCESS
    assert receipt.dispatcher == "postgres_polling"
    assert receipt.external_message_id is None
