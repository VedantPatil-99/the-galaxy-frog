"""Real workspace FastAPI and worker with synthetic Auth for the browser regression."""

import asyncio
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request, Response
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from tests.test_workspace_auth import AUTH_URL, KEY, auth_settings

from galaxy_frog.adapters.workspace_auth import SupabaseIdentityVerifier
from galaxy_frog.api.app import create_app
from galaxy_frog.api.workspace_auth import get_workspace_identity_verifier
from galaxy_frog.entrypoints.workspace_chat_worker import run_workspace_chat_worker

database = os.environ["DATABASE_URL"]
if urlsplit(database).hostname not in {"localhost", "127.0.0.1", "::1"}:
    raise RuntimeError("The browser regression requires a disposable loopback database.")

settings = auth_settings(
    database_url=database,
    ollama_base_url=os.environ["WORKSPACE_BROWSER_OLLAMA"],
    generation_model="fixture-local:4b",
    generation_model_revision="fixture-digest",
    ingestion_poll_seconds=0.25,
)
app = create_app(settings)
app_lifespan = app.router.lifespan_context
_drop_next_acknowledgement = True


@app.middleware("http")
async def drop_first_turn_acknowledgement(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    global _drop_next_acknowledgement
    response = await call_next(request)
    if (
        _drop_next_acknowledgement
        and request.method == "POST"
        and request.url.path.endswith("/turns")
        and response.status_code == 202
    ):
        _drop_next_acknowledgement = False
        return Response(status_code=202, headers={"Cache-Control": "no-store"})
    return response


@asynccontextmanager
async def browser_lifespan(application: FastAPI) -> AsyncGenerator[None]:
    async with app_lifespan(application):
        engine: AsyncEngine = application.state.database_engine
        stop = asyncio.Event()
        worker = asyncio.create_task(
            run_workspace_chat_worker(settings, async_sessionmaker(engine), stop)
        )
        try:
            yield
        finally:
            stop.set()
            await worker


def synthetic_user(request: httpx.Request) -> httpx.Response:
    if request.headers.get("authorization") != f"Bearer {os.environ['WORKSPACE_BROWSER_TOKEN']}":
        return httpx.Response(401)
    return httpx.Response(
        200,
        json={
            "id": os.environ["WORKSPACE_BROWSER_ACCOUNT"],
            "email": "owner@example.com",
            "aud": "authenticated",
            "role": "authenticated",
            "email_confirmed_at": "2026-11-19T00:00:00Z",
            "is_anonymous": False,
        },
    )


async def verifier() -> AsyncGenerator[SupabaseIdentityVerifier]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(synthetic_user)) as client:
        yield SupabaseIdentityVerifier(client, url=AUTH_URL, publishable_key=KEY)


app.router.lifespan_context = browser_lifespan
app.dependency_overrides[get_workspace_identity_verifier] = verifier
