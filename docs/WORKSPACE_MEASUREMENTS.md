# Galaxy Frog workspace measurements

Add an entry when an active feature is implemented or tested. Record the date, feature,
environment, fixture or workload, sample count, and measurement boundary. Include input
size, page or source counts, or media duration when they explain the result. Break down
measured stages and explain observed bottlenecks when there is evidence for them.

Record throughput over the actual observation window. Record accuracy with the metric,
denominator, and labeled ground truth. Distinguish browser, API, database, and provider
timings. Label simulations and unavailable values. Keep private queries and source
content out of this log.

## 2026-11-22 · W3.4 notebook keyword search

- **Workload** — Local Windows FastAPI with in-process HTTP, simulated authentication,
  and real PostgreSQL 17. The notebook had two assigned video records, including a
  one-second transcript fixture with one cue and one retrieval unit, a pasted text
  source, a two-page PDF with one saved OCR page, and one DOCX. The searchable text
  totaled 200 Unicode characters. No media download, document parsing, or cloud call
  was part of the timed path.
- **Sample** — 30 serial requests after a warm-up. Six labeled queries ran five times
  each. Nearest-rank p95 uses the 29th result of the 30 samples.
- **Latency** — API request median 10.50 ms and p95 17.44 ms, measured through ASGI
  from client submission until the response was received. SQL execution and fetching
  rows took a median 1.52 ms and p95 5.32 ms. The API measurement also includes mock
  authentication, ownership checks, evidence projection, and response handling.
- **Throughput** — 86.49 requests/s over a 0.3469 s serial observation window that
  includes the fixture assertions. This sample describes the fixture at concurrency
  one. Hosted capacity and large notebooks still need measurements.
- **Accuracy** — All 40 expected matches were returned with zero extra matches
  across the labeled repetitions (precision 40/40, recall 40/40). Ten expected
  empty responses remained empty. Five citation checks verified the stored video
  interval and cue lineage, pasted block, PDF page, DOCX block, and saved OCR block.
  The fixture evaluates keyword matches and citation integrity.
- **Browser** — The search badge showed 218 ms in one local Chrome run against a
  fixture with a deliberate 200 ms response delay. The browser check confirmed
  the badge reflected the wait, the OCR match opened page 2, and the layout reflowed
  at 390 px.

Reproduce the local API measurement with the current migrations applied.

```bash
RUN_DATABASE_INTEGRATION=1 uv run --directory backend python -m pytest -s --no-cov \
  tests/integration/test_workspace_sources.py::test_keyword_search_opens_owned_evidence_and_measures_labeled_queries
```

## 2026-11-22 · W3.5 answers from selected passages

- **API fixture** — Eight warm serial requests through local FastAPI and PostgreSQL, with
  simulated authentication and a simulated model. The three selected passages totaled
  112 characters across a one-second video record, one pasted note, and a one-page PDF.
- **Median elapsed time** — 27.04 ms for the API request, 3.64 ms for reloading and preparing
  evidence, 11.71 ms for the simulated generation stage, and 0.08 ms for citation validation.
  The API measured additional request, authorization, and response handling around those stages.
- **Citation checks** — All 24 expected citations resolved to their selected original evidence.
  The same fixture rejected drafts containing an unknown passage reference or a fabricated quote.
- **Browser fixture** — The answer badge showed 277 ms with a deliberate 250 ms response delay.
  Headless Chrome checked that passage selection was required, the citation opened its exact
  PDF page, focus moved to the preview, and the layout reflowed at 390 px.

### Installed model on the local Windows machine

Three serial calls used Ollama 0.40.13 with `qwen3:4b`. Each repeated the same synthetic question
and three passages totaling 116 characters from a three-second video record, one PDF page,
and a pasted note. These timings cover the adapter's HTTP round trip and a separate citation
check. The API fixture above measures the notebook database path.

- **First call after process start** — 11.84 s round trip. Ollama reported 6.17 s for model load,
  258 ms for prompt evaluation, and 5.37 s for generating 123 output tokens. Citation validation
  took 1.83 ms.
- **Two warm calls** — 5.46 s and 5.65 s round trips. Reported load took 5.4–6.6 ms,
  prompt evaluation took 68–70 ms, and decoding took 5.36–5.54 s. That is 22.2–23.0
  output tokens/s, calculated from 123 tokens and each call's decoding duration. Citation
  validation took 0.034–0.042 ms.
- **Fixture accuracy** — All three answers included both labeled facts and six citations
  in total validated. The citations pointed to the original video unit and PDF page.
  This sample measures one repeated question with known answers.
  [Ollama timing fields](https://docs.ollama.com/api/generate)

Reproduce the API fixture with the current local migrations applied.

```bash
RUN_DATABASE_INTEGRATION=1 uv run --directory backend python -m pytest -s --no-cov \
  tests/integration/test_workspace_sources.py::test_workspace_answer_validates_selected_passages_and_times_each_stage
```

## 2026-11-22 · W3.6 local semantic indexing and search

- **Database and API fixture** — Real local PostgreSQL 17 with simulated authentication and
  deterministic vectors. The notebook held 18 passages across a one-second video, a two-page
  PDF with one saved OCR page, and a pasted note. Two index batches saved 16 and 2 passages.
- **Index stages** — The two API calls took 99.08 ms and 58.19 ms. Preparation took 11.15 ms
  and 13.41 ms, the simulated embedding stage took 21.19 ms and 11.52 ms, and database writes
  took 48.61 ms and 14.22 ms. Remaining elapsed time includes request handling and coverage.
- **Retrieval** — Twelve warm API queries had a 31.77 ms median request time, with 10.33 ms
  in the simulated embedding stage and 9.63 ms for the database query and collection check.
  All 12 labeled targets ranked first. Each returned page or video link retained its original
  evidence. This fixture measures the database path with controlled vectors.
- **Installed model** — A separate local Ollama run used 1,024-dimensional `bge-m3:latest`
  (digest prefix `790764642607`). Two batches reused the same eight synthetic passages,
  totaling 377 characters. Embedding took 3.87 s on the first call and 154 ms on the warm call,
  or 2.1 and 51.8 passages/s respectively. Eight warm query embeddings took a median 42.04 ms.
  Each of the four labeled paraphrases ran twice. The expected passage ranked first in all
  eight trials, with mean reciprocal rank 1.0. This model sample measures embedding and ranking
  on a small labeled corpus.
- **Browser fixture** — The indexing and search badges showed 204 ms and 188 ms against
  a local fixture with 180 ms of deliberate wait in each response. The browser checked
  coverage before enabling search and opened the returned OCR passage on its original page.

Reproduce the database fixture with the current local migrations applied.

```bash
RUN_DATABASE_INTEGRATION=1 uv run --directory backend python -m pytest -s --no-cov \
  tests/integration/test_workspace_sources.py::test_local_semantic_index_preserves_sources_and_model_revision
```
