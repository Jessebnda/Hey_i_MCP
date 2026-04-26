from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


class SupabaseRestClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = base_url or _first_env("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL")
        self.api_key = api_key or _first_env(
            "SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_SECRET_KEY",
            "SUPABASE_ANON_KEY",
            "NEXT_PUBLIC_SUPABASE_ANON_KEY",
        )
        self.key_source = self._detect_key_source()

    def _detect_key_source(self) -> str | None:
        if os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
            return "SUPABASE_SERVICE_ROLE_KEY"
        if os.getenv("SUPABASE_SECRET_KEY"):
            return "SUPABASE_SECRET_KEY"
        if os.getenv("SUPABASE_ANON_KEY"):
            return "SUPABASE_ANON_KEY"
        if os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY"):
            return "NEXT_PUBLIC_SUPABASE_ANON_KEY"
        return None

    def _get_base_url(self) -> str:
        if not self.base_url:
            raise RuntimeError("SUPABASE_URL or NEXT_PUBLIC_SUPABASE_URL is not configured.")

        return self.base_url.rstrip("/")

    def _get_api_key(self) -> str:
        if not self.api_key:
            raise RuntimeError(
                "SUPABASE_SERVICE_ROLE_KEY, SUPABASE_SECRET_KEY, SUPABASE_ANON_KEY, or NEXT_PUBLIC_SUPABASE_ANON_KEY is not configured."
            )

        return self.api_key

    def select_rows(
        self,
        table_name: str = "user_profiles",
        schema: str = "public",
        filters: dict[str, str | int | bool] | None = None,
        limit: int = 5,
        order_by: str | None = None,
        ascending: bool = True,
    ) -> dict[str, Any]:
        try:
            normalized_table_name = table_name.strip()
            if not normalized_table_name:
                raise ValueError("table_name cannot be empty.")

            normalized_schema = schema.strip()
            if not normalized_schema:
                raise ValueError("schema cannot be empty.")

            if limit < 0:
                raise ValueError("limit must be greater than or equal to 0.")

            base_url = self._get_base_url()
            api_key = self._get_api_key()

            query_params: dict[str, str] = {"select": "*", "limit": str(limit)}
            if order_by:
                normalized_order_by = order_by.strip()
                if not normalized_order_by:
                    raise ValueError("order_by cannot be empty.")

                direction = "asc" if ascending else "desc"
                query_params["order"] = f"{normalized_order_by}.{direction}"

            if filters:
                for column, value in filters.items():
                    if isinstance(value, bool):
                        value_text = "true" if value else "false"
                    else:
                        value_text = str(value)

                    query_params[column] = f"eq.{value_text}"

            full_url = (
                f"{base_url}/rest/v1/{quote(normalized_table_name, safe='._')}"
                f"?{urlencode(query_params)}"
            )

            request = Request(
                full_url,
                headers={
                    "apikey": api_key,
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                    **(
                        {"Accept-Profile": normalized_schema, "Content-Profile": normalized_schema}
                        if normalized_schema != "public"
                        else {}
                    ),
                },
                method="GET",
            )

            with urlopen(request, timeout=15) as response:
                raw_body = response.read().decode("utf-8")

            rows = json.loads(raw_body) if raw_body else []
            if not isinstance(rows, list):
                raise RuntimeError("Supabase REST response was not a JSON array.")

            return {
                "ok": True,
                "table_name": normalized_table_name,
                "schema": normalized_schema,
                "filters": filters,
                "limit": limit,
                "order_by": order_by,
                "ascending": ascending,
                "key_source": self.key_source,
                "row_count": len(rows),
                "rows": rows,
            }
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            return {
                "ok": False,
                "table_name": table_name,
                "schema": schema,
                "filters": filters,
                "limit": limit,
                "order_by": order_by,
                "ascending": ascending,
                "key_source": self.key_source,
                "error": f"HTTP {exc.code}: {error_body or exc.reason}",
                "rows": [],
            }
        except (URLError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            return {
                "ok": False,
                "table_name": table_name,
                "schema": schema,
                "filters": filters,
                "limit": limit,
                "order_by": order_by,
                "ascending": ascending,
                "key_source": self.key_source,
                "error": str(exc),
                "rows": [],
            }