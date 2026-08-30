"""Tests for deterministic OpenAPI export."""

import json
from pathlib import Path

import pytest

from galaxy_frog.openapi import (
    build_openapi_document,
    export_openapi,
    main,
    openapi_is_current,
    render_openapi_document,
)


def test_rendered_openapi_is_sorted_and_ends_with_one_newline() -> None:
    rendered = render_openapi_document()

    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")
    assert json.loads(rendered) == build_openapi_document()
    assert rendered.index('"components"') < rendered.index('"info"')


def test_build_openapi_ignores_process_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "invalid")
    monkeypatch.setenv("DATABASE_URL", "not-a-database-url")

    document = build_openapi_document()

    assert document["info"] == {"title": "Galaxy Frog API", "version": "0.1.0"}


def test_export_and_current_check(tmp_path: Path) -> None:
    output = tmp_path / "contracts" / "openapi.json"

    assert openapi_is_current(output) is False

    export_openapi(output)

    assert openapi_is_current(output) is True
    assert output.read_bytes().endswith(b"\n")

    output.write_text("{}\n", encoding="utf-8")
    assert openapi_is_current(output) is False


def test_cli_exports_and_checks_the_document(tmp_path: Path) -> None:
    output = tmp_path / "openapi.json"
    arguments = ["--output", str(output)]

    assert main(arguments) == 0
    assert main([*arguments, "--check"]) == 0


def test_cli_reports_a_stale_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "openapi.json"

    assert main(["--output", str(output), "--check"]) == 1
    assert "OpenAPI artifact is stale" in capsys.readouterr().err
