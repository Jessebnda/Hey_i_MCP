from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from hey_i_mcp.database import DatabaseClient


mcp = FastMCP("Hey i MCP")
database_client = DatabaseClient()


@mcp.tool()
def run_query(query: str) -> dict[str, Any]:
    return database_client.query(query)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()