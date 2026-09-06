"""Filesystem isolation for retryable local media work."""

import shutil
from pathlib import Path
from uuid import UUID

from galaxy_frog.domain.media import AudioAcquisitionError, AudioAcquisitionErrorCode


class IsolatedMediaWorkspace:
    """Own only job-attempt directories beneath one configured media root."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, job_id: UUID, attempt: int) -> Path:
        """Return the deterministic absolute directory for one job attempt."""

        if attempt < 1:
            msg = "attempt must be positive"
            raise ValueError(msg)
        return self._root / str(job_id) / f"attempt-{attempt}"

    def prepare(self, job_id: UUID, attempt: int) -> Path:
        """Create a clean attempt directory without deleting outside the media root."""

        workspace = self.path_for(job_id, attempt)
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            if workspace.exists() or workspace.is_symlink():
                self.remove(workspace)
            workspace.mkdir(parents=True, exist_ok=False)
        except AudioAcquisitionError:
            raise
        except OSError as exc:
            raise self._workspace_error() from exc
        return workspace

    def remove(self, workspace: Path) -> bool:
        """Remove one validated attempt directory and its empty job parent."""

        candidate = workspace.resolve()
        if not self._is_owned_attempt(candidate):
            raise self._workspace_error()
        if not candidate.exists():
            return False
        try:
            shutil.rmtree(candidate)
            job_directory = candidate.parent
            if job_directory.exists() and not any(job_directory.iterdir()):
                job_directory.rmdir()
        except OSError as exc:
            raise self._workspace_error() from exc
        return True

    def _is_owned_attempt(self, candidate: Path) -> bool:
        if not candidate.is_relative_to(self._root):
            return False
        relative = candidate.relative_to(self._root)
        if len(relative.parts) != 2:
            return False
        job_part, attempt_part = relative.parts
        try:
            UUID(job_part)
        except ValueError:
            return False
        if not attempt_part.startswith("attempt-"):
            return False
        try:
            return int(attempt_part.removeprefix("attempt-")) >= 1
        except ValueError:
            return False

    @staticmethod
    def _workspace_error() -> AudioAcquisitionError:
        return AudioAcquisitionError(
            AudioAcquisitionErrorCode.WORKSPACE_ERROR,
            "The isolated media workspace could not be prepared or cleaned.",
        )
