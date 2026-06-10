"""World backup creation, rotation, listing, and restore.

This module uses :mod:`tarfile`/:mod:`zipfile` from the standard library so it
produces identical archives on Windows, Linux, and macOS, with restore,
retention by count *and* age, and integrity-friendly atomic writes.
"""

from __future__ import annotations

import os
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from mcsu.errors import BackupError
from mcsu.utils import format_bytes, parse_timestamp_slug, timestamp_slug

_EXTENSIONS = {
    "tar.gz": ".tar.gz",
    "tar": ".tar",
    "zip": ".zip",
}
_PREFIX = "backup_"


@dataclass(slots=True)
class BackupInfo:
    path: Path
    created: datetime
    size: int

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size_human(self) -> str:
        return format_bytes(self.size)


class BackupManager:
    """Manages the lifecycle of world archives for one server."""

    def __init__(
        self,
        server_dir: str | Path,
        backup_dir: str | Path,
        *,
        paths: list[str] | None = None,
        archive_format: str = "tar.gz",
        compression_level: int = 6,
        keep: int = 48,
        keep_days: int = 0,
    ) -> None:
        self.server_dir = Path(server_dir).resolve()
        self.backup_dir = Path(backup_dir).resolve()
        self.paths = paths or ["world"]
        if archive_format not in _EXTENSIONS:
            raise BackupError(f"unsupported archive format: {archive_format}")
        self.archive_format = archive_format
        self.compression_level = compression_level
        self.keep = keep
        self.keep_days = keep_days

    # -- creation ---------------------------------------------------------- #

    def create(self, *, label: str | None = None) -> BackupInfo:
        """Create a new archive of the configured world paths.

        The archive is written to a temp file first and atomically renamed so
        a crash mid-backup never leaves a corrupt file in the rotation.
        """
        sources = self._resolve_sources()
        if not sources:
            raise BackupError(
                f"none of the configured backup paths exist under {self.server_dir}: "
                f"{', '.join(self.paths)}"
            )
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        slug = timestamp_slug()
        suffix = _EXTENSIONS[self.archive_format]
        stem = f"{_PREFIX}{slug}"
        if label:
            safe = "".join(c for c in label if c.isalnum() or c in "-_")
            if safe:
                stem = f"{stem}_{safe}"
        final_path = self.backup_dir / f"{stem}{suffix}"

        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{stem}.", suffix=f"{suffix}.partial", dir=self.backup_dir
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            if self.archive_format == "zip":
                self._write_zip(tmp_path, sources)
            else:
                self._write_tar(tmp_path, sources)
            tmp_path.replace(final_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return BackupInfo(
            path=final_path,
            created=parse_timestamp_slug(slug) or datetime.now(),
            size=final_path.stat().st_size,
        )

    def _resolve_sources(self) -> list[Path]:
        sources = []
        for rel in self.paths:
            candidate = (self.server_dir / rel).resolve()
            if candidate.exists():
                sources.append(candidate)
        return sources

    def _write_tar(self, dest: Path, sources: list[Path]) -> None:
        try:
            if self.archive_format == "tar.gz":
                tar = tarfile.open(dest, "w:gz", compresslevel=self.compression_level)  # noqa: SIM115
            else:
                tar = tarfile.open(dest, "w")  # noqa: SIM115
            with tar:
                for src in sources:
                    tar.add(src, arcname=src.name)
        except (OSError, tarfile.TarError) as exc:
            raise BackupError(f"failed to write archive: {exc}") from exc

    def _write_zip(self, dest: Path, sources: list[Path]) -> None:
        compression = zipfile.ZIP_DEFLATED
        try:
            with zipfile.ZipFile(
                dest, "w", compression=compression, compresslevel=self.compression_level
            ) as zf:
                for src in sources:
                    if src.is_file():
                        zf.write(src, arcname=src.name)
                        continue
                    for path in src.rglob("*"):
                        zf.write(path, arcname=str(Path(src.name) / path.relative_to(src)))
        except (OSError, zipfile.BadZipFile) as exc:
            raise BackupError(f"failed to write archive: {exc}") from exc

    # -- listing / rotation ------------------------------------------------ #

    def list_backups(self) -> list[BackupInfo]:
        """Return existing backups, newest first."""
        if not self.backup_dir.is_dir():
            return []
        infos: list[BackupInfo] = []
        for path in self.backup_dir.iterdir():
            if not path.is_file() or not path.name.startswith(_PREFIX):
                continue
            if path.name.endswith(".partial"):
                continue
            slug = self._slug_from_name(path.name)
            created = parse_timestamp_slug(slug) if slug else None
            if created is None:
                created = datetime.fromtimestamp(path.stat().st_mtime)
            infos.append(BackupInfo(path=path, created=created, size=path.stat().st_size))
        infos.sort(key=lambda b: b.created, reverse=True)
        return infos

    @staticmethod
    def _slug_from_name(name: str) -> str | None:
        rest = name[len(_PREFIX) :]
        for ext in (".tar.gz", ".tar", ".zip"):
            if rest.endswith(ext):
                rest = rest[: -len(ext)]
                break
        # Strip an optional "_label" suffix; the slug is the first 19 chars.
        return rest[:19] if len(rest) >= 19 else None

    def prune(self) -> list[BackupInfo]:
        """Delete backups beyond the retention policy; return what was removed."""
        backups = self.list_backups()
        to_delete: list[BackupInfo] = []

        if self.keep_days > 0:
            cutoff = datetime.now() - timedelta(days=self.keep_days)
            to_delete.extend(b for b in backups if b.created < cutoff)

        survivors = [b for b in backups if b not in to_delete]
        if self.keep > 0 and len(survivors) > self.keep:
            to_delete.extend(survivors[self.keep :])

        for info in to_delete:
            info.path.unlink(missing_ok=True)
        return to_delete

    # -- restore ----------------------------------------------------------- #

    def restore(self, backup: str | Path | BackupInfo, *, destination: Path | None = None) -> Path:
        """Extract a backup back into the server directory (or ``destination``).

        Existing world directories are *not* deleted automatically; the caller
        is expected to stop the server and move/clear the old world first. The
        archive is validated before extraction.
        """
        path = backup.path if isinstance(backup, BackupInfo) else Path(backup)
        if not path.is_absolute():
            path = self.backup_dir / path
        if not path.is_file():
            raise BackupError(f"backup not found: {path}")
        target = (destination or self.server_dir).resolve()
        target.mkdir(parents=True, exist_ok=True)
        try:
            if path.name.endswith(".zip"):
                with zipfile.ZipFile(path) as zf:
                    _safe_extract_zip(zf, target)
            else:
                with tarfile.open(path) as tar:
                    _safe_extract_tar(tar, target)
        except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
            raise BackupError(f"failed to restore {path.name}: {exc}") from exc
        return target


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _safe_extract_tar(tar: tarfile.TarFile, dest: Path) -> None:
    # Guard against path-traversal ("../") entries (a.k.a. tarbomb / Zip Slip).
    for member in tar.getmembers():
        member_path = dest / member.name
        if not _is_within(dest, member_path):
            raise BackupError(f"refusing unsafe path in archive: {member.name}")
    # Python 3.12+ supports a 'filter' argument; fall back gracefully.
    try:
        tar.extractall(dest, filter="data")  # type: ignore[call-arg]
    except TypeError:
        tar.extractall(dest)


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    for name in zf.namelist():
        if not _is_within(dest, dest / name):
            raise BackupError(f"refusing unsafe path in archive: {name}")
    zf.extractall(dest)
