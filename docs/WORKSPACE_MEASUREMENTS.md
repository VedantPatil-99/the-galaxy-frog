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
