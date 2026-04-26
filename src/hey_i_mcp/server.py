from __future__ import annotations

from typing import Any, Annotated, Literal
import time

import httpx
from pydantic import Field

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


@mcp.tool()
def call_model_endpoint(
    model: Annotated[
        Literal["segmentacion"],
        Field(description="Model prefix. Only segmentacion is supported by this Space."),
    ],
    function: Annotated[
        Literal["health", "segments", "insight/new", "insight/existing"],
        Field(
            description=(
                "Endpoint suffix without the leading slash. Use health or segments for GET, "
                "and insight/new or insight/existing for POST."
            )
        ),
    ],
    method: Annotated[
        Literal["GET", "POST"] | None,
        Field(description="Optional HTTP method. Inferred when omitted."),
    ] = None,
    payload: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "JSON body for POST endpoints. For insight/new send the segmentacion feature "
                "payload, including numeric fields, 0/1 flags, and optional conversation_text. "
                "For insight/existing send {user_id, language}."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """
    Call the Datathon206 segmentacion Space.

    This tool routes requests to https://orbit05-datathon206.hf.space/{model}/{function}.
    Use it to read health metadata, list clusters, score a new user, or retrieve an
    existing user's segment insight.

    Supported routes:
    - segmentacion/health -> GET
    - segmentacion/segments -> GET
    - segmentacion/insight/new -> POST with the new-user feature payload
    - segmentacion/insight/existing -> POST with user_id and optional language
    """
    base_url = "https://orbit05-datathon206.hf.space"

    # Infer HTTP method when omitted
    if method is None:
        method = "GET" if function in ("health", "segments") else "POST"

    method = method.upper()
    if method not in ("GET", "POST"):
        return {"error": "Invalid method", "status_code": 400}

    url = f"{base_url}/{model}/{function}"

    start = time.perf_counter()
    try:
        request_kwargs: dict[str, Any] = {"timeout": 60}
        if method == "GET":
            if payload:
                request_kwargs["params"] = payload
        else:
            request_kwargs["json"] = payload or {}

        resp = httpx.request(method, url, **request_kwargs)

        latency_ms = int((time.perf_counter() - start) * 1000)
        try:
            data = resp.json()
        except Exception:
            data = {"text": resp.text}

        return {"status_code": resp.status_code, "latency_ms": latency_ms, "response": data}

    except httpx.RequestError as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {"error": str(exc), "latency_ms": latency_ms, "status_code": 500}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()