"""SQLite WAL persistence. One connection per call allows independent control threads."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from autotrader.core.security import private_database


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_database(path)
        self.path = path
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS journal "
                "(id INTEGER PRIMARY KEY, at TEXT DEFAULT CURRENT_TIMESTAMP, "
                "kind TEXT NOT NULL, payload TEXT NOT NULL)"
            )

            db.execute(
                "CREATE TABLE IF NOT EXISTS paper_fills (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY, at REAL NOT NULL, "
                "equity TEXT NOT NULL, nav TEXT NOT NULL)"
            )

    def record_metric(self, at: float, equity: str, nav: str) -> None:
        """Keep at most seven days and 125,000 samples (about five-second intervals)."""
        with self.connect() as db:
            db.execute("INSERT INTO metrics(at,equity,nav) VALUES (?,?,?)", (at, equity, nav))
            db.execute(
                "DELETE FROM metrics WHERE at < ? OR id <= (SELECT COALESCE(MAX(id),0)-125000 FROM metrics)",
                (at - 7 * 86400,),
            )

    def history(self, limit: int = 2000) -> list[float]:
        with self.connect() as db:
            rows = db.execute("SELECT equity FROM metrics ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [float(row[0]) for row in reversed(rows)]

    def recent_fills(self) -> list[dict[str, str]]:
        import json

        with self.connect() as db:
            rows = db.execute("SELECT payload FROM paper_fills ORDER BY id DESC LIMIT 20").fetchall()
        return [json.loads(row[0]) for row in rows]

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def get(self, key: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else None

    def put(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def log(self, kind: str, payload: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO journal(kind,payload) VALUES (?,?)", (kind, payload))
