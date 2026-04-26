from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import time
from typing import Any, Annotated, Literal

import httpx
from pydantic import Field

from fastmcp import FastMCP
from hey_i_mcp.analytics_dashboards import (
    build_behavior_dashboard,
    build_benchmark_dashboard,
    build_credit_dashboard,
    build_savings_dashboard,
    build_spending_dashboard,
)
from hey_i_mcp.database import DatabaseClient
from hey_i_mcp.supabase_api import SupabaseRestClient


mcp = FastMCP("Hey i MCP")
database_client = DatabaseClient()
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
# The MCP stays user-scoped through the select-based tools and the single insight write path below.


@mcp.tool()
def supabase_select_rows(
    table_name: Annotated[
        Literal["user_profiles", "chat_messages"],
        Field(
            description=(
                "Table to query. Allowed: user_profiles or chat_messages for ad hoc exact-match selects."
            )
        ),
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
    limit: Annotated[int, Field(description="Maximum number of rows to return.")] = 5,
) -> dict[str, Any]:
    return supabase_rest_client.select_rows(
        table_name=table_name,
        schema=schema,
        filters=filters,
        limit=limit,
    )


@mcp.tool()
def get_user_profile(
    user_id: Annotated[
        str,
        Field(description="UUID of the user whose profile should be retrieved."),
    ],
    top_merchants_limit: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_user_profile."),
    ] = None,
    months_back: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_user_profile."),
    ] = None,
    target_category: Annotated[
        str | None,
        Field(description="Compatibility-only. Ignored by get_user_profile."),
    ] = None,
    reduction_pct: Annotated[
        float | str | None,
        Field(description="Compatibility-only. Ignored by get_user_profile."),
    ] = None,
    weeks_back: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_user_profile."),
    ] = None,
) -> dict[str, Any]:
    """
    Fetch the latest profile row for a single user from user_profiles, ordered by updated_at DESC.

    Returns the full row with demographic, financial, and product-usage fields.
    """
    _ = top_merchants_limit, months_back, target_category, reduction_pct, weeks_back
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
    user_id: Annotated[str, Field(description="UUID of the user whose messages should be fetched.")],
    role: Annotated[
        Literal["user", "assistant"] | None,
        Field(description="Optional exact role filter. Use 'user' or 'assistant'; omit for all messages."),
    ] = None,
    limit: Annotated[int, Field(description="Maximum number of messages to return, newest first.")] = 20,
    top_merchants_limit: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_user_chat_messages."),
    ] = None,
    months_back: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_user_chat_messages."),
    ] = None,
    target_category: Annotated[
        str | None,
        Field(description="Compatibility-only. Ignored by get_user_chat_messages."),
    ] = None,
    reduction_pct: Annotated[
        float | str | None,
        Field(description="Compatibility-only. Ignored by get_user_chat_messages."),
    ] = None,
    weeks_back: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_user_chat_messages."),
    ] = None,
) -> dict[str, Any]:
    """
    Fetch the most recent chat messages for a given user from chat_messages, ordered by created_at DESC.

    Returns the raw Supabase payload, including rows, row_count, ok, and the applied filters.
    """
    _ = top_merchants_limit, months_back, target_category, reduction_pct, weeks_back
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


# @mcp.tool()  # DISABLED — sleeping get_user_segment
def get_user_segment(
    user_id: Annotated[
        str,
        Field(description="UUID of the user whose segment should be retrieved."),
    ],
    transaction_limit: Annotated[
        int | str | None,
        Field(
            description="Compatibility-only. Ignored by get_user_segment.",
        ),
    ] = None,
    message_limit: Annotated[
        int | str | None,
        Field(
            description="Compatibility-only. Ignored by get_user_segment.",
        ),
    ] = None,
) -> dict[str, Any]:
    """
    Fetch the latest segment row for a single user from user_segments, ordered by updated_at DESC.

    Returns the segment label, cluster id, and z-score features used by the model.
    """
    _ = transaction_limit, message_limit
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
    user_id: Annotated[
        str,
        Field(description="UUID of the user whose transactions should be fetched."),
    ],
    estatus: Annotated[
        str | None,
        Field(description="Optional exact status filter, such as 'completada' or 'rechazada'."),
    ] = None,
    tipo_operacion: Annotated[
        str | None,
        Field(description="Optional exact transaction type filter, such as 'compra' or 'abono_inversion'."),
    ] = None,
    categoria_mcc: Annotated[
        str | None,
        Field(description="Optional exact MCC/category filter."),
    ] = None,
    es_internacional: Annotated[
        bool | None,
        Field(description="Optional exact boolean filter for international transactions."),
    ] = None,
    role: Annotated[
        str | None,
        Field(description="Compatibility-only. Ignored by get_user_transactions."),
    ] = None,
    limit: Annotated[int, Field(description="Maximum number of transactions to return, newest first.")] = 25,
) -> dict[str, Any]:
    """
    Fetch the latest transactions for a user from user_transactions, ordered by fecha_hora DESC.

    Returns raw rows plus a derived summary with amount totals, averages, min/max,
    international ratio, counts by status/type/category/merchant, and recent samples.
    """
    _ = role
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


