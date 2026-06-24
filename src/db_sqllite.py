import os
import sqlite3
import threading
from typing import List

from langchain_core.messages import AIMessage, HumanMessage, BaseMessage


class DBHandler:
    """SQLite backend for message storage. Keeps per-thread connections."""

    def __init__(self, db_path: str, db_name: str, max_memory_turns: int):
        self._local = threading.local()
        self.db_path = db_path
        self.db_name = db_name
        self.max_memory_turns = max_memory_turns

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            os.makedirs(self.db_path, exist_ok=True)
            conn = sqlite3.connect(os.path.join(self.db_path, self.db_name), check_same_thread=True)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT    NOT NULL,
                    role        TEXT    NOT NULL,
                    content     TEXT    NOT NULL,
                    created_at  REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON messages(session_id, created_at)")
            conn.commit()
            self._local.conn = conn
        return self._local.conn

    def _load_history(self, session_id: str) -> List[BaseMessage]:
        rows = self._get_conn().execute(
            """
            SELECT role, content FROM (
                SELECT id, role, content, created_at
                FROM   messages
                WHERE  session_id = ?
                ORDER  BY id DESC
                LIMIT  ?
            ) ORDER BY id ASC
            """,
            (session_id, self.max_memory_turns),
        ).fetchall()
        result: List[BaseMessage] = []
        for role, content in rows:
            result.append(HumanMessage(content) if role == "human" else AIMessage(content))
        return result

    def _save_messages(self, session_id: str, messages: List[BaseMessage]) -> None:
        conn = self._get_conn()
        with conn:
            for msg in messages:
                role = "human" if isinstance(msg, HumanMessage) else "ai"
                conn.execute(
                    "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                    (session_id, role, msg.content),
                )
            conn.execute(
                """
                DELETE FROM messages
                WHERE  session_id = ?
                AND  id NOT IN (
                        SELECT id FROM messages
                        WHERE  session_id = ?
                        ORDER  BY id DESC
                        LIMIT  ?
                    )
                """,
                (session_id, session_id, self.max_memory_turns),
            )

    def _delete_session(self, session_id: str) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

    def _session_stats(self, session_id: str) -> dict:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return {"total_messages": row[0], "oldest_ts": row[1], "newest_ts": row[2]}
