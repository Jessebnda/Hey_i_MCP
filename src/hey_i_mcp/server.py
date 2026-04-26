from __future__ import annotations

from typing import Any
import time

import httpx

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
    model: str,
    function: str,
    method: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Router for model endpoints. Builds route as /{model}/{function}
    and forwards the request to the deployed Hugging Face Space.

    Supported model: segmentacion
    Supported functions: health (GET), segments (GET), insight/new (POST), insight/existing (POST)
    """
    base_url = "https://orbit05-datathon206.hf.space"
    supported = {
        "segmentacion": ["health", "segments", "insight/new", "insight/existing"]
    }

    if model not in supported:
        return {"error": f"Unsupported model: {model}", "status_code": 400}

    if function not in supported[model]:
        return {"error": f"Unsupported function for {model}: {function}", "status_code": 400}

    # Infer HTTP method when omitted
    if method is None:
        method = "GET" if function in ("health", "segments") else "POST"

    method = method.upper()
    if method not in ("GET", "POST"):
        return {"error": "Invalid method", "status_code": 400}

    # Validation for insight/new payload
    if model == "segmentacion" and function == "insight/new":
        required_keys = [
            "edad",
            "ingreso_mensual_mxn",
            "score_buro",
            "antiguedad_dias",
            "dias_desde_ultimo_login",
            "satisfaccion_1_10",
            "es_hey_pro",
            "nomina_domiciliada",
            "recibe_remesas",
            "usa_hey_shop",
            "tiene_seguro",
            "patron_uso_atipico",
            "n_productos_total",
            "max_utilizacion_credito",
            "total_spend_mxn",
            "fail_rate",
            "n_msi_txns",
            "cashback_total_mxn",
            "intl_ratio",
            "has_credito",
            "has_inversion",
        ]
        missing = [k for k in required_keys if not (payload and k in payload)]
        if missing:
            return {"error": "Missing required payload keys", "missing": missing, "status_code": 400}

    # insight/existing requires a known-user CSV; return 501 if not provided
    if model == "segmentacion" and function == "insight/existing":
        if not payload or "known_user_csv" not in payload:
            return {"error": "known-user CSV required", "status_code": 501}

    url = f"{base_url}/{model}/{function}"

    start = time.perf_counter()
    try:
        if method == "GET":
            resp = httpx.get(url, timeout=30)
        else:
            # For insight/existing prefer multipart file upload when CSV content provided
            if function == "insight/existing" and payload and "known_user_csv" in payload:
                known_csv = payload.get("known_user_csv")
                if isinstance(known_csv, str):
                    files = {"file": ("known_users.csv", known_csv, "text/csv")}
                    resp = httpx.post(url, files=files, timeout=60)
                else:
                    resp = httpx.post(url, json=payload, timeout=60)
            else:
                resp = httpx.post(url, json=payload or {}, headers={"Content-Type": "application/json"}, timeout=60)

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