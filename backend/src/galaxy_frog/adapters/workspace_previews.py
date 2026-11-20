"""Read generated JPEG previews after authorization in the application database."""

import json
from asyncio import timeout
from pathlib import Path
from typing import cast
from uuid import UUID

import httpx
from anyio import Path as AsyncPath

MAX_PREVIEW_BYTES = 8 * 1024 * 1024
PREVIEW_BUCKET = "workspace-previews"


class PreviewUnavailable(Exception):
    """An absent, unsafe, or unservable preview."""


class PreviewStorageUnavailable(Exception):
    """The configured remote store could not safely serve the preview."""


def _checked_jpeg(content: bytes) -> bytes:
    if len(content) > MAX_PREVIEW_BYTES or not content.startswith(b"\xff\xd8\xff"):
        raise PreviewUnavailable
    return content


async def read_private_preview(root: Path, video_id: UUID, stored_path: str) -> bytes:
    try:
        directory = await AsyncPath(root).resolve() / str(video_id)
        path = await AsyncPath(stored_path).resolve()
        if (
            not path.is_relative_to(directory)
            or path.suffix.lower() not in {".jpg", ".jpeg"}
            or not await path.is_file()
        ):
            raise PreviewUnavailable
        async with await path.open("rb") as preview:
            content = await preview.read(MAX_PREVIEW_BYTES + 1)
    except OSError, RuntimeError, ValueError:
        raise PreviewUnavailable from None
    return _checked_jpeg(content)


async def _bounded_response(response: httpx.Response, limit: int) -> bytes:
    content = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
        if len(content) + len(chunk) > limit:
            raise PreviewUnavailable
        content.extend(chunk)
    return bytes(content)


async def read_supabase_preview(
    client: httpx.AsyncClient, origin: str, key: str, video_id: UUID, frame_id: UUID
) -> bytes:
    storage = f"{origin}/storage/v1"
    headers = {"apikey": key, "accept-encoding": "identity"}
    try:
        async with timeout(8):
            async with client.stream(
                "GET", f"{storage}/bucket/{PREVIEW_BUCKET}", headers=headers, follow_redirects=False
            ) as response:
                response.raise_for_status()
                await _require_private_bucket(response)
            async with client.stream(
                "GET",
                f"{storage}/object/authenticated/{PREVIEW_BUCKET}/{video_id}/{frame_id}.jpg",
                headers=headers,
                follow_redirects=False,
            ) as response:
                if response.status_code == 404:
                    raise PreviewUnavailable
                response.raise_for_status()
                return _checked_jpeg(await _bounded_response(response, MAX_PREVIEW_BYTES))
    except httpx.HTTPError, TimeoutError:
        raise PreviewStorageUnavailable from None


async def _require_private_bucket(response: httpx.Response) -> None:
    try:
        body: object = json.loads(await _bounded_response(response, 16 * 1024))
    except PreviewUnavailable, ValueError:
        raise PreviewStorageUnavailable from None
    if not isinstance(body, dict) or cast(dict[str, object], body).get("public") is not False:
        raise PreviewStorageUnavailable
