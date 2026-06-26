from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    host: str = Field(default="127.0.0.1", alias="ORCHLINK_HOST")
    port: int = Field(default=8787, alias="ORCHLINK_PORT")
    api_key: str = Field(default="change-me", alias="ORCHLINK_API_KEY")
    log_level: str = Field(default="INFO", alias="ORCHLINK_LOG_LEVEL")
    auto_stop: bool = Field(default=False, alias="ORCHLINK_AUTO_STOP")
    require_peer_sessions: bool = Field(default=False, alias="ORCHLINK_REQUIRE_PEER_SESSIONS")
    session_heartbeat_interval_seconds: int = Field(default=10, alias="ORCHLINK_SESSION_HEARTBEAT_INTERVAL_SECONDS")
    session_grace_seconds: int = Field(default=25, alias="ORCHLINK_SESSION_GRACE_SECONDS")
    store_backend: str = Field(default="memory", alias="ORCHLINK_STORE_BACKEND")
    store_path: str = Field(default=".orch/run/orchlink-journal.jsonl", alias="ORCHLINK_STORE_PATH")

    model_config = SettingsConfigDict(
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
