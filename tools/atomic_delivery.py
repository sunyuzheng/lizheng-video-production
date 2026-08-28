"""Rollback-safe promotion of a prepared artifact bundle."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Iterable


def _replace_path(source: Path, destination: Path) -> None:
    source.replace(destination)


def _backup_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{target.name}.backup.",
        dir=target.parent,
        delete=False,
    ) as handle:
        return Path(handle.name)


def commit_prepared_files(pairs: Iterable[tuple[Path, Path]]) -> None:
    """Commit all prepared files or restore every prior target on failure.

    Prepared files must already be complete and should live on the same
    filesystem as their targets. They are consumed on success and removed on
    failure. Existing target files are backed up until the whole bundle lands.
    """
    resolved_pairs = [(source.resolve(), target.resolve()) for source, target in pairs]
    if not resolved_pairs:
        raise ValueError("artifact bundle is empty")

    roles = [path for pair in resolved_pairs for path in pair]
    if len(set(roles)) != len(roles):
        raise ValueError("prepared and target artifact paths must all be distinct")
    for source, target in resolved_pairs:
        if not source.is_file():
            raise FileNotFoundError(f"prepared artifact does not exist: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)

    backups: dict[Path, Path] = {}
    existed = {target: target.exists() for _, target in resolved_pairs}
    committed: list[Path] = []
    rollback_failed = False
    try:
        for _, target in resolved_pairs:
            if existed[target]:
                backup = _backup_path(target)
                shutil.copy2(target, backup)
                backups[target] = backup

        for source, target in resolved_pairs:
            _replace_path(source, target)
            committed.append(target)
    except Exception as error:
        rollback_errors: list[str] = []
        for target in reversed(committed):
            try:
                if existed[target]:
                    _replace_path(backups[target], target)
                else:
                    target.unlink(missing_ok=True)
            except Exception as rollback_error:
                rollback_errors.append(f"{target}: {rollback_error}")
        if rollback_errors:
            rollback_failed = True
            raise RuntimeError(
                f"artifact bundle commit failed ({error}); rollback failed for "
                + "; ".join(rollback_errors)
            ) from error
        raise
    finally:
        for source, _ in resolved_pairs:
            source.unlink(missing_ok=True)
        if not rollback_failed:
            for backup in backups.values():
                backup.unlink(missing_ok=True)
