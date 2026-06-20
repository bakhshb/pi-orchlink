from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_env_file() -> str:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return str(parent / ".env")
    return ".env"


class Settings(BaseSettings):
    host: str = Field(default="127.0.0.1", alias="ORCHLINK_HOST")
    port: int = Field(default=8787, alias="ORCHLINK_PORT")
    api_key: str = Field(default="change-me", alias="ORCHLINK_API_KEY")
    storage: str = Field(default="memory", alias="ORCHLINK_STORAGE")
    log_level: str = Field(default="INFO", alias="ORCHLINK_LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=_default_env_file(),
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
