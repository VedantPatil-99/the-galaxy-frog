# 12. Keyword search over saved notebook evidence

Accepted 2026-11-22.

W3.4 uses the local `postgres-keywords-v1` profile with PostgreSQL's `simple`
text-search configuration. All entered words must occur within the same evidence
block or transcript unit. This profile has no model or provider requirement.

Reuse the video retrieval-unit index. Add expression indexes over saved text,
extracted document sections, and successful page OCR. PostgreSQL maintains them
as source records change and indexes existing records during migration. FastAPI
scopes candidates to the owner and notebook, then ranks exact blocks using the
stored offsets. The source-level indexes can admit candidates with words spread
across blocks. The block filter removes them before ranking.

Preserve the original video interval and cue lineage, PDF page, document section,
and immutable text block identity in the response. Ranks order the first 20 matches.
Semantic profiles and answer citation validation remain separate W3 work.

The API reports elapsed SQL execution and result fetching. The browser displays
its request duration, which includes the wait to receive and validate the response.
Record fixture timings and their workload in [Workspace measurements](../WORKSPACE_MEASUREMENTS.md).
