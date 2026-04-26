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

    def insert_insight(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Inserta un registro en user_insights usando consulta parametrizada.
        Solo incluye las columnas con valor no-None.
        """
        try:
            engine = self._get_engine()
            columns = {k: v for k, v in data.items() if v is not None}

            if not columns:
                return {"ok": False, "error": "No data provided", "rows": []}

            col_names = ", ".join(columns.keys())
            col_params = ", ".join(f":{k}" for k in columns.keys())
            sql = (
                f"INSERT INTO user_insights ({col_names}) "
                f"VALUES ({col_params}) "
                "RETURNING id, created_at"
            )

            with engine.begin() as connection:
                result = connection.execute(text(sql), columns)
                row = result.mappings().first()

                if row is None:
                    return {"ok": False, "error": "Insert did not return a row", "rows": []}

                return {
                    "ok": True,
                    "id": str(row["id"]),
                    "created_at": str(row["created_at"]),
                }
        except (RuntimeError, SQLAlchemyError) as exc:
            return {"ok": False, "error": str(exc)}

    def upsert_user_segment(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Upsert de user_segments por user_id usando consulta parametrizada.
        Solo incluye columnas con valor no-None.
        """
        try:
            engine = self._get_engine()
            columns = {k: v for k, v in data.items() if v is not None}

            if "user_id" not in columns:
                return {"ok": False, "error": "user_id is required"}

            if len(columns) == 1:
                return {"ok": False, "error": "No updatable fields provided"}

            col_names = ", ".join(columns.keys())
            col_params = ", ".join(f":{k}" for k in columns.keys())
            update_columns = [col for col in columns.keys() if col != "user_id"]
            update_clause = ", ".join(f"{col} = EXCLUDED.{col}" for col in update_columns)
            sql = (
                f"INSERT INTO user_segments ({col_names}) "
                f"VALUES ({col_params}) "
                "ON CONFLICT (user_id) DO UPDATE SET "
                f"{update_clause} "
                "RETURNING user_id, segmento, cluster_id, updated_at"
            )

            with engine.begin() as connection:
                result = connection.execute(text(sql), columns)
                row = result.mappings().first()

                if row is None:
                    return {"ok": False, "error": "Upsert did not return a row"}

                return {
                    "ok": True,
                    "user_id": str(row["user_id"]),
                    "segmento": row.get("segmento"),
                    "cluster_id": row.get("cluster_id"),
                    "updated_at": str(row["updated_at"]) if row.get("updated_at") is not None else None,
                }
        except (RuntimeError, SQLAlchemyError) as exc:
            return {"ok": False, "error": str(exc)}