# Architecture overview

## Style

Galaxy Frog begins as a modular monolith in one repository. Deployment boundaries remain clear without paying the operational cost of microservices before load and failure measurements justify them.

```mermaid
flowchart TD
    UI["Next.js web<br>presentation only"] -->|"thin proxy + generated types"| API["FastAPI<br>application boundary"]
    API --> CORE["Python domain modules"]
    CORE --> DATA["PostgreSQL + pgvector"]
    CORE --> PROVIDERS["Replaceable provider adapters"]
    PROVIDERS --> YOUTUBE["YouTube metadata + captions"]
    PROVIDERS --> MEDIA["Bounded yt-dlp + FFmpeg audio"]
    PROVIDERS --> ASR["Local faster-whisper<br>multilingual ASR"]
    PROVIDERS --> OLLAMA["User-managed Ollama<br>BGE-M3 + Qwen3 4B"]
```

## Repository modules

| Path | Responsibility | Must not contain |
|---|---|---|
| `apps/web` | UI, browser state, player controls, proxy handlers | AI, retrieval, parsing, database queries |
| `backend` | API, domain services, persistence, jobs, AI orchestration | Browser UI behavior |
| `infra` | Local infrastructure and later deployment definitions | Business rules |
| `scripts` | Deterministic repository automation | Long-running services |
| `docs` | Architecture, scope, ADRs, and runbooks | Secrets or generated artifacts |

## Dependency direction

- HTTP handlers depend on application services, never the reverse.
- Domain services depend on protocols, not provider SDK clients.
- Infrastructure adapters implement domain-facing protocols.
- Configuration selects adapters at startup and validates invalid combinations early.
- Frontend code depends on generated API contracts, not backend implementation details.

## API contract generation

FastAPI and Pydantic are the sole source of truth for HTTP schemas. The backend exports a sorted,
stable `backend/openapi.json` without loading local environment configuration or connecting to
PostgreSQL. `openapi-typescript` derives `apps/web/lib/api/generated/schema.d.ts` from that document.
Both files are committed, and `bun run contracts:check` fails when either artifact is stale.

## Browser-to-API boundary

The browser calls FastAPI only through the same-origin Next.js catch-all proxy. Its upstream origin
comes exclusively from the server-only `FASTAPI_BASE_URL`; caller-controlled hosts and path traversal
are rejected. The proxy streams request and response bodies, forwards only required headers,
preserves status and correlation metadata, uses a bounded timeout, and disables response caching.

Interactive browser code consumes generated OpenAPI types and performs small runtime shape checks at
the untrusted JSON boundary. Next.js owns presentation and transport adaptation only; it does not
reimplement Python application behavior.

## Failure model

Every API failure will expose a stable error code, human-safe message, correlation ID, retry guidance, and optional structured details. Provider fallback decisions will be visible in telemetry and response metadata; a silent quality downgrade is not acceptable.

FastAPI validates caller-provided correlation IDs, generates a UUID when one is absent or unsafe,
and returns the identifier in both `X-Correlation-ID` and structured error bodies. Validation,
framework HTTP, deliberate application, and unexpected failures all use the same envelope.

`/health/live` proves only that the API process can respond. `/health/ready` executes a PostgreSQL
query through an application-lifespan engine; database failure must never make liveness fail.

Provider availability is reported by the import/question operation that requires it rather than by
database readiness. A missing embedder or generator produces a stable, correlated API failure; it
does not silently change the model or answer quality.

## Planned Phase 8 provider policy boundary

Phase 8 adds product-facing provider configuration without moving provider policy into Next.js.
FastAPI will expose capability, health and versioned profile schemas through OpenAPI; the frontend
will consume the generated types, select a profile and render the resulting decision. Provider SDK
clients, credentials, availability probes, compatibility rules, quota state, fallback resolution
and cost calculations remain Python responsibilities.

The policy supports three explicit modes:

1. `automatic_local_only` is the default and considers only healthy, compatible local providers.
2. `automatic_cloud_permitted` may consider cloud providers only when the request includes current,
   explicit cloud-processing consent.
3. `manual` pins a provider/model and either disables fallback or supplies an ordered compatible
   fallback chain.

Cloud consent is independent from the selected mode, defaults to denied and cannot be inferred from
configured credentials. Losing a local provider, exhausting a quota or timing out never authorizes
uploading source media or evidence. Strict manual selection returns a recoverable error rather than
substituting a provider silently. Evaluation profiles pin the complete provider configuration and
disable automatic fallback.

