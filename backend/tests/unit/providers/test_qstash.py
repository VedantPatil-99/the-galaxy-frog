"""Tests for the optional QStash dispatch boundary."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
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


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _signature(*, key: str, body: str, url: str) -> str:
    issued_at = int(datetime.now(UTC).timestamp())
    header = _base64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _base64url(
        json.dumps(
            {
                "iss": "Upstash",
                "sub": url,
                "exp": issued_at + 60,
                "nbf": issued_at - 1,
                "body": _base64url(hashlib.sha256(body.encode()).digest()),
            },
            separators=(",", ":"),
        ).encode()
    )
    signed = f"{header}.{payload}"
    digest = hmac.new(key.encode(), signed.encode(), hashlib.sha256).digest()
    return f"{signed}.{_base64url(digest)}"


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


@pytest.mark.parametrize(
    "response",
    [RuntimeError("network"), object(), PublishResponse("", False)],
)
@pytest.mark.asyncio
async def test_qstash_translates_provider_failures(response: object) -> None:
    dispatcher = QStashJobDispatcher(
        token="secret",
        callback_url="https://frog.example/internal/qstash/dispatch",
        publisher=Publisher(response),
    )

    with pytest.raises(JobDispatchError, match="QStash"):
        await dispatcher.dispatch(DispatchMessage(job_id=uuid4()))


def test_qstash_dispatcher_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        QStashJobDispatcher(token=" ", callback_url="https://frog.example/dispatch")


def test_qstash_verifier_accepts_current_or_next_key_and_binds_body_and_url() -> None:
    url = "https://frog.example/internal/qstash/dispatch"
    body = '{"job_id":"00000000-0000-0000-0000-000000000000","requested_action":"process"}'
    current_key = "current-signing-secret-at-least-32-bytes"
    next_key = "next-signing-secret-at-least-32-bytes"
    verifier = QStashSignatureVerifier(
        current_signing_key=current_key,
        next_signing_key=next_key,
        callback_url=url,
    )

    verifier.verify(signature=_signature(key=current_key, body=body, url=url), body=body)
    verifier.verify(signature=_signature(key=next_key, body=body, url=url), body=body)

    with pytest.raises(DispatchSignatureError, match="invalid"):
        verifier.verify(signature=_signature(key=current_key, body=body, url=url), body="tampered")
