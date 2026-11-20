"""Preview or apply explicit legacy video assignments to an existing notebook."""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.workspace_sources import LegacySourceAssignmentError, WorkspaceSourceRepository
from galaxy_frog.domain.workspace import NotebookNotFound


async def assign(
    settings: Settings,
    owner_id: UUID,
    notebook_id: UUID,
    video_ids: Sequence[UUID],
    *,
    apply: bool,
) -> int:
    database_url = settings.database_url
    target = make_url(database_url.get_secret_value()) if database_url is not None else None
    if (
        target is None
        or target.host not in {"localhost", "127.0.0.1", "::1"}
        or "host" in target.query
    ):
        raise LegacySourceAssignmentError("Use the local application PostgreSQL database.")

    engine = create_database_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session:
            count = await WorkspaceSourceRepository(session).assign_legacy_videos(
                owner_id, notebook_id, video_ids
            )
            if apply:
                await session.commit()
            else:
                await session.rollback()
            return count
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", required=True, type=UUID)
    parser.add_argument("--notebook-id", required=True, type=UUID)
    parser.add_argument("--video-id", required=True, type=UUID, action="append")
    parser.add_argument(
        "--apply", action="store_true", help="Commit instead of previewing the batch."
    )
    args = parser.parse_args(argv)
    try:
        count = asyncio.run(
            assign(Settings(), args.owner_id, args.notebook_id, args.video_id, apply=args.apply)
        )
    except LegacySourceAssignmentError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except NotebookNotFound:
        print("The notebook does not exist for the selected owner.", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("Check the local database connection and workspace migrations.", file=sys.stderr)
        return 1
    action = "Assigned" if args.apply else "Would assign"
    print(f"{action} {count} video(s). {len(args.video_id) - count} already assigned here.")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