Every provider run produces a durable decision record containing the requested mode/profile/model,
the resolved provider/model/revision/device, language and capability requirements, the selection
reason, ordered fallback attempts and reasons, input duration, processing time, estimated or actual
cost when reported, and pricing provenance (`source_url`, billing unit, currency and `verified_at`).
Pricing metadata is explanatory and can be marked stale; the provider's bill remains authoritative.

The UI presents model capabilities and the durable decision record. Provider cards show local or
cloud execution, language and code-switching coverage, privacy implications, expected quality,
measured speed, hardware needs, timestamp/confidence/diarization support and current availability.
Job details distinguish requested from actual execution and explain every fallback. None of these
presentation paths may discard or rewrite the original half-open timestamp intervals, source IDs,
cue links or evidence provenance.

Phase 2 prepares only the execution evidence needed by this later policy: its progress UI may show
the actual ASR provider, model, revision, device, timing and fallback reason as read-only data. The
interactive selector, editable profiles, cloud-consent controls and pricing presentation remain
Phase 8 work.

## Phase 1 transcript path

1. FastAPI canonicalizes an allowlisted YouTube URL and retrieves safe metadata and captions only.
2. Ordered transcript cues retain source identity and half-open millisecond intervals.
3. Deterministic retrieval units retain their contributing cue IDs.
4. BGE-M3 vectors are stored in a versioned 1,024-dimensional collection.
5. Video-scoped cosine retrieval returns units with complete cue provenance.
6. Qwen3 receives only retrieved transcript evidence; FastAPI validates every returned citation.
7. The browser renders evidence and asks the YouTube IFrame player to seek to validated timestamps.

## Phase 1 deployment view

The runnable local services are the Next.js development server, FastAPI development server,
PostgreSQL/pgvector container, and user-managed native Ollama service. Phase 1 has no worker, durable
job queue, media pipeline, ASR, OCR, visual retrieval, hybrid retrieval, reranker, or cloud provider.

## Phase 2 durable ingestion foundation

Phase 2 introduces a durable job boundary without moving business rules out of the Python modular
monolith:

1. A canonical source locator, normalized language preferences, and pipeline revision produce a
   deterministic SHA-256 input fingerprint.
2. PostgreSQL stores one idempotent `ingestion_jobs` projection per fingerprint and an append-only,
   per-job sequence of `job_events`.
3. Workers claim jobs through row locking with bounded leases, heartbeat while running, and may
   reclaim only expired work.
4. Every stage transition and reusable media/transcription output is committed as a checkpoint
   before the worker proceeds, so restart recovery does not depend on process memory.
5. Cancellation, retryability, safe error codes, attempt count, worker ownership, and lease expiry
   are explicit persisted state rather than implicit queue behavior.

The application layer owns the job lifecycle through provider-independent protocols. The initial
stage runner is transport-neutral: local PostgreSQL polling remains the development default, while
an optional QStash adapter may carry identifiers only and cannot become the source of truth. The
Phase 2 import API queues or reuses a durable job and returns immediately; the frontend declarations
for that asynchronous contract are generated from FastAPI OpenAPI.

The durable stage vocabulary is deliberately limited to Phase 2 ingestion work. It preserves video
identity and every caption or ASR cue's half-open millisecond interval and source provenance; it does
not introduce OCR, visual retrieval, hybrid retrieval, reranking, or LangGraph.

## Phase 2 bounded audio foundation

P2.5 introduced the local media boundary before P2.7 activated it, without moving media work into
the HTTP request:

1. An audio request cannot exist without a durable job/attempt, canonical source, expected duration,
   and an explicit `captions_unavailable` or `captions_unusable` fallback reason.
2. A local acquirer caps the complete operation, including concurrency wait, download, inspection,
   and normalization, with one deadline and one shared semaphore.
3. Every attempt owns only `tmp/media/{job_id}/attempt-{attempt}`. Cleanup validates this exact shape
   beneath the configured root before recursive deletion and never accepts a provider-returned path
   outside the attempt directory.
4. yt-dlp receives an argument array, single-video mode, duration filter, source-size ceiling,
   bounded retries/socket timeout, and an output template controlled by the worker.
5. ffprobe checks source duration and size before FFmpeg emits one mono 16 kHz `pcm_s16le` WAV;
   ffprobe then verifies codec, sample rate, channels, duration, and size again.
6. The resulting artifact preserves canonical source identity, fallback reason, the half-open
   `[0, duration_ms)` interval, acquisition time, and yt-dlp/FFmpeg revisions.
7. Failed or cancelled attempts clean their workspace immediately. After transcription,
   persistence, and indexing succeed, cleanup removes the artifact unless retention is configured.

