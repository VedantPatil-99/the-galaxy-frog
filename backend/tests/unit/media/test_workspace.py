"""Behavior coverage for isolated media workspaces."""

from pathlib import Path
from uuid import uuid4

import pytest

from galaxy_frog.adapters.media.workspace import IsolatedMediaWorkspace
from galaxy_frog.domain.media import AudioAcquisitionError, AudioAcquisitionErrorCode


def test_prepare_replaces_only_the_target_attempt_workspace(tmp_path: Path) -> None:
    manager = IsolatedMediaWorkspace(tmp_path / "media")
    job_id = uuid4()
    stale = manager.path_for(job_id, 1)
    stale.mkdir(parents=True)
    (stale / "partial.webm").write_bytes(b"partial")
    other = manager.path_for(job_id, 2)
    other.mkdir(parents=True)
    (other / "keep.webm").write_bytes(b"keep")

    prepared = manager.prepare(job_id, 1)

    assert prepared.is_absolute()
    assert prepared == stale
    assert list(prepared.iterdir()) == []
    assert (other / "keep.webm").read_bytes() == b"keep"


def test_remove_is_idempotent_and_prunes_an_empty_job_directory(tmp_path: Path) -> None:
    manager = IsolatedMediaWorkspace(tmp_path / "media")
    workspace = manager.prepare(uuid4(), 1)
    job_directory = workspace.parent
    (workspace / "audio.wav").write_bytes(b"audio")

    assert manager.remove(workspace) is True
    assert not workspace.exists()
    assert not job_directory.exists()
    assert manager.remove(workspace) is False


@pytest.mark.parametrize(
    "relative",
    [
        Path("outside"),
        Path("not-a-uuid/attempt-1"),
        Path(f"{uuid4()}/other-1"),
        Path(f"{uuid4()}/attempt-zero"),
        Path(f"{uuid4()}/attempt-0"),
        Path(f"{uuid4()}/attempt-1/nested"),
    ],
)
def test_remove_rejects_unowned_or_malformed_paths(tmp_path: Path, relative: Path) -> None:
    manager = IsolatedMediaWorkspace(tmp_path / "media")
    candidate = manager.root / relative

    with pytest.raises(AudioAcquisitionError) as captured:
        manager.remove(candidate)

    assert captured.value.code is AudioAcquisitionErrorCode.WORKSPACE_ERROR


def test_remove_rejects_a_path_outside_the_configured_root(tmp_path: Path) -> None:
    manager = IsolatedMediaWorkspace(tmp_path / "media")
    outside = tmp_path / str(uuid4()) / "attempt-1"
    outside.mkdir(parents=True)
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(AudioAcquisitionError):
        manager.remove(outside)

    assert marker.read_text(encoding="utf-8") == "keep"


def test_prepare_rejects_invalid_attempts(tmp_path: Path) -> None:
    manager = IsolatedMediaWorkspace(tmp_path / "media")

    with pytest.raises(ValueError, match="attempt"):
        manager.prepare(uuid4(), 0)


def test_prepare_translates_filesystem_failures_to_a_safe_error(tmp_path: Path) -> None:
    root_file = tmp_path / "not-a-directory"
    root_file.write_text("occupied", encoding="utf-8")
    manager = IsolatedMediaWorkspace(root_file)

    with pytest.raises(AudioAcquisitionError) as captured:
        manager.prepare(uuid4(), 1)

    assert captured.value.code is AudioAcquisitionErrorCode.WORKSPACE_ERROR


def test_prepare_preserves_a_safe_error_from_stale_workspace_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = IsolatedMediaWorkspace(tmp_path / "media")
    job_id = uuid4()
    manager.path_for(job_id, 1).mkdir(parents=True)
    failure = AudioAcquisitionError(
        AudioAcquisitionErrorCode.WORKSPACE_ERROR,
        "Cleanup failed.",
    )

    def fail(_workspace: Path) -> bool:
        raise failure

    monkeypatch.setattr(manager, "remove", fail)

    with pytest.raises(AudioAcquisitionError) as captured:
        manager.prepare(job_id, 1)

    assert captured.value is failure


def test_prepare_rejects_a_workspace_that_resolves_outside_its_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = IsolatedMediaWorkspace(tmp_path / "media")
    marker = tmp_path / "outside" / "marker.txt"

    def reject(_path: Path) -> bool:
        return False

    monkeypatch.setattr(manager, "_is_owned_attempt", reject)

    with pytest.raises(AudioAcquisitionError) as captured:
        manager.prepare(uuid4(), 1)

    assert captured.value.code is AudioAcquisitionErrorCode.WORKSPACE_ERROR
    assert not marker.parent.exists()


def test_remove_translates_filesystem_failures_to_a_safe_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = IsolatedMediaWorkspace(tmp_path / "media")
    workspace = manager.prepare(uuid4(), 1)

    def fail(_path: Path) -> None:
        raise OSError("locked")

    monkeypatch.setattr("galaxy_frog.adapters.media.workspace.shutil.rmtree", fail)

    with pytest.raises(AudioAcquisitionError) as captured:
        manager.remove(workspace)

    assert captured.value.code is AudioAcquisitionErrorCode.WORKSPACE_ERROR
