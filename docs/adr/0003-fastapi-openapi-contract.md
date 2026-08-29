# ADR 0003: Generate frontend contracts from FastAPI OpenAPI

- Status: Accepted
- Date: 2026-08-29

## Context

Hand-maintaining Pydantic models and TypeScript API interfaces would allow drift and make errors visible only at runtime.

## Decision

FastAPI and Pydantic are the canonical HTTP contract. Export a deterministic OpenAPI document and generate TypeScript types for the frontend. CI will fail when generated output is stale.

Structured failures use a stable envelope with an error code, safe message, correlation ID, retryability indicator, and optional details.

## Consequences

- One source of truth for request, response, and error schemas.
- Contract changes become reviewable generated diffs.
- Generation must be deterministic and part of both local workflow and CI.
