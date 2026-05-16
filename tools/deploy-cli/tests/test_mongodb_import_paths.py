"""MongoDB importer restore-path selection."""

from __future__ import annotations

from pathlib import Path

from leco_app.data_import.importers.mongodb import mongorestore_target_in_container


def _touch_bson(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00")


def test_flat_mongodump_db_folder_uses_remote_root(tmp_path: Path) -> None:
    """mongodump --out=mongo --db=admin → mongo/admin/*.bson"""
    src = tmp_path / "admin"
    _touch_bson(src / "users.bson")
    assert mongorestore_target_in_container("/tmp/leco-seed-admin", src, "admin") == "/tmp/leco-seed-admin"


def test_nested_db_subfolder_uses_remote_db(tmp_path: Path) -> None:
    """Full dump tree copied as mongo/dump/ with admin/admin/*.bson style nesting."""
    src = tmp_path / "seed"
    _touch_bson(src / "admin" / "admin" / "users.bson")
    assert mongorestore_target_in_container("/tmp/leco-seed-seed", src, "admin") == "/tmp/leco-seed-seed/admin"


def test_no_database_uses_remote_root(tmp_path: Path) -> None:
    src = tmp_path / "dump"
    _touch_bson(src / "mydb" / "coll.bson")
    assert mongorestore_target_in_container("/tmp/leco-seed-dump", src, "") == "/tmp/leco-seed-dump"
