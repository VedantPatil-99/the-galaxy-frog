"""Authenticated internal callback for optional QStash wake-up delivery."""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from galaxy_frog.api.dependencies import (
    get_dispatch_signature_verifier,
    get_ingestion_repository,
)
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.internal_schemas import InternalDispatchAccepted, InternalDispatchMessage
from galaxy_frog.application.ingestion.dispatch import (
    DispatchSignatureError,
    DispatchSignatureVerifier,
)
from galaxy_frog.db.ingestion_repository import PostgresIngestionRepository

router = APIRouter(prefix="/internal/qstash", tags=["internal"])

VerifierDependency = Annotated[
    DispatchSignatureVerifier,
    Depends(get_dispatch_signature_verifier),
]
RepositoryDependency = Annotated[PostgresIngestionRepository, Depends(get_ingestion_repository)]


@router.post(
    "/dispatch",
    response_model=InternalDispatchAccepted,
    status_code=HTTPStatus.ACCEPTED,
    include_in_schema=False,
)
async def accept_qstash_dispatch(
    request: Request,
    verifier: VerifierDependency,
    repository: RepositoryDependency,
) -> InternalDispatchAccepted:
    """Authenticate an identifier pointer and acknowledge existing durable work."""

    signature = request.headers.get("Upstash-Signature")
    if signature is None:
        raise _invalid_signature()
    raw_bytes = await request.body()
    try:
        raw_body = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid_message() from exc
    try:
        verifier.verify(signature=signature, body=raw_body)
    except DispatchSignatureError as exc:
        raise _invalid_signature() from exc
    try:
        message = InternalDispatchMessage.model_validate_json(raw_body)
    except ValidationError as exc:
        raise _invalid_message() from exc

    job = await repository.get(message.job_id)
    if job is None:
        raise ApiError(
            status_code=HTTPStatus.NOT_FOUND,
            code="INGESTION_JOB_NOT_FOUND",
            message="The requested ingestion job does not exist.",
            retryable=False,
        )
    return InternalDispatchAccepted(
        job_id=job.job_id,
        status=job.status,
        stage=job.stage,
    )


def _invalid_signature() -> ApiError:
    return ApiError(
        status_code=HTTPStatus.UNAUTHORIZED,
        code="QSTASH_SIGNATURE_INVALID",
        message="The dispatch signature is missing or invalid.",
        retryable=False,
    )


def _invalid_message() -> ApiError:
    return ApiError(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="INVALID_DISPATCH_MESSAGE",
        message="The authenticated dispatch message is invalid.",
        retryable=False,
        suggested_action="Send only a job ID and the supported requested action.",
    )
