from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5")
    max_fetched_jobs: int = int(os.getenv("MAX_FETCHED_JOBS", "80"))
    request_timeout_seconds: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
    db_path: str = os.getenv("JOBS_DB_PATH", "jobs.db")
    # USD per 1M tokens for the chosen model — set these to your model's real rates.
    openai_input_cost_per_1m: float = _float_env("OPENAI_INPUT_COST_PER_1M", 0.0)
    openai_output_cost_per_1m: float = _float_env("OPENAI_OUTPUT_COST_PER_1M", 0.0)


settings = Settings()
