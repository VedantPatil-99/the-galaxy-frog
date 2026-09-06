"""Tests for the optional QStash dispatch boundary."""

from typing import Any
from uuid import uuid4

import pytest
from qstash.message import PublishResponse

from galaxy_frog.adapters.dispatch.qstash import QStashJobDispatcher, QStashSignatureVerifier
from galaxy_frog.application.ingestion.dispatch import (
    DispatchMessage,
    DispatchSignatureError,
    JobDispatchError,
)


class Publisher:
    def __init__(self, response: object = PublishResponse("message-1", False)) -> None:
        self.response = response
        self.arguments: dict[str, Any] | None = None

    async def publish_json(self, **kwargs: Any) -> object:
        self.arguments = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.asyncio
async def test_qstash_publishes_only_identifier_action_and_deduplicates() -> None:
    publisher = Publisher()
    job_id = uuid4()
    dispatcher = QStashJobDispatcher(
        token="secret",
        callback_url="https://frog.example/internal/qstash/dispatch",
        publisher=publisher,
    )

    receipt = await dispatcher.dispatch(DispatchMessage(job_id=job_id))

    assert publisher.arguments == {
        "url": "https://frog.example/internal/qstash/dispatch",
        "body": {"job_id": str(job_id), "requested_action": "process"},
        "method": "POST",
        "deduplication_id": f"gf-{job_id}-process",
    }
    assert receipt.dispatcher == "qstash"
    assert receipt.external_message_id == "message-1"


@pytest.mark.parametrize("response", [RuntimeError("network"), object()])
@pytest.mark.asyncio
async def test_qstash_translates_provider_failures(response: object) -> None:
    dispatcher = QStashJobDispatcher(
        token="secret",
        callback_url="https://frog.example/internal/qstash/dispatch",
        publisher=Publisher(response),
    )

    with pytest.raises(JobDispatchError, match="QStash"):
        await dispatcher.dispatch(DispatchMessage(job_id=uuid4()))


def test_qstash_adapters_reject_invalid_configuration_and_signature() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        QStashJobDispatcher(token=" ", callback_url="https://frog.example/dispatch")

    verifier = QStashSignatureVerifier(
        current_signing_key="Y3VycmVudA==",
        next_signing_key="bmV4dA==",
    )
    with pytest.raises(DispatchSignatureError, match="invalid"):
        verifier.verify(
            signature="invalid",
            body='{"job_id":"00000000-0000-0000-0000-000000000000"}',
            url="https://frog.example/internal/qstash/dispatch",
        )
