# ADR 0001: Start as a modular monolith

- Status: Accepted
- Date: 2026-08-29

## Context

Galaxy Frog will eventually coordinate ingestion, retrieval, background work, and several inference providers. Splitting those concerns into network services now would add deployment, tracing, authentication, and failure complexity before workload measurements exist.

## Decision

Use one monorepo and a modular Python backend. Keep module and adapter boundaries explicit so background workers or inference services can be extracted later without changing the domain contract.

## Consequences

- Local development and debugging stay simple.
- Transactions and typed refactors are easier.
- Modules must enforce dependency direction through code structure and tests.
- Service extraction requires evidence such as independent scaling, isolation, or deployment needs.
