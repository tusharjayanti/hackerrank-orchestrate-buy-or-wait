"""Runtime settings, read from environment variables and the repo-root .env file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: SecretStr | None = None
    model: str = "claude-sonnet-5"
    llm_max_retries: int = 3
    llm_timeout_seconds: float = 180.0

    agent_effort: str = "medium"
    message_effort: str = "low"
    image_effort: str = "medium"
    max_agent_turns: int = 8
    # "ambiguous": call the agent only where the engine simulated alternative readings; "all": every request.
    agent_scope: Literal["ambiguous", "all"] = "ambiguous"
    concurrency: int = 6
    # Read messages and images through the Message Batches API (50% price, asynchronous, synchronous fallback).
    evidence_batch: bool = False
    batch_poll_seconds: float = 15.0
    batch_timeout_seconds: float = 1500.0

    forecast_horizon_days: int = 90

    dataset_dir: Path = REPO_ROOT / "dataset"
    runs_dir: Path = REPO_ROOT / "runs"
    cache_dir: Path = REPO_ROOT / "cache"
    output_path: Path = REPO_ROOT / "output.csv"
