from __future__ import annotations

import tarfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mcsu.backup import _PREFIX, BackupManager
from mcsu.errors import BackupError


def _manager(server_dir: Path, **kw) -> BackupManager:
    return BackupManager(
        server_dir,
        server_dir / "backups",
        paths=["world"],
        **kw,
    )


def test_create_targz(server_dir):
    mgr = _manager(server_dir, archive_format="tar.gz")
    info = mgr.create()
    assert info.path.exists()
    assert info.path.suffix == ".gz"
    assert info.size > 0
    with tarfile.open(info.path) as tar:
        names = tar.getnames()
    assert any("world" in n for n in names)
    assert any("level.dat" in n for n in names)


def test_create_zip(server_dir):
    mgr = _manager(server_dir, archive_format="zip")
    info = mgr.create()
    assert info.path.suffix == ".zip"
    with zipfile.ZipFile(info.path) as zf:
        names = zf.namelist()
    assert any("level.dat" in n for n in names)


def test_create_with_label(server_dir):
    mgr = _manager(server_dir)
    info = mgr.create(label="pre-update")
    assert "pre-update" in info.name


def test_missing_paths_raises(server_dir):
    mgr = BackupManager(server_dir, server_dir / "backups", paths=["does_not_exist"])
    with pytest.raises(BackupError, match="none of the configured"):
        mgr.create()


def test_list_sorted_newest_first(server_dir):
    mgr = _manager(server_dir)
    # Forge three archives with distinct timestamps in their names.
    backups_dir = server_dir / "backups"
    backups_dir.mkdir(exist_ok=True)
    for slug in ["2026-06-10_10-00-00", "2026-06-10_12-00-00", "2026-06-10_11-00-00"]:
        (backups_dir / f"{_PREFIX}{slug}.tar.gz").write_bytes(b"x")
    listed = mgr.list_backups()
    names = [b.name for b in listed]
    assert names[0].endswith("12-00-00.tar.gz")
    assert names[-1].endswith("10-00-00.tar.gz")


def test_prune_by_count(server_dir):
    backups_dir = server_dir / "backups"
    backups_dir.mkdir(exist_ok=True)
    for hour in range(6):
        slug = f"2026-06-10_{hour:02d}-00-00"
        (backups_dir / f"{_PREFIX}{slug}.tar.gz").write_bytes(b"x")
    mgr = _manager(server_dir, keep=3)
    removed = mgr.prune()
    assert len(removed) == 3
    remaining = mgr.list_backups()
    assert len(remaining) == 3
    # The newest three survive.
    assert remaining[0].name.endswith("05-00-00.tar.gz")


def test_prune_by_age(server_dir):
    backups_dir = server_dir / "backups"
    backups_dir.mkdir(exist_ok=True)
    old = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d_%H-%M-%S")
    new = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    (backups_dir / f"{_PREFIX}{old}.tar.gz").write_bytes(b"x")
    (backups_dir / f"{_PREFIX}{new}.tar.gz").write_bytes(b"x")
    mgr = _manager(server_dir, keep=0, keep_days=7)
    removed = mgr.prune()
    assert len(removed) == 1
    assert old in removed[0].name


def test_restore_roundtrip(server_dir, tmp_path):
    mgr = _manager(server_dir)
    info = mgr.create()
    dest = tmp_path / "restored"
    mgr.restore(info, destination=dest)
    assert (dest / "world" / "level.dat").is_file()


def test_restore_rejects_traversal(server_dir, tmp_path):
    # Craft a malicious tar with a path-traversal member.
    backups_dir = server_dir / "backups"
    backups_dir.mkdir(exist_ok=True)
    evil = backups_dir / f"{_PREFIX}2026-01-01_00-00-00.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("pwned", encoding="utf-8")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../../escape.txt")
    mgr = _manager(server_dir)
    with pytest.raises(BackupError, match="unsafe path"):
        mgr.restore(evil, destination=tmp_path / "out")


def test_partial_file_ignored_in_listing(server_dir):
    backups_dir = server_dir / "backups"
    backups_dir.mkdir(exist_ok=True)
    (backups_dir / f"{_PREFIX}2026-06-10_10-00-00.tar.gz.partial").write_bytes(b"x")
    mgr = _manager(server_dir)
    assert mgr.list_backups() == []
