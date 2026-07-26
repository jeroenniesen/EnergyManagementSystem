import sqlite3

from scripts.verify_backup import verify


def test_verify_backup_accepts_valid_database(tmp_path):
    path = tmp_path / "backup.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("create table sample (value integer)")
    assert verify(path)


def test_verify_backup_rejects_missing_and_zero_byte_files(tmp_path):
    assert not verify(tmp_path / "missing.sqlite")
    empty = tmp_path / "empty.sqlite"
    empty.touch()
    assert not verify(empty)


def test_verify_backup_rejects_corrupt_database(tmp_path):
    path = tmp_path / "corrupt.sqlite"
    path.write_bytes(b"not sqlite")
    assert not verify(path)