## Phase 2 resumable caption-to-ASR path

P2.7 connects the provider-neutral media and transcription boundaries through durable evidence:

1. Caption retrieval selects and validates a track first. Only an unavailable or unusable transcript
   records an explicit fallback reason and advances to audio acquisition.
2. `media_assets` stores the attempt, exact source interval, local lifecycle state, and
   downloader/normalizer revisions. Filesystem paths remain internal and never enter job events or
   HTTP responses.
3. `transcription_runs` stores one result per job with its media identity, provider/model revisions,
   device/compute type, language evidence, processing time, and fallback reason.
4. `transcription_run_cues` stores the provider's ordered integer-millisecond intervals and optional
   confidence method before final video persistence.
5. Final `transcript_cues` identify their caption origin or link to exactly one transcription run;
   retrieval units retain the ordered cue IDs and reconstructed interval as before.
6. A failure before inference output resumes at transcription using retained audio. A failure after
   the transcription checkpoint reuses that run. Unique constraints prevent a second run or cue set.
7. Successful cleanup marks the media record deleted only after the transcript is persisted and
   indexed, preserving the database lineage after the local file is gone.

FastAPI owns the safe transcript execution schema and generated browser declarations. Next.js may
display the actual provider, model, revision, device, timing, confidence, and fallback reason, but it
receives no local media path and cannot select or execute a provider during Phase 2.

## Phase 2 progress and recovery presentation

P2.8 keeps the browser as a generated-contract consumer. The import response supplies the first
durable job projection; the client polls job detail, refreshes ordered events, and derives labels and
progress only from FastAPI-owned enum values. Cancellation and retry post to FastAPI through the same
thin proxy. The UI never mutates a stage locally, and it offers retry only when the returned job marks
the last failure retryable.

After success, the transcript response remains the evidence source. Caption-backed results state
that ASR was skipped. ASR-backed results present the actual run/provider/model revisions,
device/compute type, language confidence, processing time, source interval, caption-fallback reason,
and cue confidence. These are read-only facts about what ran, not a provider-configuration surface.
The existing player, grounded-question, retrieval evidence, and citation-seeking paths remain
unchanged.

## Phase 2 operational visibility

P2.9 keeps append-only PostgreSQL job events as the authoritative operational history and treats
process logs as diagnostics. The standalone worker enables INFO output and emits both visible
key-value messages and structured record fields for its fixed provider/model revision, device and
compute mode, lease/poll settings, media limits, retention policy, claims, stage transitions,
retryability, and shutdown. Dispatcher acceptance/failure uses the same safe pattern. Logs exclude
credentials, raw provider exceptions, source titles, transcript text, and local media paths.

Only an explicit allowlist of stage details can enter diagnostic logs. It includes evidence IDs,
exact intervals, cue counts, provider/tool revisions, language/confidence methods, fallback reason,
reuse, measured processing time, and cleanup retention. The browser derives its compact decision
summaries from the persisted FastAPI event contract rather than process logs. Native faster-whisper
and its binary dependencies are imported only when a transcription requests the model, so API,
worker wiring, and non-ASR tests do not initialize CUDA or NumPy.

## Phase 3 lexical retrieval foundation

P3.1 adds an independently callable PostgreSQL lexical retriever beside the existing dense adapter.
A stored `simple`-dictionary `tsvector` on `retrieval_units.text` backfills existing rows and is
maintained by PostgreSQL for new or changed text. A GIN index supports lexical matching without
rerunning ingestion or adding a provider call. Quoted phrases use PostgreSQL web-search semantics;
the language-neutral dictionary performs token matching, not language-specific stemming or fuzzy
substring matching.

The retriever binds user text, scopes every query by video, ranks with `ts_rank_cd` normalization 32,
and breaks ties by unit ID. Lexical scores are neither answer confidence nor directly comparable to
dense similarity; P3.2 will fuse ranks. Results carry original units and ordered cue links, preserving
caption/ASR lineage through the existing transcript tables. P3.1 does not activate a new public
search or question path. See ADR 0005 for the approved subsequent retrieval boundaries.
## Shared text retrieval (P3.2)

`application/retrieval/text.py` composes the lexical and existing collection-safe dense ports.
Stages run sequentially because adapters may share one `AsyncSession`. Query normalization and
signal detection are deterministic domain functions; no language model classifies queries.
Quoted phrases become the lexical expression while dense retrieval retains the full question.
Temporal signals require explicit resolution before this service can retrieve unrestricted text.

