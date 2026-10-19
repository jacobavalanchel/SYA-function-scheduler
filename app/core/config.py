"""Cross-cutting runtime configuration (Polaris `core/` layer)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env", override=False)


class Settings(BaseSettings):
    """Desktop App settings with threshold-driven replanning policy."""

    model_config = SettingsConfigDict(
        env_file=str(_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_prefix="SYA_",
        extra="ignore",
        validate_default=True,
    )

    scheduler_data_dir: str = ""

    app_name: str = "FocusFlow"
    debug: bool = False

    completion_low_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    completion_high_threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    evaluation_leaf_batch_size: int = Field(default=3, ge=1)
    metrics_window_size: int = Field(default=10, ge=1)

    event_history_maxlen: int = Field(default=2000, ge=100)
    ws_ping_interval_seconds: int = Field(default=20, ge=5)

    openai_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    openai_api_key: str = ""
    openai_model: str = "glm-5.3"
    llm_timeout_seconds: int = Field(default=90, ge=5)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> "Settings":
        """Ensure low-threshold is strictly below high-threshold."""
        if self.completion_low_threshold >= self.completion_high_threshold:
            raise ValueError(
                "completion_low_threshold must be lower than completion_high_threshold"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings object for dependency injection."""
    return Settings()
