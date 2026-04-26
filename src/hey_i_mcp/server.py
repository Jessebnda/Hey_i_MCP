from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Annotated, Literal
import time

import httpx
from pydantic import Field

from fastmcp import FastMCP

from hey_i_mcp.supabase_api import SupabaseRestClient


mcp = FastMCP("Hey i MCP")
supabase_rest_client = SupabaseRestClient()


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    if isinstance(value, str):
        normalized_value = value.replace("Z", "+00:00")
        try:
            parsed_value = datetime.fromisoformat(normalized_value)
        except ValueError:
            return None

        return (
            parsed_value
            if parsed_value.tzinfo is not None
            else parsed_value.replace(tzinfo=timezone.utc)
        )

    return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sort_rows_by_datetime(rows: list[dict[str, Any]], *fields: str) -> list[dict[str, Any]]:
    fallback = datetime.min.replace(tzinfo=timezone.utc)

    def sort_key(row: dict[str, Any]) -> datetime:
        for field in fields:
            parsed_value = _parse_datetime(row.get(field))
            if parsed_value is not None:
                return parsed_value

        return fallback

    return sorted(rows, key=sort_key, reverse=True)


def _format_transaction_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "user_id": row.get("user_id"),
        "producto_id": row.get("producto_id"),
        "fecha_hora": row.get("fecha_hora"),
        "tipo_operacion": row.get("tipo_operacion"),
        "monto": row.get("monto"),
        "categoria_mcc": row.get("categoria_mcc"),
        "estatus": row.get("estatus"),
        "motivo_no_procesada": row.get("motivo_no_procesada"),
        "es_internacional": row.get("es_internacional"),
        "comercio_nombre": row.get("comercio_nombre"),
        "created_at": row.get("created_at"),
    }


def _format_message_row(row: dict[str, Any]) -> dict[str, Any]:
    content = row.get("content")
    if isinstance(content, str):
        content_preview = content[:160]
        if len(content) > 160:
            content_preview += "..."
    else:
        content_preview = content

    return {
        "id": row.get("id"),
        "user_id": row.get("user_id"),
        "role": row.get("role"),
        "created_at": row.get("created_at"),
        "content_preview": content_preview,
    }


def _build_transaction_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "row_count": 0,
            "amount_total": 0,
            "amount_average": 0,
            "amount_min": 0,
            "amount_max": 0,
            "international_count": 0,
            "international_amount_total": 0,
            "international_ratio": 0,
            "status_counts": {},
            "operation_counts": {},
            "category_counts": {},
            "merchant_counts": {},
            "first_transaction_at": None,
            "last_transaction_at": None,
            "recent_transactions": [],
        }

    sorted_rows = _sort_rows_by_datetime(rows, "fecha_hora", "created_at")
    amounts: list[float] = []
    international_amounts: list[float] = []
    status_counts: Counter[str] = Counter()
    operation_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    merchant_counts: Counter[str] = Counter()
    timestamps: list[datetime] = []

    for row in rows:
        amount_value = _safe_float(row.get("monto"))
        if amount_value is not None:
            amounts.append(amount_value)
            if row.get("es_internacional"):
                international_amounts.append(amount_value)

        estatus = row.get("estatus")
        if estatus is not None:
            status_counts[str(estatus)] += 1

        tipo_operacion = row.get("tipo_operacion")
        if tipo_operacion is not None:
            operation_counts[str(tipo_operacion)] += 1

        categoria_mcc = row.get("categoria_mcc")
        if categoria_mcc is not None:
            category_counts[str(categoria_mcc)] += 1

        comercio_nombre = row.get("comercio_nombre")
        if comercio_nombre is not None:
            merchant_counts[str(comercio_nombre)] += 1

        timestamp_value = _parse_datetime(row.get("fecha_hora") or row.get("created_at"))
        if timestamp_value is not None:
            timestamps.append(timestamp_value)

    row_count = len(rows)
    amount_total = round(sum(amounts), 2) if amounts else 0
    amount_average = round(amount_total / len(amounts), 2) if amounts else 0
    amount_min = round(min(amounts), 2) if amounts else 0
    amount_max = round(max(amounts), 2) if amounts else 0
    international_count = len(international_amounts)
    international_amount_total = round(sum(international_amounts), 2) if international_amounts else 0
    international_ratio = round(international_count / row_count, 4) if row_count else 0

    first_transaction_at = min(timestamps).isoformat() if timestamps else None
    last_transaction_at = max(timestamps).isoformat() if timestamps else None

    return {
        "row_count": row_count,
        "amount_total": amount_total,
        "amount_average": amount_average,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "international_count": international_count,
        "international_amount_total": international_amount_total,
        "international_ratio": international_ratio,
        "status_counts": dict(status_counts.most_common()),
        "operation_counts": dict(operation_counts.most_common()),
        "category_counts": dict(category_counts.most_common(10)),
        "merchant_counts": dict(merchant_counts.most_common(10)),
        "first_transaction_at": first_transaction_at,
        "last_transaction_at": last_transaction_at,
        "recent_transactions": [
            _format_transaction_row(row) for row in sorted_rows[:10]
        ],
    }


