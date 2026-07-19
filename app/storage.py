from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
STATE_DIR = Path("/data") if Path("/data").exists() else BASE_DIR / ".localstate"
RAILWAY_VOLUME_ENV = "RAILWAY_VOLUME_MOUNT_PATH"

_SAFE_GENERATED_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class StoragePaths:
    root: Path
    uploads_dir: Path
    pdfs_dir: Path
    visual_reference_uploads_dir: Path
    estimate_pdfs_dir: Path
    using_railway_volume: bool


def configured_storage_paths() -> StoragePaths:
    railway_mount = os.environ.get(RAILWAY_VOLUME_ENV, "").strip()
    if railway_mount:
        root = Path(railway_mount)
        uploads_dir = root / "uploads"
        pdfs_dir = root / "pdfs"
        return StoragePaths(
            root=root,
            uploads_dir=uploads_dir,
            pdfs_dir=pdfs_dir,
            visual_reference_uploads_dir=uploads_dir / "visual-references",
            estimate_pdfs_dir=pdfs_dir / "estimates",
            using_railway_volume=True,
        )

    visual_reference_uploads_dir = STATIC_DIR / "visual-references" / "uploads"
    return StoragePaths(
        root=BASE_DIR,
        uploads_dir=visual_reference_uploads_dir,
        pdfs_dir=STATE_DIR,
        visual_reference_uploads_dir=visual_reference_uploads_dir,
        estimate_pdfs_dir=STATE_DIR / "estimate_pdfs",
        using_railway_volume=False,
    )


def ensure_storage_directories(paths: StoragePaths | None = None) -> StoragePaths:
    paths = paths or configured_storage_paths()
    for directory in {
        paths.uploads_dir,
        paths.pdfs_dir,
        paths.visual_reference_uploads_dir,
        paths.estimate_pdfs_dir,
    }:
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def safe_upload_suffix(filename: str) -> str:
    raw = str(filename or "").strip()
    if not raw or "/" in raw or "\\" in raw:
        raise HTTPException(status_code=400, detail="Invalid upload filename")
    suffix = Path(raw).suffix.lower()
    if not suffix or any(ord(char) < 32 for char in suffix):
        raise HTTPException(status_code=400, detail="Invalid upload filename")
    return suffix


def safe_generated_filename(filename: str) -> str:
    name = Path(str(filename or "")).name
    if not name or name != str(filename) or not _SAFE_GENERATED_FILENAME_RE.fullmatch(name):
        raise HTTPException(status_code=404, detail="File not found")
    return name


def resolve_storage_child(directory: Path, filename: str) -> Path:
    name = safe_generated_filename(filename)
    root = directory.resolve()
    target = (directory / name).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail="File not found")
    return target


def visual_reference_upload_url(filename: str) -> str:
    name = safe_generated_filename(filename)
    return f"/static/visual-references/uploads/{name}"
