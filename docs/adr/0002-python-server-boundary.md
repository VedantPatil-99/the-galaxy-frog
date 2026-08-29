# ADR 0002: Keep all server and AI logic in Python

- Status: Accepted
- Date: 2026-08-29

## Context

The product benefits from Next.js and React for its interactive video interface, while video processing, retrieval, evaluation, and AI ecosystems are strongest in Python. Duplicating server behavior across TypeScript and Python would create two configuration and error models.

## Decision

Use TypeScript only for the presentation tier and thin request proxy. FastAPI owns application behavior, data access, jobs, AI orchestration, and provider integration. Next.js does not directly call AI providers or PostgreSQL.

## Consequences

- One server-side language and dependency graph.
- Browser code remains type-safe through generated OpenAPI contracts.
- Some Next.js conveniences are intentionally not used when they would move business logic into JavaScript.
