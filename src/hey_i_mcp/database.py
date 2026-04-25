from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from .config import settings


class DatabaseClient:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or settings.database_url
        self._engine: Engine | None = None

    def _get_engine(self) -> Engine:
        if self._engine is None:
            if not self.database_url:
                raise RuntimeError(
                    "SUPABASE_DATABASE_URL or DATABASE_URL is not configured."
                )

            self._engine = create_engine(self.database_url, future=True)

        return self._engine

    def query(self, query: str) -> dict[str, Any]:
        try:
            engine = self._get_engine()

            with engine.begin() as connection:
                result = connection.execute(text(query))

                if result.returns_rows:
                    rows = [dict(row) for row in result.mappings().all()]
                    return {
                        "ok": True,
                        "query": query,
                        "row_count": len(rows),
                        "rows": rows,
                    }

                return {
                    "ok": True,
                    "query": query,
                    "row_count": result.rowcount,
                    "rows": [],
                }
        except (RuntimeError, SQLAlchemyError) as exc:
            return {"ok": False, "query": query, "error": str(exc), "rows": []}