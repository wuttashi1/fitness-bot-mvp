import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
        # Existing v1 databases keep their data. Schema upgrades are atomic and repeatable.
        with self.transaction() as connection:
            if not connection.execute("SELECT 1 FROM schema_version WHERE version=2").fetchone():
                connection.execute("ALTER TABLE users ADD COLUMN username TEXT")
                connection.execute("ALTER TABLE library ADD COLUMN alias TEXT")
                connection.execute("ALTER TABLE programs ADD COLUMN published INTEGER NOT NULL DEFAULT 0")
                connection.execute("CREATE INDEX workshop_listing ON programs(published,id)")
                connection.execute("INSERT INTO schema_version VALUES(2)")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