# @mcp.tool()  # DISABLED — sleeping get_user_context_snapshot
def get_user_context_snapshot(
    user_id: Annotated[
        str,
        Field(description="UUID of the user to summarize."),
    ],
    transaction_limit: Annotated[
        int | str,
        Field(
            description="Maximum number of recent transactions to include in the snapshot.",
        ),
    ] = 50,
    message_limit: Annotated[
        int | str,
        Field(
            description="Maximum number of recent chat messages to include in the snapshot.",
        ),
    ] = 50,
) -> dict[str, Any]:
    """
    Build a compact multi-source snapshot from profile, segment, transactions, and chat.

    The result combines the latest profile and segment rows with summarized activity data,
    plus latest_activity_at and per-source errors when a query fails.

    This is a read-only composition step. If you need to persist a generated insight
    based on this snapshot, use save_user_insight separately.
    """
    transaction_limit = int(transaction_limit)
    message_limit = int(message_limit)

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
    # Anchor the snapshot with the newest visible activity across messages and transactions.
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
def get_spending_dashboard(
    user_id: Annotated[
        str,
        Field(description="UUID of the user whose spending dashboard should be built."),
    ],
    top_merchants_limit: Annotated[
        int | str | None,
        Field(description="How many merchants to include in the top merchants chart."),
    ] = 5,
    months_back: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_spending_dashboard."),
    ] = None,
    target_category: Annotated[
        str | None,
        Field(description="Compatibility-only. Ignored by get_spending_dashboard."),
    ] = None,
    reduction_pct: Annotated[
        float | str | None,
        Field(description="Compatibility-only. Ignored by get_spending_dashboard."),
    ] = None,
    weeks_back: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_spending_dashboard."),
    ] = None,
    limit: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_spending_dashboard."),
    ] = None,
    estatus: Annotated[
        str | None,
        Field(description="Compatibility-only. Ignored by get_spending_dashboard."),
    ] = None,
    categoria_mcc: Annotated[
        str | None,
        Field(description="Compatibility-only. Ignored by get_spending_dashboard."),
    ] = None,
    tipo_operacion: Annotated[
        str | None,
        Field(description="Compatibility-only. Ignored by get_spending_dashboard."),
    ] = None,
    es_internacional: Annotated[
        bool | None,
        Field(description="Compatibility-only. Ignored by get_spending_dashboard."),
    ] = None,
    role: Annotated[
        str | None,
        Field(description="Compatibility-only. Ignored by get_spending_dashboard."),
    ] = None,
) -> dict[str, Any]:
    """
    Build the spending and categories dashboard.

    Returns chart-ready JSON with donut, comparison bar, daily line, heatmap, and top merchants charts.
    """
    _ = (
        months_back,
        target_category,
        reduction_pct,
        weeks_back,
        limit,
        estatus,
        categoria_mcc,
        tipo_operacion,
        es_internacional,
        role,
    )
    try:
        normalized_top_merchants_limit = int(top_merchants_limit) if top_merchants_limit is not None else 5
    except (TypeError, ValueError):
        normalized_top_merchants_limit = 5

    return build_spending_dashboard(
        supabase_rest_client,
        user_id=user_id,
        top_merchants_limit=normalized_top_merchants_limit,
    )


@mcp.tool()
def get_credit_dashboard(
    user_id: Annotated[
        str,
        Field(description="UUID of the user whose credit health dashboard should be built."),
    ],
    top_merchants_limit: Annotated[
        int | str | None,
        Field(
            description="Compatibility-only. Ignored by get_credit_dashboard.",
        ),
    ] = None,
    months_back: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_credit_dashboard."),
    ] = None,
    target_category: Annotated[
        str | None,
        Field(description="Compatibility-only. Ignored by get_credit_dashboard."),
    ] = None,
    reduction_pct: Annotated[
        float | str | None,
        Field(description="Compatibility-only. Ignored by get_credit_dashboard."),
    ] = None,
    weeks_back: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_credit_dashboard."),
    ] = None,
) -> dict[str, Any]:
    """
    Build the credit and financial health dashboard.

    Returns the utilization gauge, score history, and an explicit placeholder for debt vs limit.
    """
    _ = top_merchants_limit, months_back, target_category, reduction_pct, weeks_back
    return build_credit_dashboard(supabase_rest_client, user_id=user_id)


