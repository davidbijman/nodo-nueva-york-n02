"""Limpieza unidireccional de artefactos retirados del núcleo Sonantia 1.0."""

from __future__ import annotations

import shutil
from pathlib import Path

_DEPRECATED_STATE_PATHS = (
    "data/messages",
    "data/replication",
    "data/interactions",
    "data/state.json",
    "data/known-message-ids.json",
)

_DEPRECATED_PUBLIC_FILES = (
    "status.json",
    "month.html",
    "interactions.html",
)


def remove_deprecated_artifacts(root: Path, *, public_directory: str = "public") -> list[Path]:
    """Elimina restos que ya no pertenecen al contrato ni al estado Sonantia 1.0."""

    removed: list[Path] = []
    for relative_path in _DEPRECATED_STATE_PATHS:
        path = root / relative_path
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(path)
        elif path.exists():
            path.unlink()
            removed.append(path)

    public_root = root / public_directory
    for relative_path in _DEPRECATED_PUBLIC_FILES:
        path = public_root / relative_path
        if path.exists():
            path.unlink()
            removed.append(path)

    interactions_root = public_root / "interactions"
    if interactions_root.is_dir():
        for path in interactions_root.glob("*.json"):
            if path.name != "current.json":
                path.unlink()
                removed.append(path)

    archive_root = public_root / "archive"
    if archive_root.is_dir():
        for path in archive_root.iterdir():
            if path.is_dir() and path.name.isdigit():
                shutil.rmtree(path)
                removed.append(path)

    return removed
