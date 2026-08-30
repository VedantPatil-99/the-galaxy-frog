"""Deterministic OpenAPI export for generated frontend contracts."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from galaxy_frog.api.app import create_app
from galaxy_frog.config import Settings

DEFAULT_OUTPUT = Path("openapi.json")


def build_openapi_document() -> dict[str, JsonValue]:
    """Build the API schema without reading local environment configuration."""

    settings = Settings.model_validate({"app_env": "test", "database_url": None})
    application = create_app(settings)
    return cast(dict[str, JsonValue], application.openapi())


def render_openapi_document() -> str:
    """Serialize the API schema in a stable, reviewable representation."""

    return (
        json.dumps(
            build_openapi_document(),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def export_openapi(output: Path = DEFAULT_OUTPUT) -> None:
    """Write the deterministic OpenAPI document to disk."""

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_openapi_document(), encoding="utf-8", newline="\n")


def openapi_is_current(output: Path = DEFAULT_OUTPUT) -> bool:
    """Return whether the committed document matches the current FastAPI schema."""

    if not output.is_file():
        return False
    return output.read_text(encoding="utf-8") == render_openapi_document()


def main(argv: Sequence[str] | None = None) -> int:
    """Export the schema or fail when the committed artifact is stale."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the output is stale")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    output = cast(Path, arguments.output)

    if cast(bool, arguments.check):
        if openapi_is_current(output):
            return 0
        print(f"OpenAPI artifact is stale: {output}", file=sys.stderr)
        return 1

    export_openapi(output)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the module CLI
    raise SystemExit(main())
