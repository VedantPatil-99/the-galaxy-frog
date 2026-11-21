from uuid import uuid4

import pytest

from galaxy_frog.domain.workspace_text import (
    WorkspaceTextBlock,
    WorkspaceTextRejected,
    prepare_workspace_text,
)


def test_text_blocks_preserve_exact_unicode_slices_and_source_version() -> None:
    source = prepare_workspace_text("## Glaciers\r\nWhy ice moves.\r\n\r\n  🧊 A second note.")
    assert source.text == "## Glaciers\nWhy ice moves.\n\n  🧊 A second note."
    assert source.content_sha256 == prepare_workspace_text(source.text).content_sha256
    version_id = uuid4()
    blocks = [
        WorkspaceTextBlock.from_span(version_id, source.text, start, end)
        for start, end in source.spans
    ]
    assert [block.text for block in blocks] == [
        "## Glaciers\nWhy ice moves.",
        "  🧊 A second note.",
    ]
    for block in blocks:
        assert block.text == source.text[block.start_char : block.end_char]
        assert block == WorkspaceTextBlock.from_span(
            version_id, source.text, block.start_char, block.end_char
        )
        assert (
            block.block_id
            != WorkspaceTextBlock.from_span(
                uuid4(), source.text, block.start_char, block.end_char
            ).block_id
        )


def test_long_paragraphs_are_split_without_losing_coordinates() -> None:
    source = prepare_workspace_text("x" * 4_097)
    assert source.spans == ((0, 4096), (4096, 4097))


def test_whitespace_only_chunks_do_not_become_evidence() -> None:
    source = prepare_workspace_text(" " * 4096 + "Content")
    assert source.spans == ((4096, 4103),)


@pytest.mark.parametrize(
    "text",
    [" \n\t", "invalid\0value", "\ud800", "x" * 65_537, "🧊" * 33_000, "x\n\n" * 2_049],
    ids=["blank", "null", "invalid-unicode", "character-limit", "byte-limit", "block-limit"],
)
def test_unusable_pastes_are_rejected(text: str) -> None:
    with pytest.raises(WorkspaceTextRejected):
        prepare_workspace_text(text)
