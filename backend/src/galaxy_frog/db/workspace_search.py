"""Indexed keyword candidates with owner-scoped evidence locations."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

_OWNED = """
    owned AS MATERIALIZED (
        SELECT id FROM workspace_notebooks WHERE id = :notebook_id AND owner_id = :owner_id
    )
    """

# The optional predicates are fixed in code. Keyword search can use the source indexes
# before expanding blocks. Semantic retrieval reads the same original evidence coordinates.
_SECTIONS = """
    sections AS (
        SELECT 'text' AS kind, s.id AS source_id, s.version_id, 1 AS section_number,
            s.text AS content, s.spans
        FROM workspace_text_sources s JOIN owned o ON o.id = s.notebook_id
        WHERE true {text_filter}
        UNION ALL
        SELECT 'document', d.id, d.version_id, CAST(p.number AS int),
            p.section ->> 'text', p.section -> 'spans'
        FROM workspace_documents d JOIN owned o ON o.id = d.notebook_id
        CROSS JOIN LATERAL jsonb_array_elements(d.sections) WITH ORDINALITY AS p(section, number)
        WHERE true {document_filter}
        UNION ALL
        SELECT 'page_ocr', d.id, d.version_id, p.page_number, p.text, p.spans
        FROM workspace_page_ocr p JOIN workspace_documents d ON d.version_id = p.version_id
        JOIN owned o ON o.id = d.notebook_id
        WHERE p.status = 'succeeded' {ocr_filter}
    ), blocks AS (
        SELECT s.*, CAST(span ->> 0 AS int) AS start_char, CAST(span ->> 1 AS int) AS end_char
        FROM sections s CROSS JOIN LATERAL jsonb_array_elements(s.spans) AS span
    )
    """
_BLOCK_PASSAGES = """
    SELECT b.kind, b.source_id, b.version_id, b.section_number, b.start_char, b.end_char,
        x.excerpt AS text, NULL::text AS unit_id, NULL::bigint AS start_ms, NULL::bigint AS end_ms
        {ranks}
    FROM blocks b
    CROSS JOIN LATERAL (
        SELECT substring(b.content FROM b.start_char + 1 FOR b.end_char - b.start_char) AS excerpt
    ) x
    {terms}
    """
_VIDEO_PASSAGES = """
    SELECT 'video', u.video_id, NULL::uuid, NULL::int, NULL::int, NULL::int,
        u.text, u.id, u.start_ms, u.end_ms {ranks}
    FROM retrieval_units u JOIN workspace_legacy_sources s ON s.video_id = u.video_id
    JOIN owned o ON o.id = s.notebook_id
    {terms}
    WHERE EXISTS (
            SELECT 1 FROM retrieval_unit_cues l JOIN transcript_cues c ON c.id = l.cue_id
            WHERE l.retrieval_unit_id = u.id AND c.video_id = u.video_id
    ) {video_filter}
    """
WORKSPACE_EVIDENCE_PROJECTION = """
    SELECT m.*, CASE WHEN d.format = 'pdf' THEN m.section_number END AS page_number,
        CASE m.kind
            WHEN 'video' THEN jsonb_build_object(
                'video_id', v.id, 'source_kind', v.source_kind, 'title', v.title,
                'duration_ms', v.duration_ms,
                'canonical_url', CASE WHEN v.source_kind = 'youtube' THEN v.canonical_url END
            )
            WHEN 'text' THEN jsonb_build_object(
                'source_id', s.id, 'version_id', s.version_id, 'title', s.title,
                'format', s.format, 'created_at', s.created_at,
                'block_count', jsonb_array_length(s.spans)
            )
            ELSE jsonb_build_object(
                'source_id', d.id, 'version_id', d.version_id, 'filename', d.filename,
                'format', d.format, 'created_at', d.created_at, 'size_bytes', d.size_bytes,
                'section_count', d.section_count, 'pages_without_text', d.pages_without_text
            )
        END AS source,
        (SELECT jsonb_agg(jsonb_build_object(
            'cue_id', c.id, 'origin', c.origin, 'track_id', c.track_id,
            'transcription_run_id', c.transcription_run_id
        ) ORDER BY l.cue_order)
        FROM retrieval_unit_cues l JOIN transcript_cues c ON c.id = l.cue_id
        WHERE l.retrieval_unit_id = m.unit_id AND c.video_id = m.source_id) AS cues
    FROM matches m
    LEFT JOIN videos v ON v.id = m.source_id AND m.kind = 'video'
    LEFT JOIN workspace_text_sources s ON s.id = m.source_id AND m.kind = 'text'
    LEFT JOIN workspace_documents d ON d.id = m.source_id AND m.kind IN ('document', 'page_ocr')
    """

_KEYWORD_SECTIONS = _SECTIONS.format(
    text_filter="AND to_tsvector('simple'::regconfig, s.text) @@ (SELECT value FROM terms)",
    document_filter="""AND jsonb_to_tsvector('simple'::regconfig, d.sections, '["string"]'::jsonb)
                       @@ (SELECT value FROM terms)""",
    ocr_filter="AND to_tsvector('simple'::regconfig, p.text) @@ (SELECT value FROM terms)",
)
_KEYWORD_BLOCK_PASSAGES = _BLOCK_PASSAGES.format(
    ranks=", ts_rank_cd(v.vector, t.value, 32) AS rank",
    terms="""CROSS JOIN terms t
        CROSS JOIN LATERAL (SELECT to_tsvector('simple'::regconfig, x.excerpt) AS vector) v
        WHERE v.vector @@ t.value""",
)
_KEYWORD_VIDEO_PASSAGES = _VIDEO_PASSAGES.format(
    ranks=", ts_rank_cd(u.search_vector, t.value, 32)",
    terms="CROSS JOIN terms t",
    video_filter="AND u.search_vector @@ t.value",
)
_SEARCH = text(f"""
    WITH {_OWNED}, terms AS (SELECT plainto_tsquery('simple'::regconfig, :query) AS value),
    {_KEYWORD_SECTIONS}, candidates AS (
        {_KEYWORD_BLOCK_PASSAGES}
        UNION ALL
        {_KEYWORD_VIDEO_PASSAGES}
    ), matches AS MATERIALIZED (
        SELECT * FROM candidates
        ORDER BY rank DESC, source_id, section_number, start_char, unit_id LIMIT 20
    )
    {WORKSPACE_EVIDENCE_PROJECTION}
    ORDER BY m.rank DESC, m.source_id, m.section_number, m.start_char, m.unit_id
    """)

WORKSPACE_PASSAGE_INVENTORY = f"""
    WITH {_OWNED}, {_SECTIONS.format(text_filter="", document_filter="", ocr_filter="")},
    candidates AS (
        {_BLOCK_PASSAGES.format(ranks="", terms="")}
        UNION ALL
        {_VIDEO_PASSAGES.format(ranks="", terms="", video_filter="")}
    ), passages AS (
        SELECT c.*, encode(sha256(convert_to(CAST(jsonb_build_array(
            c.kind, c.source_id, c.version_id, c.section_number, c.start_char, c.end_char,
            c.unit_id, c.start_ms, c.end_ms, c.text
        ) AS text), 'UTF8')), 'hex') AS passage_key
        FROM candidates c
    )
    """


async def search_workspace_sources(
    session: AsyncSession, owner_id: UUID, notebook_id: UUID, query: str
) -> Sequence[RowMapping]:
    rows = await session.execute(
        _SEARCH, {"owner_id": owner_id, "notebook_id": notebook_id, "query": query}
    )
    return rows.mappings().all()
