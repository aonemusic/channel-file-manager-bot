"""Small SQLite persistence layer for the Channel File Manager Bot."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable


class Database:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS channels (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    username TEXT,
                    registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    bot_status TEXT NOT NULL DEFAULT 'unknown',
                    is_active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS forwarded (
                    source_chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    destination_chat_id INTEGER NOT NULL,
                    forwarded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (source_chat_id, message_id, destination_chat_id)
                );
                CREATE TABLE IF NOT EXISTS scan_plans (
                    plan_id TEXT PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    source_chat_id INTEGER NOT NULL,
                    destination_chat_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    scanned_count INTEGER NOT NULL DEFAULT 0,
                    found_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    plan_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    PRIMARY KEY (plan_id, message_id),
                    FOREIGN KEY (plan_id) REFERENCES scan_plans(plan_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_candidates_plan ON candidates(plan_id, message_id);
                """
            )

    def upsert_channel(self, chat_id: int, title: str, username: str | None, bot_status: str) -> None:
        with self._connect() as con:
            con.execute(
                """INSERT INTO channels(chat_id,title,username,bot_status,is_active)
                   VALUES(?,?,?,?,1)
                   ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,
                     username=excluded.username, bot_status=excluded.bot_status, is_active=1""",
                (chat_id, title, username, bot_status),
            )

    def update_channel_status(self, chat_id: int, status: str, active: bool) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE channels SET bot_status=?, is_active=? WHERE chat_id=?",
                (status, int(active), chat_id),
            )

    def list_channels(self, active_only: bool = False) -> list[sqlite3.Row]:
        query = "SELECT * FROM channels"
        if active_only:
            query += " WHERE is_active=1"
        query += " ORDER BY title COLLATE NOCASE, chat_id"
        with self._connect() as con:
            return list(con.execute(query).fetchall())

    def get_channel(self, chat_id: int) -> sqlite3.Row | None:
        with self._connect() as con:
            return con.execute("SELECT * FROM channels WHERE chat_id=?", (chat_id,)).fetchone()

    def create_plan(self, plan_id: str, owner_id: int, source_id: int, destination_id: int) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO scan_plans(plan_id,owner_id,source_chat_id,destination_chat_id,status) VALUES(?,?,?,?, 'scanning')",
                (plan_id, owner_id, source_id, destination_id),
            )

    def add_candidates(self, plan_id: str, ids: Iterable[int]) -> int:
        values = [(plan_id, int(message_id)) for message_id in ids]
        if not values:
            return 0
        with self._connect() as con:
            con.executemany(
                "INSERT OR IGNORE INTO candidates(plan_id,message_id) VALUES(?,?)", values
            )
            return con.execute("SELECT changes()").fetchone()[0]

    def update_plan_counts(self, plan_id: str, scanned: int, found: int) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE scan_plans SET scanned_count=?, found_count=? WHERE plan_id=?",
                (scanned, found, plan_id),
            )

    def set_plan_status(self, plan_id: str, status: str) -> None:
        with self._connect() as con:
            con.execute("UPDATE scan_plans SET status=? WHERE plan_id=?", (status, plan_id))

    def get_plan(self, plan_id: str) -> sqlite3.Row | None:
        with self._connect() as con:
            return con.execute("SELECT * FROM scan_plans WHERE plan_id=?", (plan_id,)).fetchone()

    def candidate_ids(self, plan_id: str) -> list[int]:
        with self._connect() as con:
            return [r[0] for r in con.execute(
                "SELECT message_id FROM candidates WHERE plan_id=? ORDER BY message_id", (plan_id,)
            ).fetchall()]

    def candidate_batches(self, plan_id: str, batch_size: int):
        """Yield ordered candidate ID batches without materializing full history."""
        with self._connect() as con:
            cursor = con.execute(
                "SELECT message_id FROM candidates WHERE plan_id=? ORDER BY message_id",
                (plan_id,),
            )
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                yield [int(row[0]) for row in rows]

    def already_forwarded(self, source_id: int, destination_id: int, message_id: int) -> bool:
        with self._connect() as con:
            return con.execute(
                "SELECT 1 FROM forwarded WHERE source_chat_id=? AND destination_chat_id=? AND message_id=?",
                (source_id, destination_id, message_id),
            ).fetchone() is not None

    def record_forward(self, source_id: int, destination_id: int, message_id: int) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO forwarded(source_chat_id,message_id,destination_chat_id) VALUES(?,?,?)",
                (source_id, message_id, destination_id),
            )

    def delete_plan(self, plan_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM candidates WHERE plan_id=?", (plan_id,))
            con.execute("DELETE FROM scan_plans WHERE plan_id=?", (plan_id,))