@mcp.tool()
def get_savings_dashboard(
    user_id: Annotated[
        str,
        Field(description="UUID of the user whose savings dashboard should be built."),
    ],
    months_back: Annotated[
        int | str | None,
        Field(description="Number of months to include in the monthly charts."),
    ] = 6,
    target_category: Annotated[
        str | None,
        Field(description="Optional category to use for the projection chart."),
    ] = None,
    reduction_pct: Annotated[
        float | str | None,
        Field(description="Percent reduction scenario used for the projection chart."),
    ] = 10.0,
    top_merchants_limit: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_savings_dashboard."),
    ] = None,
    weeks_back: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_savings_dashboard."),
    ] = None,
) -> dict[str, Any]:
    """
    Build the savings and investment dashboard.

    Returns investment growth, income vs spend, and a category reduction scenario.
    """
    _ = top_merchants_limit, weeks_back
    try:
        normalized_months_back = int(months_back) if months_back is not None else 6
    except (TypeError, ValueError):
        normalized_months_back = 6

    try:
        normalized_reduction_pct = float(reduction_pct) if reduction_pct is not None else 10.0
    except (TypeError, ValueError):
        normalized_reduction_pct = 10.0

    return build_savings_dashboard(
        supabase_rest_client,
        user_id=user_id,
        months_back=normalized_months_back,
        target_category=target_category,
        reduction_pct=normalized_reduction_pct,
    )


@mcp.tool()
def get_behavior_dashboard(
    user_id: Annotated[
        str,
        Field(description="UUID of the user whose behavior dashboard should be built."),
    ],
    weeks_back: Annotated[
        int | str | None,
        Field(description="How many weeks to include in the weekly frequency chart."),
    ] = 12,
    top_merchants_limit: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_behavior_dashboard."),
    ] = None,
    months_back: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_behavior_dashboard."),
    ] = None,
    target_category: Annotated[
        str | None,
        Field(description="Compatibility-only. Ignored by get_behavior_dashboard."),
    ] = None,
    reduction_pct: Annotated[
        float | str | None,
        Field(description="Compatibility-only. Ignored by get_behavior_dashboard."),
    ] = None,
) -> dict[str, Any]:
    """
    Build the behavior dashboard.

    Returns weekday/weekend spending, weekly transaction frequency, and activity heatmaps.
    """
    _ = top_merchants_limit, months_back, target_category, reduction_pct
    try:
        normalized_weeks_back = int(weeks_back) if weeks_back is not None else 12
    except (TypeError, ValueError):
        normalized_weeks_back = 12

    return build_behavior_dashboard(
        supabase_rest_client,
        user_id=user_id,
        weeks_back=normalized_weeks_back,
    )


@mcp.tool()
def get_benchmark_dashboard(
    user_id: Annotated[
        str,
        Field(description="UUID of the user whose segment benchmark should be built."),
    ],
    top_merchants_limit: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_benchmark_dashboard."),
    ] = None,
    months_back: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_benchmark_dashboard."),
    ] = None,
    target_category: Annotated[
        str | None,
        Field(description="Compatibility-only. Ignored by get_benchmark_dashboard."),
    ] = None,
    reduction_pct: Annotated[
        float | str | None,
        Field(description="Compatibility-only. Ignored by get_benchmark_dashboard."),
    ] = None,
    weeks_back: Annotated[
        int | str | None,
        Field(description="Compatibility-only. Ignored by get_benchmark_dashboard."),
    ] = None,
) -> dict[str, Any]:
    """
    Build the segment benchmark dashboard.

    Returns horizontal category bars and a 6-dimension radar chart.
    """
    _ = top_merchants_limit, months_back, target_category, reduction_pct, weeks_back
    return build_benchmark_dashboard(supabase_rest_client, user_id=user_id)


@mcp.tool()
def save_user_insight(
    user_id: Annotated[str, Field(description="UUID del usuario al que se le guardará el insight.")],
    trigger_type: Annotated[
        str,
        Field(
            description=(
                "Short trigger label that originated the insight. Use values like cargo_fallido_reciente, "
                "credito_al_limite, sin_login_reciente, nomina_sin_inversion, "
                "suscripcion_sin_uso, gasto_inusual, baja_satisfaccion."
            )
        ),
    ],
    insight_text: Annotated[str, Field(description="Full insight text to persist.")],
    segment_name: Annotated[str | None, Field(description="Optional segment label associated with the user.")] = None,
    insight_type: Annotated[
        str | None,
        Field(
            description=(
                "Optional insight category. Use one of: upsell_investment, upsell_digital, upsell_business, "
                "retention_reactivation, retention_churn_risk, loyalty_payroll, financial_stress_relief."
            )
        ),
    ] = None,
    cluster: Annotated[int | None, Field(description="Optional numeric cluster from the segmentation model.")] = None,
    score_buro: Annotated[int | None, Field(description="Optional bureau score used in the insight.")] = None,
    utilizacion_credito_pct: Annotated[
        float | None, Field(description="Credit utilization as a decimal percentage, for example 0.38 for 38%.")
    ] = None,
    gasto_total_anual_mxn: Annotated[
        float | None, Field(description="Total annual spend in MXN.")
    ] = None,
    tasa_fallos_pct: Annotated[
        float | None, Field(description="Failure rate as a decimal percentage, for example 0.02 for 2%.")
    ] = None,
) -> dict[str, Any]:
    """
    Persist a generated insight into user_insights through the SQLAlchemy database layer.

    Call this after generating the insight with call_model_endpoint. Returns ok, id,
    created_at, and error when the insert fails.
    """
    return database_client.insert_insight(
        {
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