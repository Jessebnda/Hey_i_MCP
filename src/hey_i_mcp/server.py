from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from hey_i_mcp.database import DatabaseClient
from hey_i_mcp.supabase_api import SupabaseRestClient


mcp = FastMCP("Hey i MCP")
database_client = DatabaseClient()
supabase_rest_client = SupabaseRestClient()


@mcp.tool()
def run_query(query: str) -> dict[str, Any]:
    return database_client.query(query)


@mcp.tool()
def supabase_select_rows(
    table_name: str = "users",
    schema: str = "public",
    status: str | None = "active",
    limit: int = 5,
) -> dict[str, Any]:
    return supabase_rest_client.select_rows(
        table_name=table_name,
        schema=schema,
        status=status,
        limit=limit,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()