def _build_message_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "row_count": 0,
            "role_counts": {},
            "first_message_at": None,
            "last_message_at": None,
            "recent_messages": [],
        }

    sorted_rows = _sort_rows_by_datetime(rows, "created_at")
    role_counts: Counter[str] = Counter()
    timestamps: list[datetime] = []

    for row in rows:
        role = row.get("role")
        if role is not None:
            role_counts[str(role)] += 1

        timestamp_value = _parse_datetime(row.get("created_at"))
        if timestamp_value is not None:
            timestamps.append(timestamp_value)

    first_message_at = min(timestamps).isoformat() if timestamps else None
    last_message_at = max(timestamps).isoformat() if timestamps else None

    return {
        "row_count": len(rows),
        "role_counts": dict(role_counts.most_common()),
        "first_message_at": first_message_at,
        "last_message_at": last_message_at,
        "recent_messages": [_format_message_row(row) for row in sorted_rows[:10]],
    }


# Raw SQL access is intentionally not exposed right now.
# The MCP stays read-only and user-scoped through the select-based tools below.


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
        order_by="updated_at",
        ascending=False,
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
        order_by="created_at",
        ascending=False,
    )


@mcp.tool()
def get_user_segment(
    user_id: Annotated[str, Field(description="UUID of the user to retrieve from user_segments.")],
) -> dict[str, Any]:
    """
    Fetch the latest segment row for a single user from user_segments.

    Returns cluster and scoring features: segmento, cluster_id, score_buro_z,
    fail_rate_z, max_utilizacion_z, ingreso_z, ratio_servicios_digitales_z,
    has_inversion_z, nomina_domiciliada_z, has_cuenta_negocios_z, updated_at.
    """
    return supabase_rest_client.select_rows(
        table_name="user_segments",
        schema="public",
        filters={"user_id": user_id},
        limit=1,
        order_by="updated_at",
        ascending=False,
    )


@mcp.tool()
def get_user_transactions(
    user_id: Annotated[str, Field(description="UUID of the user whose transactions to fetch.")],
    estatus: Annotated[
        str | None,
        Field(description="Optional exact filter for transaction estatus."),
    ] = None,
    tipo_operacion: Annotated[
        str | None,
        Field(description="Optional exact filter for tipo_operacion."),
    ] = None,
    categoria_mcc: Annotated[
        str | None,
        Field(description="Optional exact filter for categoria_mcc."),
    ] = None,
    es_internacional: Annotated[
        bool | None,
        Field(description="Optional exact filter for international transactions."),
    ] = None,
    limit: int = 25,
) -> dict[str, Any]:
    """
    Fetch the latest transactions for a user from user_transactions.

    Returns raw rows plus a derived spending summary so downstream tools can use
    either the samples or the aggregates without raw SQL.
    """
    filters: dict[str, str | int | bool] = {"user_id": user_id}
    if estatus is not None:
        filters["estatus"] = estatus
    if tipo_operacion is not None:
        filters["tipo_operacion"] = tipo_operacion
    if categoria_mcc is not None:
        filters["categoria_mcc"] = categoria_mcc
    if es_internacional is not None:
        filters["es_internacional"] = es_internacional

    result = supabase_rest_client.select_rows(
        table_name="user_transactions",
        schema="public",
        filters=filters,
        limit=limit,
        order_by="fecha_hora",
        ascending=False,
    )

    rows = result.get("rows", [])
    return {
        "ok": result.get("ok", False),
        "user_id": user_id,
        "filters": filters,
        "limit": limit,
        "row_count": result.get("row_count", len(rows)),
        "summary": _build_transaction_summary(rows),
        "rows": rows,
        "error": result.get("error"),
    }


