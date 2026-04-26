from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import fmean
from typing import Any, Callable

from hey_i_mcp.supabase_api import SupabaseRestClient


WEEKDAY_LABELS = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]

SPEND_OPERATIONS = {
    "compra",
    "cargo_recurrente",
    "pago",
    "pago_servicio",
    "pago_credito",
    "retiro_cajero",
}
INCOME_OPERATIONS = {"transf_entrada"}
INVESTMENT_OPERATIONS = {"abono_inversion"}

TransactionRecord = tuple[datetime, float, dict[str, Any]]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_money(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _round_pct(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _normalized_key(value: Any) -> str:
    if value is None:
        return ""

    normalized_value = str(value).strip().lower()
    if normalized_value in {"", "none", "null", "nan"}:
        return ""

    return normalized_value


def _display_label(value: Any, fallback: str) -> str:
    if value is None:
        return fallback

    text = str(value).strip()
    if not text or _normalized_key(text) == "":
        return fallback

    return text


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


def _transaction_timestamp(row: dict[str, Any]) -> datetime | None:
    return _parse_datetime(row.get("fecha_hora") or row.get("created_at"))


def _transaction_amount(row: dict[str, Any]) -> float | None:
    return _safe_float(row.get("monto"))


def _transaction_status(row: dict[str, Any]) -> str:
    return _normalized_key(row.get("estatus"))


def _transaction_operation(row: dict[str, Any]) -> str:
    return _normalized_key(row.get("tipo_operacion"))


def _transaction_category(row: dict[str, Any]) -> str:
    return _display_label(row.get("categoria_mcc"), "Sin categoría")


def _transaction_merchant(row: dict[str, Any]) -> str:
    return _display_label(row.get("comercio_nombre"), "Sin comercio")


def _is_completed(row: dict[str, Any], _dt: datetime, _amount: float) -> bool:
    return _transaction_status(row) == "completada"


def _is_spend(row: dict[str, Any], _dt: datetime, _amount: float) -> bool:
    return _is_completed(row, _dt, _amount) and _transaction_operation(row) in SPEND_OPERATIONS


def _is_income(row: dict[str, Any], _dt: datetime, _amount: float) -> bool:
    return _is_completed(row, _dt, _amount) and _transaction_operation(row) in INCOME_OPERATIONS


def _is_investment(row: dict[str, Any], _dt: datetime, _amount: float) -> bool:
    return _is_completed(row, _dt, _amount) and _transaction_operation(row) in INVESTMENT_OPERATIONS


def _score_buro_to_pct(score: Any) -> float | None:
    score_value = _safe_float(score)
    if score_value is None:
        return None

    return _clamp(((score_value - 300.0) / 550.0) * 100.0)


def _utilization_proxy_from_z(score_z: Any) -> float | None:
    z_value = _safe_float(score_z)
    if z_value is None:
        return None

    return _clamp(50.0 + (z_value * 20.0))


def _percent_from_decimal(value: Any) -> float | None:
    decimal_value = _safe_float(value)
    if decimal_value is None:
        return None

    if abs(decimal_value) <= 1.0:
        return _clamp(decimal_value * 100.0)

    return _clamp(decimal_value)


def _month_start(moment: datetime) -> datetime:
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _shift_months(moment: datetime, months: int) -> datetime:
    month_index = moment.year * 12 + (moment.month - 1) + months
    year, month_zero_based = divmod(month_index, 12)
    return moment.replace(
        year=year,
        month=month_zero_based + 1,
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def _month_sequence(moment: datetime, months_back: int) -> list[datetime]:
    months_back = max(1, months_back)
    first_month = _shift_months(_month_start(moment), -(months_back - 1))
    return [_shift_months(first_month, index) for index in range(months_back)]


def _week_sequence(moment: datetime, weeks_back: int) -> list[datetime.date]:
    weeks_back = max(1, weeks_back)
    current_week_start = moment.date() - timedelta(days=moment.weekday())
    first_week_start = current_week_start - timedelta(weeks=weeks_back - 1)
    return [first_week_start + timedelta(weeks=index) for index in range(weeks_back)]


def _day_sequence(moment: datetime, days_back: int) -> list[datetime.date]:
    days_back = max(1, days_back)
    first_day = moment.date() - timedelta(days=days_back - 1)
    return [first_day + timedelta(days=index) for index in range(days_back)]


def _select_rows(
    client: SupabaseRestClient,
    table_name: str,
    *,
    filters: dict[str, str | int | bool] | None = None,
    limit: int = 1000,
    order_by: str | None = None,
    ascending: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = client.select_rows(
        table_name=table_name,
        schema="public",
        filters=filters,
        limit=limit,
        order_by=order_by,
        ascending=ascending,
    )
    return result, list(result.get("rows") or [])


def _transaction_records(
    rows: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any], datetime, float], bool] | None = None,
) -> list[TransactionRecord]:
    records: list[TransactionRecord] = []

    for row in rows:
        timestamp = _transaction_timestamp(row)
        amount = _transaction_amount(row)
        if timestamp is None or amount is None:
            continue

        if predicate is not None and not predicate(row, timestamp, amount):
            continue

        records.append((timestamp, amount, row))

    return records


def _filter_records_between(
    records: list[TransactionRecord],
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[TransactionRecord]:
    filtered_records: list[TransactionRecord] = []
    for timestamp, amount, row in records:
        if start is not None and timestamp < start:
            continue
        if end is not None and timestamp >= end:
            continue
        filtered_records.append((timestamp, amount, row))
    return filtered_records


def _aggregate_records(
    records: list[TransactionRecord],
    *,
    key_fn: Callable[[dict[str, Any], datetime, float], str],
    start: datetime | None = None,
    end: datetime | None = None,
    predicate: Callable[[dict[str, Any], datetime, float], bool] | None = None,
    metric: str = "amount",
) -> tuple[dict[str, float], Counter[str]]:
    totals: defaultdict[str, float] = defaultdict(float)
    counts: Counter[str] = Counter()

    for timestamp, amount, row in records:
        if start is not None and timestamp < start:
            continue
        if end is not None and timestamp >= end:
            continue
        if predicate is not None and not predicate(row, timestamp, amount):
            continue

        key = key_fn(row, timestamp, amount)
        totals[key] += 1.0 if metric == "count" else amount
        counts[key] += 1

    return dict(totals), counts


def _aggregate_heatmap(
    records: list[TransactionRecord],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    predicate: Callable[[dict[str, Any], datetime, float], bool] | None = None,
    metric: str = "amount",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grid: dict[tuple[int, int], float] = {(day, hour): 0.0 for day in range(7) for hour in range(24)}

    for timestamp, amount, row in records:
        if start is not None and timestamp < start:
            continue
        if end is not None and timestamp >= end:
            continue
        if predicate is not None and not predicate(row, timestamp, amount):
            continue

        grid[(timestamp.weekday(), timestamp.hour)] += 1.0 if metric == "count" else amount

    points = [
        {
            "day_of_week": WEEKDAY_LABELS[day],
            "day_index": day,
            "hour": hour,
            "value": _round_money(grid[(day, hour)]) if metric == "amount" else int(grid[(day, hour)]),
        }
        for day in range(7)
        for hour in range(24)
    ]

    peak_key, peak_value = max(grid.items(), key=lambda item: item[1]) if grid else ((0, 0), 0.0)
    return points, {
        "peak_day": WEEKDAY_LABELS[peak_key[0]],
        "peak_hour": peak_key[1],
        "peak_value": _round_money(peak_value) if metric == "amount" else int(peak_value),
    }


def _chart_payload(
    chart_id: str,
    chart_type: str,
    title: str,
    subtitle: str = "",
    *,
    unit: str | None = None,
    value_format: dict[str, Any] | None = None,
    available: bool = True,
    reason: str | None = None,
    series: list[dict[str, Any]] | None = None,
    categories: list[str] | None = None,
    points: list[dict[str, Any]] | None = None,
    thresholds: list[dict[str, Any]] | None = None,
    value: float | int | None = None,
    orientation: str | None = None,
    dimensions: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    chart: dict[str, Any] = {
        "id": chart_id,
        "type": chart_type,
        "title": title,
        "subtitle": subtitle,
        "available": available,
    }

    if unit is not None:
        chart["unit"] = unit
    if value_format is not None:
        chart["value_format"] = value_format
    if reason is not None:
        chart["reason"] = reason
    if series is not None:
        chart["series"] = series
    if categories is not None:
        chart["categories"] = categories
    if points is not None:
        chart["points"] = points
    if thresholds is not None:
        chart["thresholds"] = thresholds
    if value is not None:
        chart["value"] = _round_pct(value) if unit == "%" else _round_money(value)
    if orientation is not None:
        chart["orientation"] = orientation
    if dimensions is not None:
        chart["dimensions"] = dimensions
    if summary is not None:
        chart["summary"] = summary
    if meta is not None:
        chart["meta"] = meta
    if notes is not None:
        chart["notes"] = notes

    return chart


def _dashboard_payload(
    dashboard: str,
    user_id: str,
    *,
    charts: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": not errors,
        "dashboard": dashboard,
        "user_id": user_id,
        "generated_at": _now_utc().isoformat(),
        "charts": charts,
        "summary": summary or {},
        "warnings": warnings or [],
        "errors": errors or [],
    }

    if meta is not None:
        payload["meta"] = meta

    return payload


def _empty_dashboard(dashboard: str, user_id: str, error: str) -> dict[str, Any]:
    return _dashboard_payload(dashboard, user_id, charts=[], errors=[error])


def _series_from_totals(labels: list[str], totals: dict[str, float], name: str) -> dict[str, Any]:
    return {
        "name": name,
        "data": [{"label": label, "value": _round_money(totals.get(label, 0.0)) or 0.0} for label in labels],
    }


def _items_from_totals(
    labels: list[str],
    totals: dict[str, float],
    counts: Counter[str] | None = None,
    *,
    total_value: float | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for label in labels:
        amount = totals.get(label, 0.0)
        item: dict[str, Any] = {"label": label, "value": _round_money(amount) or 0.0}
        if counts is not None:
            item["count"] = int(counts.get(label, 0))
        if total_value:
            item["share_pct"] = round((amount / total_value) * 100.0, 2) if total_value else 0.0
        items.append(item)
    return items


def _months_as_keys(month_starts: list[datetime]) -> list[str]:
    return [month_start.strftime("%Y-%m") for month_start in month_starts]


def _build_spending_dashboard(
    client: SupabaseRestClient,
    *,
    user_id: str,
    top_merchants_limit: int = 5,
) -> dict[str, Any]:
    transactions_result, rows = _select_rows(
        client,
        "user_transactions",
        filters={"user_id": user_id},
        limit=1000,
        order_by="fecha_hora",
        ascending=False,
    )
    if not transactions_result.get("ok", False):
        return _empty_dashboard(
            "spending_dashboard",
            user_id,
            str(transactions_result.get("error") or "Failed to load transactions."),
        )

    top_merchants_limit = max(1, int(top_merchants_limit))
    now = _now_utc()
    current_month_start = _month_start(now)
    next_month_start = _shift_months(current_month_start, 1)
    previous_month_start = _shift_months(current_month_start, -1)
    daily_start_date = now.date() - timedelta(days=29)

    records = _transaction_records(rows)
    spend_records = [record for record in records if _is_spend(record[2], record[0], record[1])]

    current_month_spend = _filter_records_between(spend_records, current_month_start, next_month_start)
    previous_month_spend = _filter_records_between(
        spend_records,
        previous_month_start,
        current_month_start,
    )

    current_category_totals, current_category_counts = _aggregate_records(
        current_month_spend,
        key_fn=lambda row, _ts, _amount: _transaction_category(row),
    )
    previous_category_totals, _ = _aggregate_records(
        previous_month_spend,
        key_fn=lambda row, _ts, _amount: _transaction_category(row),
    )

    category_labels = sorted(
        set(current_category_totals) | set(previous_category_totals),
        key=lambda label: (
            current_category_totals.get(label, 0.0),
            previous_category_totals.get(label, 0.0),
            label,
        ),
        reverse=True,
    )

    current_month_total = sum(current_category_totals.values())
    previous_month_total = sum(previous_category_totals.values())

    donut_chart = _chart_payload(
        "spending_category_donut",
        "donut",
        "Gasto por categoría este mes",
        "Sólo incluye transacciones completadas de salida.",
        unit="MXN",
        value_format={"kind": "currency", "currency": "MXN", "decimals": 2},
        available=bool(category_labels),
        series=[
            {
                "name": "Gasto",
                "data": _items_from_totals(
                    category_labels,
                    current_category_totals,
                    current_category_counts,
                    total_value=current_month_total,
                ),
            }
        ],
        summary={
            "total_mxn": _round_money(current_month_total) or 0.0,
            "category_count": len(category_labels),
            "top_category": category_labels[0] if category_labels else None,
        },
        meta={
            "period": {
                "start": current_month_start.isoformat(),
                "end": next_month_start.isoformat(),
                "label": "current_month",
            },
        },
        notes=["Las categorías vacías se omiten."] if category_labels else ["No hay gasto completado en el mes actual."],
    )

    comparison_chart = _chart_payload(
        "spending_category_month_comparison",
        "bar",
        "Este mes vs mes anterior por categoría",
        "Barras agrupadas con gasto completado por categoría.",
        unit="MXN",
        value_format={"kind": "currency", "currency": "MXN", "decimals": 2},
        available=bool(category_labels),
        orientation="vertical",
        categories=category_labels,
        series=[
            _series_from_totals(category_labels, current_category_totals, "Este mes"),
            _series_from_totals(category_labels, previous_category_totals, "Mes anterior"),
        ],
        summary={
            "current_month_total_mxn": _round_money(current_month_total) or 0.0,
            "previous_month_total_mxn": _round_money(previous_month_total) or 0.0,
            "delta_mxn": _round_money(current_month_total - previous_month_total) or 0.0,
            "delta_pct": (
                round(((current_month_total - previous_month_total) / previous_month_total) * 100.0, 2)
                if previous_month_total
                else None
            ),
        },
        meta={
            "period": {
                "current_month_start": current_month_start.isoformat(),
                "previous_month_start": previous_month_start.isoformat(),
            },
        },
    )

    daily_records = _filter_records_between(
        spend_records,
        datetime.combine(daily_start_date, datetime.min.time(), tzinfo=timezone.utc),
        _shift_months(current_month_start, 1),
    )
    daily_totals, _ = _aggregate_records(
        daily_records,
        key_fn=lambda _row, timestamp, _amount: timestamp.date().isoformat(),
    )
    day_sequence = _day_sequence(now, 30)
    daily_points = [
        {"x": day.isoformat(), "y": _round_money(daily_totals.get(day.isoformat(), 0.0)) or 0.0}
        for day in day_sequence
    ]

    daily_line_chart = _chart_payload(
        "spending_daily_last_30_days",
        "line",
        "Gasto diario en los últimos 30 días",
        "Serie diaria con ceros explícitos para días sin gasto.",
        unit="MXN",
        value_format={"kind": "currency", "currency": "MXN", "decimals": 2},
        available=True,
        series=[{"name": "Gasto diario", "data": daily_points}],
        summary={
            "total_mxn": _round_money(sum(point["y"] for point in daily_points)) or 0.0,
            "average_daily_mxn": _round_money(
                sum(point["y"] for point in daily_points) / len(daily_points)
            )
            if daily_points
            else 0.0,
            "days": len(daily_points),
        },
        meta={
            "period": {
                "start": day_sequence[0].isoformat() if day_sequence else None,
                "end": day_sequence[-1].isoformat() if day_sequence else None,
                "window_days": 30,
            },
        },
    )

    spend_heatmap_points, spend_heatmap_summary = _aggregate_heatmap(
        spend_records,
        start=datetime.combine(daily_start_date, datetime.min.time(), tzinfo=timezone.utc),
        end=_shift_months(current_month_start, 1),
        predicate=None,
        metric="amount",
    )
    spend_heatmap_chart = _chart_payload(
        "spending_heatmap_weekday_hour",
        "heatmap",
        "Heatmap de gasto por día de semana y hora",
        "Las coordenadas día/hora se derivan de fecha_hora porque la tabla no las expone directamente.",
        unit="MXN",
        value_format={"kind": "currency", "currency": "MXN", "decimals": 2},
        available=True,
        points=spend_heatmap_points,
        summary=spend_heatmap_summary,
        meta={
            "derived_fields": ["dia_semana", "hora_del_dia"],
            "source_field": "fecha_hora",
            "window": {
                "start": day_sequence[0].isoformat() if day_sequence else None,
                "end": day_sequence[-1].isoformat() if day_sequence else None,
            },
        },
    )

    merchant_totals, merchant_counts = _aggregate_records(
        current_month_spend,
        key_fn=lambda row, _ts, _amount: _transaction_merchant(row),
    )
    merchant_labels = sorted(
        merchant_totals,
        key=lambda label: (merchant_totals.get(label, 0.0), merchant_counts.get(label, 0), label),
        reverse=True,
    )[:top_merchants_limit]
    merchant_items = []
    for label in merchant_labels:
        amount = merchant_totals.get(label, 0.0)
        count = merchant_counts.get(label, 0)
        merchant_items.append(
            {
                "label": label,
                "value": _round_money(amount) or 0.0,
                "count": int(count),
                "average_ticket_mxn": _round_money(amount / count) if count else 0.0,
            }
        )

    merchants_chart = _chart_payload(
        "spending_top_merchants",
        "bar",
        "Top 5 comercios donde más gastas",
        "Ranking de gasto completado en el mes actual.",
        unit="MXN",
        value_format={"kind": "currency", "currency": "MXN", "decimals": 2},
        available=bool(merchant_items),
        orientation="horizontal",
        series=[{"name": "Gasto", "data": merchant_items}],
        summary={
            "merchant_count": len(merchant_items),
            "top_merchant": merchant_items[0]["label"] if merchant_items else None,
            "top_merchant_mxn": merchant_items[0]["value"] if merchant_items else 0.0,
        },
        notes=["Sólo se cuentan transacciones completadas de salida."],
    )

    summary = {
        "current_month_spend_mxn": _round_money(current_month_total) or 0.0,
        "previous_month_spend_mxn": _round_money(previous_month_total) or 0.0,
        "delta_mxn": _round_money(current_month_total - previous_month_total) or 0.0,
        "category_count": len(category_labels),
        "merchant_count": len(merchant_items),
        "spend_transaction_count": len(current_month_spend),
    }

    return _dashboard_payload(
        "spending_dashboard",
        user_id,
        charts=[donut_chart, comparison_chart, daily_line_chart, spend_heatmap_chart, merchants_chart],
        summary=summary,
        meta={"source_table": "user_transactions", "filters": {"user_id": user_id}},
    )


def _build_credit_dashboard(
    client: SupabaseRestClient,
    *,
    user_id: str,
) -> dict[str, Any]:
    profile_result, profile_rows = _select_rows(
        client,
        "user_profiles",
        filters={"user_id": user_id},
        limit=1,
        order_by="updated_at",
        ascending=False,
    )
    segment_result, segment_rows = _select_rows(
        client,
        "user_segments",
        filters={"user_id": user_id},
        limit=1,
        order_by="updated_at",
        ascending=False,
    )
    insights_result, insight_rows = _select_rows(
        client,
        "user_insights",
        filters={"user_id": user_id},
        limit=1000,
        order_by="created_at",
        ascending=True,
    )

    errors: list[str] = []
    if not profile_result.get("ok", False):
        errors.append(str(profile_result.get("error") or "Failed to load user profile."))
    if not segment_result.get("ok", False):
        errors.append(str(segment_result.get("error") or "Failed to load user segment."))
    if not insights_result.get("ok", False):
        errors.append(str(insights_result.get("error") or "Failed to load user insights."))

    if errors:
        return _dashboard_payload("credit_dashboard", user_id, charts=[], errors=errors)

    profile = profile_rows[0] if profile_rows else {}
    segment = segment_rows[0] if segment_rows else {}

    utilization_history: list[dict[str, Any]] = []
    for row in insight_rows:
        utilization_pct = _percent_from_decimal(row.get("utilizacion_credito_pct"))
        if utilization_pct is None:
            continue

        timestamp = _parse_datetime(row.get("created_at"))
        if timestamp is None:
            continue

        utilization_history.append({"x": timestamp.isoformat(), "y": _round_pct(utilization_pct) or 0.0})

    latest_utilization = utilization_history[-1]["y"] if utilization_history else None
    utilization_is_estimated = False
    if latest_utilization is None:
        latest_utilization = _utilization_proxy_from_z(segment.get("max_utilizacion_z"))
        utilization_is_estimated = latest_utilization is not None
    utilization_chart = _chart_payload(
        "credit_utilization_gauge",
        "gauge",
        "Utilización de crédito",
        "Zona roja arriba de 80%.",
        unit="%",
        value_format={"kind": "percent", "decimals": 2},
        available=latest_utilization is not None,
        value=latest_utilization,
        reason=(
            "Estimado desde user_segments.max_utilizacion_z porque no hay snapshots directos de utilizacion_credito_pct."
            if utilization_is_estimated
            else None
        ),
        thresholds=[
            {"label": "Verde", "min": 0, "max": 60, "color": "#16a34a"},
            {"label": "Amarillo", "min": 60, "max": 80, "color": "#f59e0b"},
            {"label": "Rojo", "min": 80, "max": 100, "color": "#dc2626"},
        ],
        summary={
            "utilization_pct": latest_utilization,
            "status": (
                "red"
                if latest_utilization is not None and latest_utilization >= 80
                else "yellow"
                if latest_utilization is not None and latest_utilization >= 60
                else "green"
                if latest_utilization is not None
                else None
            ),
            "history_points": len(utilization_history),
            "is_estimated": utilization_is_estimated,
            "basis": (
                "user_segments.max_utilizacion_z" if utilization_is_estimated else "user_insights.utilizacion_credito_pct"
            ),
        },
        meta={
            "source_field": "user_insights.utilizacion_credito_pct",
            "availability_reason": (
                "Disponible sólo si user_insights almacena snapshots con utilizacion_credito_pct."
            ),
            "proxy_field": "user_segments.max_utilizacion_z",
        },
        notes=["La fuente actual no expone esta métrica en user_transactions ni en user_profiles."]
        if latest_utilization is None
        else None,
    )

    score_history: list[dict[str, Any]] = []
    for row in insight_rows:
        score_buro = _safe_float(row.get("score_buro"))
        timestamp = _parse_datetime(row.get("created_at"))
        if score_buro is None or timestamp is None:
            continue
        score_history.append({"x": timestamp.isoformat(), "y": _round_money(score_buro) or 0.0})

    profile_score = _safe_float(profile.get("score_buro"))
    if not score_history and profile_score is not None:
        timestamp = _parse_datetime(profile.get("updated_at") or profile.get("created_at"))
        if timestamp is not None:
            score_history.append({"x": timestamp.isoformat(), "y": _round_money(profile_score) or 0.0})

    score_chart_available = bool(score_history)
    score_chart_reason = None if len(score_history) >= 2 else "No hay suficiente histórico de score buró; sólo hay un snapshot actual."
    current_snapshot_timestamp = _parse_datetime(profile.get("updated_at") or profile.get("created_at"))
    score_chart = _chart_payload(
        "credit_score_history",
        "line",
        "Evolución del score buró",
        "Histórico de snapshots; si sólo hay uno, la serie queda como referencia actual.",
        unit="score",
        value_format={"kind": "numeric", "decimals": 0},
        available=score_chart_available,
        reason=score_chart_reason,
        series=[{"name": "Score buró", "data": score_history}],
        summary={
            "latest_score_buro": _round_money(score_history[-1]["y"]) if score_history else None,
            "history_points": len(score_history),
            "current_score_buro": _round_money(profile_score) if profile_score is not None else None,
        },
        meta={
            "source_fields": ["user_insights.score_buro", "user_profiles.score_buro"],
            "current_snapshot_timestamp": current_snapshot_timestamp.isoformat()
            if current_snapshot_timestamp is not None
            else None,
        },
        notes=["No existe una tabla de histórico dedicada en la schema pública actual."]
        if len(score_history) < 2
        else None,
    )

    observed_products = []
    debt_chart = _chart_payload(
        "credit_debt_vs_limit_by_product",
        "bar",
        "Deuda vs límite por producto",
        "Requiere una fuente con saldo y límite por producto. La schema actual sólo expone producto_id en transacciones.",
        unit="MXN",
        value_format={"kind": "currency", "currency": "MXN", "decimals": 2},
        available=False,
        reason="No existe una fuente pública con saldo y límite por producto para calcular esta comparación sin inventar datos.",
        orientation="horizontal",
        series=[],
        summary={
            "needed_fields": ["producto_id", "saldo_actual", "limite_credito"],
            "observed_product_ids": observed_products,
        },
        notes=["Si agregas una tabla o RPC con saldos y límites, este chart se puede activar sin cambiar el frontend."],
    )

    summary = {
        "score_buro_current": profile_score,
        "segment_score_buro_z": segment.get("score_buro_z"),
        "segment_max_utilization_z": segment.get("max_utilizacion_z"),
        "latest_utilization_pct": latest_utilization,
        "score_history_points": len(score_history),
        "utilization_history_points": len(utilization_history),
        "segment_label": segment.get("segmento"),
    }

    return _dashboard_payload(
        "credit_dashboard",
        user_id,
        charts=[utilization_chart, score_chart, debt_chart],
        summary=summary,
        meta={"source_tables": ["user_profiles", "user_segments", "user_insights"]},
    )


def _build_savings_dashboard(
    client: SupabaseRestClient,
    *,
    user_id: str,
    months_back: int = 6,
    target_category: str | None = None,
    reduction_pct: float = 10.0,
) -> dict[str, Any]:
    profile_result, profile_rows = _select_rows(
        client,
        "user_profiles",
        filters={"user_id": user_id},
        limit=1,
        order_by="updated_at",
        ascending=False,
    )
    transactions_result, rows = _select_rows(
        client,
        "user_transactions",
        filters={"user_id": user_id},
        limit=1000,
        order_by="fecha_hora",
        ascending=False,
    )

    errors: list[str] = []
    if not profile_result.get("ok", False):
        errors.append(str(profile_result.get("error") or "Failed to load user profile."))
    if not transactions_result.get("ok", False):
        errors.append(str(transactions_result.get("error") or "Failed to load transactions."))
    if errors:
        return _dashboard_payload("savings_dashboard", user_id, charts=[], errors=errors)

    profile = profile_rows[0] if profile_rows else {}
    profile_income = _safe_float(profile.get("ingreso_mensual_mxn"))
    now = _now_utc()
    months_back = max(1, int(months_back))
    reduction_pct = _clamp(float(reduction_pct), 0.0, 100.0)

    records = _transaction_records(rows)
    months = _month_sequence(now, months_back)
    month_keys = _months_as_keys(months)

    month_start_map = {month_key: month_start for month_key, month_start in zip(month_keys, months)}
    month_end_map = {
        month_key: _shift_months(month_start, 1)
        for month_key, month_start in month_start_map.items()
    }

    investment_records = [record for record in records if _is_investment(record[2], record[0], record[1])]
    spend_records = [record for record in records if _is_spend(record[2], record[0], record[1])]
    income_records = [record for record in records if _is_income(record[2], record[0], record[1])]

    investment_month_totals, _ = _aggregate_records(
        investment_records,
        key_fn=lambda _row, timestamp, _amount: timestamp.strftime("%Y-%m"),
    )

    cumulative_investment = 0.0
    investment_points: list[dict[str, Any]] = []
    for month_key in month_keys:
        cumulative_investment += investment_month_totals.get(month_key, 0.0)
        investment_points.append({"x": month_key, "y": _round_money(cumulative_investment) or 0.0})

    investment_chart = _chart_payload(
        "savings_investment_growth",
        "line",
        "Crecimiento acumulado de inversión",
        "Suma de abonos de inversión registrados dentro del periodo seleccionado.",
        unit="MXN",
        value_format={"kind": "currency", "currency": "MXN", "decimals": 2},
        available=True,
        series=[{"name": "Inversión acumulada", "data": investment_points}],
        summary={
            "final_cumulative_mxn": investment_points[-1]["y"] if investment_points else 0.0,
            "investment_tx_count": len(investment_records),
        },
        meta={"period": {"months_back": months_back, "months": month_keys}},
        notes=["Este chart mide aportes acumulados, no valuación de portafolio."]
        if investment_records
        else ["No se encontraron abonos de inversión; la línea queda en cero."],
    )

    spend_month_totals, _ = _aggregate_records(
        spend_records,
        key_fn=lambda _row, timestamp, _amount: timestamp.strftime("%Y-%m"),
    )
    income_month_totals, _ = _aggregate_records(
        income_records,
        key_fn=lambda _row, timestamp, _amount: timestamp.strftime("%Y-%m"),
    )

    if profile_income is not None and profile_income > 0:
        income_series_name = "Ingreso estimado"
        income_values = {month_key: profile_income for month_key in month_keys}
        income_source = "user_profiles.ingreso_mensual_mxn"
    else:
        income_series_name = "Ingreso transaccional"
        income_values = {month_key: income_month_totals.get(month_key, 0.0) for month_key in month_keys}
        income_source = "user_transactions.transf_entrada"

    spend_points = [{"x": month_key, "y": _round_money(spend_month_totals.get(month_key, 0.0)) or 0.0} for month_key in month_keys]
    income_points = [{"x": month_key, "y": _round_money(income_values.get(month_key, 0.0)) or 0.0} for month_key in month_keys]
    net_points = [
        {"x": month_key, "y": _round_money(income_values.get(month_key, 0.0) - spend_month_totals.get(month_key, 0.0)) or 0.0}
        for month_key in month_keys
    ]

    income_vs_spend_chart = _chart_payload(
        "savings_income_vs_spend",
        "bar",
        "Ingreso vs gasto mensual",
        "Comparativa mensual con ingreso estimado y gasto completado.",
        unit="MXN",
        value_format={"kind": "currency", "currency": "MXN", "decimals": 2},
        available=True,
        categories=month_keys,
        series=[
            {"name": income_series_name, "data": income_points},
            {"name": "Gasto", "data": spend_points},
            {"name": "Sobra / falta", "data": net_points},
        ],
        summary={
            "income_source": income_source,
            "latest_month_income_mxn": income_points[-1]["y"] if income_points else 0.0,
            "latest_month_spend_mxn": spend_points[-1]["y"] if spend_points else 0.0,
            "latest_month_net_mxn": net_points[-1]["y"] if net_points else 0.0,
        },
        meta={"period": {"months_back": months_back, "months": month_keys}},
    )

    current_month_key = month_keys[-1]
    current_month_start = month_start_map[current_month_key]
    current_month_end = month_end_map[current_month_key]
    current_month_category_totals, _ = _aggregate_records(
        spend_records,
        key_fn=lambda row, _timestamp, _amount: _transaction_category(row),
        start=current_month_start,
        end=current_month_end,
    )
    current_categories = sorted(
        current_month_category_totals,
        key=lambda label: (current_month_category_totals.get(label, 0.0), label),
        reverse=True,
    )

    selected_category = target_category
    if selected_category is None and current_categories:
        selected_category = current_categories[0]

    category_lookup = {category.lower(): category for category in current_categories}
    resolved_category = category_lookup.get(_normalized_key(selected_category)) if selected_category else None

    if resolved_category is None and selected_category is not None:
        projection_chart = _chart_payload(
            "savings_projection",
            "bar",
            "Proyección de ahorro por categoría",
            "Escenario no disponible porque la categoría solicitada no aparece en el periodo actual.",
            unit="MXN",
            value_format={"kind": "currency", "currency": "MXN", "decimals": 2},
            available=False,
            reason="No hay gasto actual en la categoría solicitada.",
            orientation="horizontal",
            series=[],
            summary={"category": selected_category, "reduction_pct": reduction_pct},
            notes=["Prueba con otra categoría o deja que el tool use la categoría con mayor gasto del mes."],
        )
        projected_savings = 0.0
        projection_category = selected_category
        current_category_spend = 0.0
    else:
        projection_category = resolved_category or selected_category
        current_category_spend = current_month_category_totals.get(projection_category or "", 0.0)
        projected_savings = current_category_spend * (reduction_pct / 100.0)
        projected_spend = current_category_spend - projected_savings
        projection_chart = _chart_payload(
            "savings_projection",
            "bar",
            "Proyección de ahorro por categoría",
            f"Escenario: reducir {reduction_pct:.0f}% en {projection_category}.",
            unit="MXN",
            value_format={"kind": "currency", "currency": "MXN", "decimals": 2},
            available=True,
            orientation="horizontal",
            categories=[projection_category] if projection_category else [],
            series=[
                {
                    "name": "Gasto actual",
                    "data": [{"label": projection_category, "value": _round_money(current_category_spend) or 0.0}],
                },
                {
                    "name": "Gasto proyectado",
                    "data": [{"label": projection_category, "value": _round_money(projected_spend) or 0.0}],
                },
                {
                    "name": "Ahorro potencial",
                    "data": [{"label": projection_category, "value": _round_money(projected_savings) or 0.0}],
                },
            ],
            summary={
                "category": projection_category,
                "current_month_spend_mxn": _round_money(current_category_spend) or 0.0,
                "reduction_pct": reduction_pct,
                "projected_savings_mxn": _round_money(projected_savings) or 0.0,
                "projected_spend_mxn": _round_money(current_category_spend - projected_savings) or 0.0,
                "projected_yearly_savings_mxn": _round_money(projected_savings * 12.0) or 0.0,
            },
            meta={"period": {"months_back": months_back, "current_month": current_month_key}},
        )

    summary = {
        "investment_final_cumulative_mxn": investment_points[-1]["y"] if investment_points else 0.0,
        "latest_month_income_mxn": income_points[-1]["y"] if income_points else 0.0,
        "latest_month_spend_mxn": spend_points[-1]["y"] if spend_points else 0.0,
        "latest_month_net_mxn": net_points[-1]["y"] if net_points else 0.0,
        "projection_category": projection_category,
        "projected_savings_mxn": _round_money(projected_savings) or 0.0,
    }

    return _dashboard_payload(
        "savings_dashboard",
        user_id,
        charts=[investment_chart, income_vs_spend_chart, projection_chart],
        summary=summary,
        meta={"source_tables": ["user_profiles", "user_transactions"], "months_back": months_back},
    )


def _build_behavior_dashboard(
    client: SupabaseRestClient,
    *,
    user_id: str,
    weeks_back: int = 12,
) -> dict[str, Any]:
    transactions_result, rows = _select_rows(
        client,
        "user_transactions",
        filters={"user_id": user_id},
        limit=1000,
        order_by="fecha_hora",
        ascending=False,
    )
    if not transactions_result.get("ok", False):
        return _empty_dashboard(
            "behavior_dashboard",
            user_id,
            str(transactions_result.get("error") or "Failed to load transactions."),
        )

    weeks_back = max(1, int(weeks_back))
    now = _now_utc()
    records = _transaction_records(rows)

    behavior_start = now - timedelta(days=89)
    spend_records = [record for record in records if _is_spend(record[2], record[0], record[1])]

    weekday_spend = sum(amount for timestamp, amount, _row in spend_records if timestamp >= behavior_start and timestamp.weekday() < 5)
    weekend_spend = sum(amount for timestamp, amount, _row in spend_records if timestamp >= behavior_start and timestamp.weekday() >= 5)
    weekend_chart = _chart_payload(
        "behavior_weekday_vs_weekend_spend",
        "bar",
        "Gasto en días de semana vs fin de semana",
        "Monto completado de los últimos 90 días.",
        unit="MXN",
        value_format={"kind": "currency", "currency": "MXN", "decimals": 2},
        available=True,
        orientation="vertical",
        series=[
            {
                "name": "Gasto",
                "data": [
                    {"label": "Días hábiles", "value": _round_money(weekday_spend) or 0.0},
                    {"label": "Fin de semana", "value": _round_money(weekend_spend) or 0.0},
                ],
            }
        ],
        summary={
            "weekday_spend_mxn": _round_money(weekday_spend) or 0.0,
            "weekend_spend_mxn": _round_money(weekend_spend) or 0.0,
            "weekday_share_pct": (
                round((weekday_spend / (weekday_spend + weekend_spend)) * 100.0, 2)
                if (weekday_spend + weekend_spend)
                else None
            ),
        },
        meta={"window_days": 90},
    )

    week_sequence = _week_sequence(now, weeks_back)
    week_keys = [week_start.isoformat() for week_start in week_sequence]
    weekly_totals, _ = _aggregate_records(
        records,
        key_fn=lambda _row, timestamp, _amount: (timestamp.date() - timedelta(days=timestamp.weekday())).isoformat(),
        start=datetime.combine(week_sequence[0], datetime.min.time(), tzinfo=timezone.utc),
        end=datetime.combine(week_sequence[-1] + timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc),
        metric="count",
    )
    weekly_points = [{"x": week_key, "y": int(weekly_totals.get(week_key, 0.0))} for week_key in week_keys]
    weekly_chart = _chart_payload(
        "behavior_weekly_transaction_frequency",
        "line",
        "Frecuencia de transacciones por semana",
        "Cuenta todas las transacciones por semana en el periodo seleccionado.",
        unit="count",
        value_format={"kind": "count", "decimals": 0},
        available=True,
        series=[{"name": "Transacciones", "data": weekly_points}],
        summary={
            "total_transactions": int(sum(point["y"] for point in weekly_points)),
            "average_per_week": _round_money(sum(point["y"] for point in weekly_points) / len(weekly_points))
            if weekly_points
            else 0.0,
            "weeks": len(weekly_points),
        },
        meta={"period": {"weeks_back": weeks_back, "weeks": week_keys}},
    )

    activity_points, activity_summary = _aggregate_heatmap(
        records,
        start=now - timedelta(days=29),
        end=now + timedelta(seconds=1),
        metric="count",
    )
    activity_chart = _chart_payload(
        "behavior_activity_heatmap",
        "heatmap",
        "Mapa de calor de actividad por hora del día",
        "La tabla no expone hora_del_dia; se deriva desde fecha_hora.",
        unit="count",
        value_format={"kind": "count", "decimals": 0},
        available=True,
        points=activity_points,
        summary=activity_summary,
        meta={
            "derived_fields": ["dia_semana", "hora_del_dia"],
            "source_field": "fecha_hora",
            "window_days": 30,
        },
    )

    summary = {
        "weekday_spend_mxn": _round_money(weekday_spend) or 0.0,
        "weekend_spend_mxn": _round_money(weekend_spend) or 0.0,
        "total_transactions": len(records),
        "weeks_back": weeks_back,
        "peak_hour": activity_summary.get("peak_hour"),
        "peak_day": activity_summary.get("peak_day"),
    }

    return _dashboard_payload(
        "behavior_dashboard",
        user_id,
        charts=[weekend_chart, weekly_chart, activity_chart],
        summary=summary,
        meta={"source_table": "user_transactions", "window_days": 90},
    )


def _build_benchmark_dashboard(
    client: SupabaseRestClient,
    *,
    user_id: str,
) -> dict[str, Any]:
    profile_result, profile_rows = _select_rows(
        client,
        "user_profiles",
        filters={"user_id": user_id},
        limit=1,
        order_by="updated_at",
        ascending=False,
    )
    segment_result, segment_rows = _select_rows(
        client,
        "user_segments",
        filters={"user_id": user_id},
        limit=1,
        order_by="updated_at",
        ascending=False,
    )
    profiles_result, profiles_rows = _select_rows(
        client,
        "user_profiles",
        limit=1000,
        order_by="updated_at",
        ascending=False,
    )
    segments_result, segments_rows = _select_rows(
        client,
        "user_segments",
        limit=1000,
        order_by="updated_at",
        ascending=False,
    )
    transactions_result, transactions_rows = _select_rows(
        client,
        "user_transactions",
        limit=1000,
        order_by="fecha_hora",
        ascending=False,
    )

    errors: list[str] = []
    for result, label in [
        (profile_result, "user profile"),
        (segment_result, "user segment"),
        (profiles_result, "profile catalog"),
        (segments_result, "segment catalog"),
        (transactions_result, "transactions"),
    ]:
        if not result.get("ok", False):
            errors.append(str(result.get("error") or f"Failed to load {label}."))
    if errors:
        return _dashboard_payload("benchmark_dashboard", user_id, charts=[], errors=errors)

    profile = profile_rows[0] if profile_rows else {}
    segment = segment_rows[0] if segment_rows else {}

    profile_map = {row.get("user_id"): row for row in profiles_rows if row.get("user_id")}
    transactions_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in transactions_rows:
        row_user_id = row.get("user_id")
        if row_user_id:
            transactions_by_user[str(row_user_id)].append(row)

    segment_label = _display_label(segment.get("segmento"), "")
    cluster_id = segment.get("cluster_id")
    peer_user_ids = [
        user_row.get("user_id")
        for user_row in segments_rows
        if user_row.get("user_id")
        and (
            (
                segment_label
                and _normalized_key(user_row.get("segmento")) == _normalized_key(segment_label)
            )
            or (
                cluster_id is not None and user_row.get("cluster_id") == cluster_id
            )
        )
    ]
    peer_user_ids = [peer_user_id for peer_user_id in peer_user_ids if peer_user_id]
    peer_user_ids = [peer_user_id for peer_user_id in peer_user_ids if peer_user_id != user_id]
    if not peer_user_ids:
        peer_user_ids = [user_id]

    now = _now_utc()
    current_month_start = _month_start(now)
    next_month_start = _shift_months(current_month_start, 1)
    spend_window_start = now - timedelta(days=89)
    activity_window_start = now - timedelta(days=29)

    def _current_month_category_totals_for(user_transactions: list[dict[str, Any]]) -> dict[str, float]:
        user_records = _transaction_records(user_transactions)
        spend_records = [record for record in user_records if _is_spend(record[2], record[0], record[1])]
        totals, _ = _aggregate_records(
            spend_records,
            key_fn=lambda row, _timestamp, _amount: _transaction_category(row),
            start=current_month_start,
            end=next_month_start,
        )
        return totals

    def _user_scores(user_profile: dict[str, Any], user_transactions: list[dict[str, Any]]) -> dict[str, float]:
        records = _transaction_records(user_transactions)
        spend_records = [record for record in records if _is_spend(record[2], record[0], record[1])]
        current_month_spend = sum(
            amount for timestamp, amount, _row in spend_records if current_month_start <= timestamp < next_month_start
        )
        income_value = _safe_float(user_profile.get("ingreso_mensual_mxn"))
        if income_value is None or income_value <= 0:
            actual_income = sum(
                amount
                for timestamp, amount, row in records
                if current_month_start <= timestamp < next_month_start and _is_income(row, timestamp, amount)
            )
            income_value = actual_income

        savings_score = (
            _clamp(((income_value - current_month_spend) / income_value) * 100.0)
            if income_value and income_value > 0
            else 0.0
        )
        spend_score = (
            _clamp(100.0 - min(100.0, (current_month_spend / income_value) * 100.0))
            if income_value and income_value > 0
            else 0.0
        )
        credit_score = _score_buro_to_pct(user_profile.get("score_buro")) or 0.0
        activity_score = _clamp(len([record for record in records if record[0] >= activity_window_start]) * 5.0)
        unique_categories = {
            _transaction_category(row)
            for timestamp, amount, row in spend_records
            if timestamp >= spend_window_start
        }
        diversification_score = _clamp(len(unique_categories) * 12.5)
        punctuality_score = (
            _clamp(
                (sum(1 for record in records if _is_completed(record[2], record[0], record[1])) / len(records)) * 100.0
            )
            if records
            else 0.0
        )

        return {
            "ahorro": _round_pct(savings_score) or 0.0,
            "gasto": _round_pct(spend_score) or 0.0,
            "credito": _round_pct(credit_score) or 0.0,
            "actividad": _round_pct(activity_score) or 0.0,
            "diversificacion": _round_pct(diversification_score) or 0.0,
            "puntualidad": _round_pct(punctuality_score) or 0.0,
        }

    user_scores = _user_scores(profile, transactions_by_user.get(user_id, []))
    peer_scores: list[dict[str, float]] = []
    peer_category_totals: dict[str, list[float]] = defaultdict(list)

    for peer_user_id in peer_user_ids:
        peer_profile = profile_map.get(peer_user_id, {})
        peer_transactions = transactions_by_user.get(peer_user_id, [])
        peer_scores.append(_user_scores(peer_profile, peer_transactions))

        category_totals = _current_month_category_totals_for(peer_transactions)
        for category in set(category_totals) | set(peer_category_totals):
            peer_category_totals[category]

        for category, amount in category_totals.items():
            peer_category_totals[category].append(amount)

    if not peer_scores:
        peer_scores = [user_scores]

    current_month_category_totals = _current_month_category_totals_for(transactions_by_user.get(user_id, []))
    category_labels = sorted(
        set(current_month_category_totals) | set(peer_category_totals),
        key=lambda label: (
            current_month_category_totals.get(label, 0.0),
            fmean(peer_category_totals.get(label, [0.0])) if peer_category_totals.get(label) else 0.0,
            label,
        ),
        reverse=True,
    )

    peer_averages: dict[str, float] = {}
    for category in category_labels:
        peer_values = peer_category_totals.get(category, [])
        peer_averages[category] = fmean(peer_values) if peer_values else 0.0

    category_chart = _chart_payload(
        "benchmark_category_comparison",
        "bar",
        "Tus categorías vs promedio de tu segmento",
        "Comparación horizontal del gasto completado del mes actual.",
        unit="MXN",
        value_format={"kind": "currency", "currency": "MXN", "decimals": 2},
        available=bool(category_labels),
        orientation="horizontal",
        categories=category_labels,
        series=[
            _series_from_totals(category_labels, current_month_category_totals, "Tú"),
            _series_from_totals(category_labels, peer_averages, "Promedio segmento"),
        ],
        summary={
            "segment_label": segment_label,
            "peer_count": len(peer_user_ids),
            "top_category": category_labels[0] if category_labels else None,
        },
        meta={"period": {"current_month_start": current_month_start.isoformat(), "end": next_month_start.isoformat()}},
    )

    dimension_definitions = [
        {
            "key": "ahorro",
            "label": "Ahorro",
            "meaning": "Porcentaje de ingreso que queda después del gasto del mes actual.",
        },
        {
            "key": "gasto",
            "label": "Gasto",
            "meaning": "Puntaje invertido: más alto significa menor presión de gasto.",
        },
        {
            "key": "credito",
            "label": "Crédito",
            "meaning": "Score buró normalizado a una escala de 0 a 100.",
        },
        {
            "key": "actividad",
            "label": "Actividad",
            "meaning": "Frecuencia reciente de transacciones, normalizada a 0 a 100.",
        },
        {
            "key": "diversificacion",
            "label": "Diversificación",
            "meaning": "Cantidad de categorías de gasto recientes, normalizada a 0 a 100.",
        },
        {
            "key": "puntualidad",
            "label": "Puntualidad",
            "meaning": "Proporción de transacciones completadas sobre el total.",
        },
    ]

    segment_dimension_scores: dict[str, list[float]] = defaultdict(list)
    for score_row in peer_scores:
        for key, value in score_row.items():
            segment_dimension_scores[key].append(value)

    segment_scores = {key: _round_pct(fmean(values)) or 0.0 for key, values in segment_dimension_scores.items()}
    radar_chart = _chart_payload(
        "benchmark_radar",
        "radar",
        "Radar de salud financiera",
        "Las seis dimensiones se normalizan a 0 a 100 para compararlas en el mismo plano.",
        unit="%",
        value_format={"kind": "percent", "decimals": 2},
        available=True,
        dimensions=dimension_definitions,
        series=[
            {
                "name": "Tú",
                "data": [{"label": dimension["label"], "value": user_scores[dimension["key"]]} for dimension in dimension_definitions],
            },
            {
                "name": "Promedio segmento",
                "data": [
                    {"label": dimension["label"], "value": segment_scores.get(dimension["key"], 0.0)}
                    for dimension in dimension_definitions
                ],
            },
        ],
        summary={
            "segment_label": segment_label,
            "peer_count": len(peer_user_ids),
            "user_scores": user_scores,
            "segment_scores": segment_scores,
        },
        meta={
            "period": {
                "current_month_start": current_month_start.isoformat(),
                "spend_window_days": 90,
                "activity_window_days": 30,
            },
        },
    )

    summary = {
        "segment_label": segment_label,
        "peer_count": len(peer_user_ids),
        "top_category": category_labels[0] if category_labels else None,
        "user_score_buro": profile.get("score_buro"),
        "user_income_mxn": profile.get("ingreso_mensual_mxn"),
    }

    return _dashboard_payload(
        "benchmark_dashboard",
        user_id,
        charts=[category_chart, radar_chart],
        summary=summary,
        meta={"source_tables": ["user_profiles", "user_segments", "user_transactions"]},
    )


def build_spending_dashboard(
    client: SupabaseRestClient,
    *,
    user_id: str,
    top_merchants_limit: int = 5,
) -> dict[str, Any]:
    return _build_spending_dashboard(client, user_id=user_id, top_merchants_limit=top_merchants_limit)


def build_credit_dashboard(
    client: SupabaseRestClient,
    *,
    user_id: str,
) -> dict[str, Any]:
    return _build_credit_dashboard(client, user_id=user_id)


def build_savings_dashboard(
    client: SupabaseRestClient,
    *,
    user_id: str,
    months_back: int = 6,
    target_category: str | None = None,
    reduction_pct: float = 10.0,
) -> dict[str, Any]:
    return _build_savings_dashboard(
        client,
        user_id=user_id,
        months_back=months_back,
        target_category=target_category,
        reduction_pct=reduction_pct,
    )


def build_behavior_dashboard(
    client: SupabaseRestClient,
    *,
    user_id: str,
    weeks_back: int = 12,
) -> dict[str, Any]:
    return _build_behavior_dashboard(client, user_id=user_id, weeks_back=weeks_back)


def build_benchmark_dashboard(
    client: SupabaseRestClient,
    *,
    user_id: str,
) -> dict[str, Any]:
    return _build_benchmark_dashboard(client, user_id=user_id)