Equal-weight RRF combines ranks with constant 60 and stable unit-ID tie breaking. Scores remain
separate from source scores, and duplicate appearances in one stage cannot boost a unit. Each result
retains original intervals/cues plus bounded stage, fusion, candidate, and evidence records. Only
embedding-provider failures allow an observable lexical fallback in hybrid mode; strict or dense-only
requests fail instead. Database, collection, and evidence-integrity errors propagate.
The current question API adopts this service in P3.5; persistent traces are also deferred to P3.5.

## Local reranker (P3.3)

`TextReranker` is a domain port; `BgeTranscriptReranker` owns a bounded offline CUDA subprocess.
It uses the existing command runner's kill/reap behavior and bounded output collection. The small
standalone runtime needs only torch and Transformers, allowing an explicitly provisioned interpreter
without importing FastAPI or database clients there. Provider output is validated before it reaches
domain results; original text, IDs, intervals, and cue links remain in the retrieval service.

`reranked` mode reranks the fused candidate budget and exposes raw scores separately. Provider
failure preserves fused order only with warnings/degraded status; strict mode fails. Empty evidence
skips the provider. Each request releases model residency on child exit; the resulting load latency
is measured in the [P3.3 verification record](phase-3/p3-3-verification.md).

## Temporal evidence (P3.4)

`RetrieveTemporalEvidence` parses one trailing timestamp/range or named-event constraint, then
passes a half-open `TimeWindow` to both PostgreSQL adapters before ranking/limiting. Pure timestamp
browsing selects up to 30 original units chronologically without embedding or reranking. Unsupported
grammar fails explicitly. Quoted temporal words stay literal. The parser currently uses English
operators; transcript text, dense retrieval, and reranking retain multilingual support.

Named-event resolution records a separate retrieval pass, using fused retrieval for reranked mode.
Literal inspection includes the entire stored transcript, preventing top-k truncation from concealing
ambiguity. Cue intervals locate literal events; cross-cue phrases retain the original unit interval.
Semantic suggestions use ranked, distinct unit intervals and always require user selection. Up to
five choices have stable identities tied to video, event, interval, and original unit IDs. Selection
is revalidated against current server candidates. Unresolved/ambiguous requests return no main pass.

`expand_evidence` merges +/-15 second windows and gaps under five seconds, then selects at most eight
groups by their best seed rank. The 12,000-character context budget reserves whole matching units
before adding neighbors. Original text, cue IDs, cue order, caption/ASR origin, transcription-run ID,
and unit intervals remain unchanged. Display windows are clipped to temporal scope; boundary-crossing
originals and context omissions receive explicit warnings. No generator or HTTP schema is introduced
by this packet. The subsequent API packet persists these records and reuses citation validation.

## Traced search and question API (P3.5)

`SearchTranscript` wraps the temporal service and persists a version-1 record before returning
results. Both public search and question routes use it. The request-scoped database session backs
lexical search, collection-safe dense search, transcript reads, and trace persistence. One app-scoped
reranker object enforces its concurrency bound; generation is constructed only after anchor resolution.

Migration `20260927_0007` adds `retrieval_traces`, indexed by video/time with cascade deletion when
its video is deleted. Python and PostgreSQL enforce a 256 KiB serialized record limit. Trace records
contain query analysis, active configuration, declared providers, separate stage/fusion/reranker
records, timings, fallback reasons, anchor decisions, and original interval/cue/ASR lineage. Stage
and group copies exclude transcript text; anchor snippets are bounded. Oversized records fail with
an explicit code. There is no automatic retention deletion policy; per-record size is bounded.

Search exposes status, anchor choices, evidence groups, warnings, and a durable trace ID. A trace read
filters by both video ID and trace ID. Questions keep prior answer/citation fields and add retrieval
metadata/status. Unresolved anchors produce an empty answer without generation. Resolved questions
reuse original citation validation and additionally require constrained quotes to appear in cues
overlapping the selected scope. Safe generation/citation errors include the already persisted trace ID.

FastAPI OpenAPI owns all new contracts, including the nested original evidence records. The Next.js
proxy only forwards requests; search/question timeouts are five minutes to allow bounded native
startup and generation. The backend provider deadlines remain independent and observable.

### P3.6 evidence presentation

The browser selects retrieval-only versus question submission and presents the generated response.
Anchor IDs return to FastAPI for validation; the client neither resolves events nor ranks evidence.
Merged windows, original cue/unit intervals, caption/ASR lineage, stage scores, fallback warnings,
and saved traces are inspectable. All evidence and anchor preview timestamps reuse the existing
YouTube seek commands. Query/mode/video changes clear stale results, and active requests disable
conflicting submissions. Browser acceptance remains separate from HTTP/component tests.
