"""Optional QStash adapter for identifier-only ingestion wake-up hints."""

from typing import Any, Protocol, cast

from qstash import AsyncQStash, Receiver
from qstash.errors import SignatureError

from galaxy_frog.application.ingestion.dispatch import (
    DispatchMessage,
    DispatchReceipt,
    DispatchSignatureError,
    JobDispatchError,
)


class JsonPublisher(Protocol):
    """Narrow SDK seam used by the QStash dispatch adapter."""

    async def publish_json(self, **kwargs: Any) -> Any: ...


class QStashJobDispatcher:
    """Publish a deduplicated pointer to durable PostgreSQL job state."""

    def __init__(
        self,
        *,
        token: str,
        callback_url: str,
        publisher: JsonPublisher | None = None,
    ) -> None:
        if not token.strip() or not callback_url.strip():
            raise ValueError("QStash token and callback URL must not be blank")
        self._callback_url = callback_url
        self._publisher = publisher or cast(JsonPublisher, AsyncQStash(token).message)

    async def dispatch(self, message: DispatchMessage) -> DispatchReceipt:
        payload = {
            "job_id": str(message.job_id),
            "requested_action": message.requested_action.value,
        }
        try:
            response: Any = await self._publisher.publish_json(
                url=self._callback_url,
                body=payload,
                method="POST",
                deduplication_id=f"gf-{message.job_id}-{message.requested_action.value}",
            )
            message_id = response.message_id
        except Exception as exc:
            raise JobDispatchError("QStash could not accept the dispatch message.") from exc
        if not isinstance(message_id, str) or not message_id:
            raise JobDispatchError("QStash returned an invalid dispatch acknowledgement.")
        return DispatchReceipt(dispatcher="qstash", external_message_id=message_id)


class QStashSignatureVerifier:
    """Verify QStash JWT signatures with current and next signing keys."""

    def __init__(
        self,
        *,
        current_signing_key: str,
        next_signing_key: str,
        callback_url: str,
    ) -> None:
        self._receiver = Receiver(
            current_signing_key=current_signing_key,
            next_signing_key=next_signing_key,
        )
        self._callback_url = callback_url

    def verify(self, *, signature: str, body: str) -> None:
        try:
            self._receiver.verify(
                signature=signature,
                body=body,
                url=self._callback_url,
            )
        except SignatureError as exc:
            raise DispatchSignatureError("The QStash signature is invalid.") from exc
