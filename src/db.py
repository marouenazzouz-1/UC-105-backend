"""Dispatch module to pick the DB backend implementation based on `DB_BACKEND`.

Exports `DBHandler` which is implemented in either `db_sqllite` or `db_SqlServer`.
"""

import os

backend = os.environ.get("DB_BACKEND", "sqlite").lower()

if backend == "mssql":
    from .db_SqlServer import DBHandler  # type: ignore
else:
    from .db_sqllite import DBHandler  # type: ignore