@mcp.tool()
def get_user_context_snapshot(
    user_id: Annotated[str, Field(description="UUID of the user to summarize.")],
    transaction_limit: Annotated[
        int,
        Field(description="How many latest transactions to include in the summary sample."),
    ] = 50,
    message_limit: Annotated[
        int,
        Field(description="How many latest chat messages to include in the summary sample."),
    ] = 50,
) -> dict[str, Any]:
    """
    Build a compact user snapshot from profile, segment, transactions, and chat.

    This is the main cross-table read tool for models that need a broader view
    of the user's behavior without exposing arbitrary SQL.
    """
    profile_result = supabase_rest_client.select_rows(
        table_name="user_profiles",
        schema="public",
        filters={"user_id": user_id},
        limit=1,
        order_by="updated_at",
        ascending=False,
    )
    segment_result = supabase_rest_client.select_rows(
        table_name="user_segments",
        schema="public",
        filters={"user_id": user_id},
        limit=1,
        order_by="updated_at",
        ascending=False,
    )
    transaction_result = supabase_rest_client.select_rows(
        table_name="user_transactions",
        schema="public",
        filters={"user_id": user_id},
        limit=transaction_limit,
        order_by="fecha_hora",
        ascending=False,
    )
    message_result = supabase_rest_client.select_rows(
        table_name="chat_messages",
        schema="public",
        filters={"user_id": user_id},
        limit=message_limit,
        order_by="created_at",
        ascending=False,
    )

    errors: dict[str, str] = {}
    if not profile_result.get("ok", False):
        errors["profile"] = str(profile_result.get("error"))
    if not segment_result.get("ok", False):
        errors["segment"] = str(segment_result.get("error"))
    if not transaction_result.get("ok", False):
        errors["transactions"] = str(transaction_result.get("error"))
    if not message_result.get("ok", False):
        errors["messages"] = str(message_result.get("error"))

    profile_rows = profile_result.get("rows", [])
    segment_rows = segment_result.get("rows", [])
    transaction_rows = transaction_result.get("rows", [])
    message_rows = message_result.get("rows", [])

    transactions_summary = _build_transaction_summary(transaction_rows)
    messages_summary = _build_message_summary(message_rows)

    latest_activity_candidates: list[datetime] = []
    for timestamp_value in (
        transactions_summary.get("last_transaction_at"),
        messages_summary.get("last_message_at"),
    ):
        parsed_value = _parse_datetime(timestamp_value)
        if parsed_value is not None:
            latest_activity_candidates.append(parsed_value)

    latest_activity_at = (
        max(latest_activity_candidates).isoformat() if latest_activity_candidates else None
    )

    return {
        "ok": not errors,
        "user_id": user_id,
        "transaction_limit": transaction_limit,
        "message_limit": message_limit,
        "profile": profile_rows[0] if profile_rows else None,
        "segment": segment_rows[0] if segment_rows else None,
        "transactions_summary": transactions_summary,
        "messages_summary": messages_summary,
        "latest_activity_at": latest_activity_at,
        "errors": errors,
    }


@mcp.tool()
def save_user_insight(
    user_id: Annotated[str, Field(description="UUID del usuario autenticado.")],
    trigger_type: Annotated[
        str,
        Field(
            description=(
                "Tipo de trigger que originó el insight. Ej: cargo_fallido_reciente, "
                "credito_al_limite, sin_login_reciente, nomina_sin_inversion, "
                "suscripcion_sin_uso, gasto_inusual, baja_satisfaccion."
            )
        ),
    ],
    insight_text: Annotated[str, Field(description="Texto completo del insight personalizado.")],
    segment_name: Annotated[str | None, Field(description="Nombre del segmento del usuario.")] = None,
    insight_type: Annotated[
        str | None,
        Field(
            description=(
                "Tipo de insight. Uno de: upsell_investment, upsell_digital, upsell_business, "
                "retention_reactivation, retention_churn_risk, loyalty_payroll, financial_stress_relief."
            )
        ),
    ] = None,
    cluster: Annotated[int | None, Field(description="Número de cluster del modelo ML.")] = None,
    score_buro: Annotated[int | None, Field(description="Score de buró del usuario.")] = None,
    utilizacion_credito_pct: Annotated[
        float | None, Field(description="Porcentaje de utilización de crédito.")
    ] = None,
    gasto_total_anual_mxn: Annotated[
        float | None, Field(description="Gasto total anual en MXN.")
    ] = None,
    tasa_fallos_pct: Annotated[
        float | None, Field(description="Tasa de pagos fallidos en porcentaje.")
    ] = None,
) -> dict[str, Any]:
    """
    Persiste un insight generado por el modelo en la tabla user_insights.

    Llamar SIEMPRE después de obtener el insight de call_model_endpoint.
    Retorna el id y created_at del registro creado.
    """
    row = {
        "user_id": user_id,
        "trigger_type": trigger_type,
        "insight_text": insight_text,
        "segment_name": segment_name,
        "insight_type": insight_type,
        "cluster": cluster,
        "score_buro": score_buro,
        "utilizacion_credito_pct": utilizacion_credito_pct,
        "gasto_total_anual_mxn": gasto_total_anual_mxn,
        "tasa_fallos_pct": tasa_fallos_pct,
    }
    row = {key: value for key, value in row.items() if value is not None}
    result = supabase_rest_client.insert_row("user_insights", row, schema="public")
    inserted_row = result.get("rows", [{}])[0] if result.get("rows") else {}

    return {
        "ok": result.get("ok", False),
        "id": inserted_row.get("id"),
        "created_at": inserted_row.get("created_at"),
        "row": inserted_row,
        "error": result.get("error"),
    }


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