"""Only a bounded, generated JPEG from the selected video can be served."""

from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from anyio import Path as AsyncPath
from pydantic import ValidationError

from galaxy_frog.adapters.workspace_previews import (
    MAX_PREVIEW_BYTES,
    PreviewStorageUnavailable,
    PreviewUnavailable,
    read_private_preview,
    read_supabase_preview,
)
from galaxy_frog.config import Settings

ORIGIN = "https://example.supabase.co"
KEY = "sb_secret_synthetic"
JPEG = b"\xff\xd8\xffpreview"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("missing.jpg", None),
        ("non-preview.txt", b"\xff\xd8\xff"),
        ("unexpected.jpg", b"not a JPEG"),
        ("oversized.jpg", b"\xff\xd8\xff" + b"x" * MAX_PREVIEW_BYTES),
        ("invalid\x00.jpg", None),
    ],
    ids=["missing", "extension", "content", "too-large", "invalid-path"],
)
async def test_bad_preview_is_never_served(
    tmp_path: Path, name: str, content: bytes | None
) -> None:
    video_id = uuid4()
    directory = tmp_path / str(video_id)
    directory.mkdir()
    path = directory / name
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(PreviewUnavailable):
        await read_private_preview(tmp_path, video_id, str(path))


@pytest.mark.asyncio
async def test_preview_disappearing_between_validation_and_open_has_safe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video_id = uuid4()
    directory = tmp_path / str(video_id)
    directory.mkdir()
    path = directory / "preview.jpg"
    path.write_bytes(JPEG)
    monkeypatch.setattr(AsyncPath, "open", AsyncMock(side_effect=FileNotFoundError))
    with pytest.raises(PreviewUnavailable):
        await read_private_preview(tmp_path, video_id, str(path))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bucket_body", "status", "object_body", "error"),
    [
        (b'{"public":false}', 200, JPEG, None),
        (b'{"public":false}', 404, b"", PreviewUnavailable),
        (b'{"public":false}', 400, b'{"statusCode":"404","error":"not_found"}', PreviewUnavailable),
        (b'{"public":false}', 400, b'{"code":"NoSuchKey"}', PreviewUnavailable),
        (b'{"public":false}', 400, b'{"statusCode":"403"}', PreviewStorageUnavailable),
        (b'{"public":false}', 400, b"invalid json", PreviewStorageUnavailable),
        (b'{"public":false}', 400, b"[]", PreviewStorageUnavailable),
        (b'{"public":false}', 503, b"", PreviewStorageUnavailable),
        (b'{"public":false}', 200, b"x" * (MAX_PREVIEW_BYTES + 1), PreviewUnavailable),
        (b'{"public":true}', 200, JPEG, PreviewStorageUnavailable),
        (b"[]", 200, JPEG, PreviewStorageUnavailable),
        (b"invalid json", 200, JPEG, PreviewStorageUnavailable),
        (b" " * (16 * 1024 + 1), 200, JPEG, PreviewStorageUnavailable),
    ],
    ids=[
        "private",
        "missing",
        "legacy-missing",
        "code-missing",
        "legacy-denied",
        "legacy-invalid-json",
        "legacy-non-object",
        "failure",
        "too-large",
        "public",
        "malformed",
        "invalid",
        "huge",
    ],
)
async def test_remote_previews_use_private_bucket_and_fixed_object_identity(
    bucket_body: bytes, status: int, object_body: bytes, error: type[Exception] | None
) -> None:
    video_id, frame_id = uuid4(), uuid4()
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["apikey"] == KEY
        assert "authorization" not in request.headers
        assert request.url.host == "example.supabase.co"
        requests.append(request.url.path)
        if len(requests) == 1:
            return httpx.Response(200, content=bucket_body)
        assert request.url.path == (
            f"/storage/v1/object/authenticated/workspace-previews/{video_id}/{frame_id}.jpg"
        )
        return httpx.Response(status, content=object_body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        if error is None:
            assert await read_supabase_preview(client, ORIGIN, KEY, video_id, frame_id) == JPEG
        else:
            with pytest.raises(error):
                await read_supabase_preview(client, ORIGIN, KEY, video_id, frame_id)
    assert requests[0] == "/storage/v1/bucket/workspace-previews"
    assert len(requests) == (2 if bucket_body == b'{"public":false}' else 1)


def test_storage_key_is_separate_from_publishable_key() -> None:
    with pytest.raises(ValidationError, match="modern Supabase secret key"):
        Settings.model_validate({"supabase_storage_secret_key": "sb_publishable_invalid"})
