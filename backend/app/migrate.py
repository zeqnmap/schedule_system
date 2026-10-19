"""Create a SQLite backup and apply pending schedule database migrations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url

from . import database
from .migrations import CURRENT_REVISION, current_revision, upgrade


def backup_database() -> Path | None:
    url = make_url(database.SQLALCHEMY_DATABASE_URL)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    source = Path(url.database)
    if not source.is_absolute():
        source = Path.cwd() / source
    if not source.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = source.with_name(f"{source.name}.pre-migration-{stamp}.bak")
    with sqlite3.connect(source) as src, sqlite3.connect(backup) as dst:
        src.backup(dst)
    return backup


def main() -> None:
    before = current_revision()
    if before == CURRENT_REVISION:
        print(f"Database is already at {CURRENT_REVISION}; no changes made.")
        return
    backup = backup_database()
    changed = upgrade()
    if backup:
        print(f"Backup: {backup}")
    print(f"Migration complete: {CURRENT_REVISION} (changed={changed}).")


if __name__ == "__main__":
    main()
