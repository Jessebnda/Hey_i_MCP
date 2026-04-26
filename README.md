# Hey-i_MCP

This repo is the MCP side of the system.

Architecture:

- `app` -> Lambda + LangChain
- `api gateway` -> MCP service
- `mcp` -> tools for `db`

Base FastMCP scaffold for user-scoped tools:

- `supabase_select_rows(table_name, schema, filters, limit)` for quick REST checks using Supabase keys.
- `get_user_profile(user_id)` for the latest row in `user_profiles`.
- `get_user_segment(user_id)` for the latest row in `user_segments`.
- `get_user_transactions(user_id, ...)` for user-scoped transaction history plus aggregates.
- `get_user_context_snapshot(user_id, ...)` for a compact cross-table user summary.
- `get_user_chat_messages(user_id, ...)` for user chat history.
- `save_user_insight(user_id, ...)` to persist a generated insight into `user_insights`.
- `call_model_endpoint(model, function, method, payload)` for the Datathon206 FastAPI Space router.

## Environment variables

- `SUPABASE_URL` or `NEXT_PUBLIC_SUPABASE_URL`
- `SUPABASE_ANON_KEY` or `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY` if you want a backend-only key with full access

The MCP layer now uses Supabase REST only. You do not need a Postgres connection string for the exposed tools.

Example local `.env`:

```env
NEXT_PUBLIC_SUPABASE_URL=https://ghdriiamxjczjfzfrmlw.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
```

## Local setup

1. Create your local `.env` file from `.env.example`.
2. Install dependencies with `pip install -e .`.
3. Start the server with `python -m hey_i_mcp`.

## Deploy en Prefect Horizon

- Entry point: `server.py:mcp`
- Si la UI te pide solo archivo, usa `server.py`
- `pyproject.toml` ya basta como archivo de dependencias; no necesitas `requirements.txt` para este repo

## Repo Scope

This repository currently owns only the MCP service. The Lambda app that uses LangChain should live separately and call this MCP layer through API Gateway.

## Supabase key-based test tool

`supabase_select_rows(table_name, schema, filters, limit)` calls the Supabase REST API using the configured key instead of the PostgreSQL driver. It is a generic read helper for quick checks against `user_profiles`, `chat_messages`, or any other table you explicitly target. If you set `SUPABASE_SERVICE_ROLE_KEY`, it uses that first; otherwise it falls back to `SUPABASE_ANON_KEY` and the `NEXT_PUBLIC_SUPABASE_ANON_KEY` value from your existing `.env`.

The user-scoped tools focus on tables that already exist in this project:

- `user_profiles` for demographic and behavioral profile data.
- `user_segments` for the latest segment assignment and z-scores.
- `user_transactions` for spending and activity summaries.
- `chat_messages` for recent conversation history.
- `user_insights` for saved model-generated insights.

Most of these tools are read-only and filter by `user_id`; `save_user_insight` is the write path for persisting generated insights, which is still safer than exposing raw SQL access.

## Datathon206 segmentacion router

`call_model_endpoint(model, function, method, payload)` forwards requests to `https://orbit05-datathon206.hf.space/{model}/{function}`.

Supported model:

- `segmentacion`

Supported functions:

- `health` with `GET`
- `segments` with `GET`
- `insight/new` with `POST`

`insight/new` accepts this JSON payload shape:

```json
{
	"edad": 32,
	"ingreso_mensual_mxn": 28000,
	"score_buro": 720,
	"antiguedad_dias": 540,
	"dias_desde_ultimo_login": 4,
	"satisfaccion_1_10": 8,
	"es_hey_pro": 1,
	"nomina_domiciliada": 1,
	"recibe_remesas": 0,
	"usa_hey_shop": 1,
	"tiene_seguro": 0,
	"patron_uso_atipico": 0,
	"n_productos_total": 5,
	"max_utilizacion_credito": 0.38,
	"total_spend_mxn": 125000,
	"fail_rate": 0.02,
	"n_msi_txns": 3,
	"cashback_total_mxn": 850,
	"intl_ratio": 0.05,
	"has_credito": 1,
	"has_inversion": 1,
	"conversation_text": "opcional"
}
```

For `health` and `segments`, omit `payload` and let the tool infer `GET`.
