from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str | None


settings = Settings(
    database_url=os.getenv("SUPABASE_DATABASE_URL") or os.getenv("DATABASE_URL") or None,
)