import os
import sqlite3
import threading
from typing import List
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, BaseMessage

class DBHandler():
    def __init__(self, db_path: str, db_name: str, max_memory_turns: int):
        self._local = threading.local()
        self.db_path = db_path
        self.db_name = db_name
        self.max_memory_turns = max_memory_turns

    def _get_conn(self) -> sqlite3.Connection:
        """Return a per-thread SQLite connection, creating it on first use."""
        if not hasattr(self._local, "conn"):
            os.makedirs(self.db_path, exist_ok=True)
            # check_same_thread=True is correct here: each thread has its own conn
            conn = sqlite3.connect(os.path.join(self.db_path, self.db_name), check_same_thread=True)
            conn.execute("PRAGMA journal_mode=WAL")    # concurrent readers + one writer
            conn.execute("PRAGMA synchronous=NORMAL")  # good durability / perf balance
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT    NOT NULL,
                    role        TEXT    NOT NULL,
                    content     TEXT    NOT NULL,
                    created_at  REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session ON messages(session_id, created_at)"
            )
            conn.commit()
            self._local.conn = conn
        return self._local.conn

    def _load_history(self, session_id: str) -> List[BaseMessage]:
        """Load the most recent MAX_MEMORY_TURNS messages for a session."""
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
        """
        Append messages to the DB then prune rows beyond MAX_MEMORY_TURNS.
        Both steps run in one transaction — a crash never leaves stale data.
        """
        conn = self._get_conn()
        with conn:  # auto-commit on success, rollback on exception
            for msg in messages:
                role = "human" if isinstance(msg, HumanMessage) else "ai"
                conn.execute(
                    "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                    (session_id, role, msg.content),
                )
            # Keep only the MAX_MEMORY_TURNS most recent rows per session
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
