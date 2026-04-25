# Hey-i_MCP

This repo is the MCP side of the system.

Architecture:

- `app` -> Lambda + LangChain
- `api gateway` -> MCP service
- `mcp` -> tools for `db`

Base FastMCP scaffold for one tool:

- `run_query(query)` for raw SQL execution against a configured database URL.

## Environment variables

- `SUPABASE_DATABASE_URL`
- `DATABASE_URL` as fallback

For this repo, the query tool needs a Postgres connection string. If you are using Supabase, that connection string should come from the Supabase database settings. The `NEXT_PUBLIC_SUPABASE_*` values currently in your `.env` belong to a different app flow and are not enough by themselves for raw SQL.

Example local `.env`:

```env
SUPABASE_DATABASE_URL=postgresql+psycopg://postgres:YOUR_SUPABASE_DB_PASSWORD@db.ghdriiamxjczjfzfrmlw.supabase.co:5432/postgres?sslmode=require
NEXT_PUBLIC_SUPABASE_URL=https://ghdriiamxjczjfzfrmlw.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
```

If you want to keep a generic fallback name instead, you can also use:

```env
DATABASE_URL=postgresql+psycopg://postgres:YOUR_SUPABASE_DB_PASSWORD@db.ghdriiamxjczjfzfrmlw.supabase.co:5432/postgres?sslmode=require
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

## Database query tool

`run_query(query)` sends the SQL string directly to the configured `DATABASE_URL` and returns a JSON-friendly result. This version is intentionally raw and leaves out safety controls for now, as requested.