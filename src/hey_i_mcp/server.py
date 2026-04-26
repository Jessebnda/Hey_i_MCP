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
    table_name: Annotated[
        Literal["user_profiles", "chat_messages"],
        Field(description="Table to query. Allowed: user_profiles, chat_messages."),
    ] = "user_profiles",
    schema: str = "public",
    filters: Annotated[
        dict[str, str | int | bool] | None,
        Field(
            description=(
                "Optional equality filters as a dict. Keys must be valid column names for the "
                "chosen table. For user_profiles: user_id, sexo, estado, ciudad, "
                "nivel_educativo, ocupacion, es_hey_pro, nomina_domiciliada, canal_apertura, "
                "preferencia_canal, idioma_preferido, recibe_remesas, usa_hey_shop, "
                "tiene_seguro, patron_uso_atipico. "
                "For chat_messages: user_id, role."
            )
        ),
    ] = None,
    limit: int = 5,
) -> dict[str, Any]:
    return supabase_rest_client.select_rows(
        table_name=table_name,
        schema=schema,
        filters=filters,
        limit=limit,
    )


@mcp.tool()
def get_user_profile(
    user_id: Annotated[str, Field(description="UUID of the user to retrieve from user_profiles.")],
) -> dict[str, Any]:
    """
    Fetch the full profile row for a single user from user_profiles.

    Returns demographic and behavioural fields: edad, sexo, estado, ciudad,
    nivel_educativo, ocupacion, ingreso_mensual_mxn, antiguedad_dias, es_hey_pro,
    nomina_domiciliada, canal_apertura, score_buro, dias_desde_ultimo_login,
    preferencia_canal, satisfaccion_1_10, recibe_remesas, usa_hey_shop,
    idioma_preferido, tiene_seguro, num_productos_activos, patron_uso_atipico.
    """
    return supabase_rest_client.select_rows(
        table_name="user_profiles",
        schema="public",
        filters={"user_id": user_id},
        limit=1,
    )


@mcp.tool()
def get_user_chat_messages(
    user_id: Annotated[str, Field(description="UUID of the user whose messages to fetch.")],
    role: Annotated[
        Literal["user", "assistant"] | None,
        Field(description="Optional filter by role: 'user' or 'assistant'. Omit for all messages."),
    ] = None,
    limit: int = 20,
) -> dict[str, Any]:
    """
    Fetch chat messages for a given user from the chat_messages table.

    Messages are ordered by created_at DESC on the database side (via the index
    idx_chat_messages_user_id_created). Results include: id, user_id, role,
    content, created_at.
    """
    filters: dict[str, str | int | bool] = {"user_id": user_id}
    if role is not None:
        filters["role"] = role
    return supabase_rest_client.select_rows(
        table_name="chat_messages",
        schema="public",
        filters=filters,
        limit=limit,
    )


@mcp.tool()
def call_model_endpoint(
    model: Annotated[
        Literal["segmentacion"],
        Field(description="Model prefix. Only segmentacion is supported by this Space."),
    ],
    function: Annotated[
        Literal["health", "segments", "insight/new"],
        Field(
            description=(
                "Endpoint suffix without the leading slash. Use health or segments for GET, "
                "and insight/new for POST."
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
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """
    Call the Datathon206 segmentacion Space.

    This tool routes requests to https://orbit05-datathon206.hf.space/{model}/{function}.
    Use it to read health metadata, list clusters, or score a new user.

    Supported routes:
    - segmentacion/health -> GET
    - segmentacion/segments -> GET
    - segmentacion/insight/new -> POST with the new-user feature payload
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