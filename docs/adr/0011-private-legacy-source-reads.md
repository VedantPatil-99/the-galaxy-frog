# ADR 0011 — Notebook ownership for legacy video sources

Date 2026-11-20

W1.3 stores an explicit assignment from each selected legacy video to one existing notebook.
PostgreSQL enforces one assignment per video, foreign keys to both records, and RLS without
browser policies. The application checks the verified creator against the notebook owner and
checks membership before reading any evidence or file. Missing and inaccessible sources
return the same 404 response.

An operator supplies an owner ID, notebook ID, and exact video IDs. The import command
previews the transaction by default. Reapplying the same assignment is safe, and a missing
video or conflicting assignment rolls back the batch. Creating a user or notebook grants
access to zero legacy videos. The migration creates an empty table without rewriting existing
IDs, intervals, transcripts, embeddings, frames, or Study artifacts.

The private API serves notebook source listings, transcripts, and frame previews. It uses
the original evidence projection and omits local file URLs. The old global routes stay
locked in private mode, including global retrieval, Study, job, and provider routes.
When notebook-scoped versions are added, each must enforce its own resource authorization.
Existing trusted local development mode still uses its original routes.

The local preview implementation reads a bounded JPEG from the assigned video's directory.
An opt-in Supabase implementation checks that the fixed preview bucket is private, then
reads an object addressed only by stored video and frame IDs. FastAPI uses a dedicated
server credential, which carries elevated Supabase access, and checks ownership before
calling Storage. The endpoint returns bytes with `no-store` and `nosniff`. Bucket paths,
server credentials, and download links stay inside FastAPI. A remote failure has an
explicit error response and does not switch storage.

Hosted activation requires a provisioned private bucket with no browser policies and
a server-only secret. In the live acceptance test, local FastAPI used two synthetic
application identities. The owner received the exact hosted JPEG and the other
identity and anonymous callers were denied. A publishable key could not read the
object directly. On 2026-11-21, the Storage dashboard confirmed the private bucket
had no bucket-specific or schema-wide read policies. The test fixture was removed.
Uploads, originals, exports, and public sharing follow in their respective packets.
