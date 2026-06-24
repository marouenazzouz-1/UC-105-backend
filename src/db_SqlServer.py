import os
import threading
from typing import List, Optional

try:
    import pyodbc
except Exception:
    pyodbc = None

from langchain_core.messages import AIMessage, HumanMessage, BaseMessage


class DBHandler:
    """MSSQL backend (ODBC via pyodbc).

    Environment variables:
    - `SQL_SERVER_CONN`: full ODBC connection string (preferred).
    - or `SQL_SERVER_PWD`: password; the code will combine it with the built-in template.
    """

    _TABLE_CREATION_SQL = """
    IF OBJECT_ID('dbo.messages', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.messages (
            id INT IDENTITY(1,1) PRIMARY KEY,
            session_id NVARCHAR(255) NOT NULL,
            role NVARCHAR(16) NOT NULL,
            content NVARCHAR(MAX) NOT NULL,
            created_at DATETIME2 NOT NULL DEFAULT (SYSUTCDATETIME())
        );
    END
    """

    _INDEX_CREATION_SQL = """
    IF NOT EXISTS (
        SELECT name FROM sys.indexes WHERE name = 'idx_session' AND object_id = OBJECT_ID('dbo.messages')
    )
    BEGIN
        CREATE INDEX idx_session ON dbo.messages(session_id, created_at);
    END
    """

    def __init__(self, db_path: Optional[str], db_name: Optional[str], max_memory_turns: int):
        self._local = threading.local()
        self.max_memory_turns = max_memory_turns
        self._conn_str = os.environ.get("SQL_SERVER_CONN")
        if not self._conn_str:
            pwd = os.environ.get("SQL_SERVER_PWD")
            if not pwd:
                raise ValueError(
                    "No SQL Server connection provided. Set SQL_SERVER_CONN or SQL_SERVER_PWD environment variable."
                )
            self._conn_str = (
                "Driver={ODBC Driver 18 for SQL Server};"
                "Server=tcp:rag-sql-v4ax4i.database.windows.net,1433;"
                "Database=rag-db-v4ax4i;"
                "Uid=SQLCCadmin;"
                f"Pwd={pwd};"
                "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
            )

    def _get_conn(self) -> "pyodbc.Connection":
        if pyodbc is None:
            raise ImportError("pyodbc is required for MSSQL backend. Install with `pip install pyodbc`.")
        if not hasattr(self._local, "conn"):
            conn = pyodbc.connect(self._conn_str, autocommit=False)
            cur = conn.cursor()
            cur.execute(self._TABLE_CREATION_SQL)
            cur.execute(self._INDEX_CREATION_SQL)
            conn.commit()
            self._local.conn = conn
        return self._local.conn

    def _load_history(self, session_id: str) -> List[BaseMessage]:
        conn = self._get_conn()
        cur = conn.cursor()
        sql = (
            "SELECT role, content FROM ("
            " SELECT id, role, content FROM dbo.messages"
            " WHERE session_id = ?"
            " ORDER BY id DESC"
            " OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY"
            " ) sub ORDER BY id ASC"
        )
        cur.execute(sql, (session_id, self.max_memory_turns))
        rows = cur.fetchall()
        result: List[BaseMessage] = []
        for role, content in rows:
            result.append(HumanMessage(content) if role == "human" else AIMessage(content))
        return result

    def _save_messages(self, session_id: str, messages: List[BaseMessage]) -> None:
        conn = self._get_conn()
        cur = conn.cursor()
        try:
            for msg in messages:
                role = "human" if isinstance(msg, HumanMessage) else "ai"
                cur.execute(
                    "INSERT INTO dbo.messages (session_id, role, content) VALUES (?, ?, ?)",
                    (session_id, role, msg.content),
                )

            delete_sql = (
                "DELETE FROM dbo.messages WHERE session_id = ? AND id NOT IN ("
                " SELECT id FROM dbo.messages WHERE session_id = ? ORDER BY id DESC OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY"
                " )"
            )
            cur.execute(delete_sql, (session_id, session_id, self.max_memory_turns))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _delete_session(self, session_id: str) -> None:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM dbo.messages WHERE session_id = ?", (session_id,))
        conn.commit()

    def _session_stats(self, session_id: str) -> dict:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM dbo.messages WHERE session_id = ?",
            (session_id,),
        )
        row = cur.fetchone()
        return {"total_messages": row[0], "oldest_ts": row[1].strftime('%Y-%m-%d %H:%M:%S.%f'), "newest_ts": row[2].strftime('%Y-%m-%d %H:%M:%S.%f')}
