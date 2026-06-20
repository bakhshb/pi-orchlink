from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    host: str = Field(default="127.0.0.1", alias="ORCHLINK_HOST")
    port: int = Field(default=8787, alias="ORCHLINK_PORT")
    api_key: str = Field(default="change-me", alias="ORCHLINK_API_KEY")
    storage: str = Field(default="memory", alias="ORCHLINK_STORAGE")
    log_level: str = Field(default="INFO", alias="ORCHLINK_LOG_LEVEL")

    model_config = SettingsConfigDict(
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
