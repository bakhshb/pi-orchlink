"""Non-secret connector configuration value objects."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

SECRET_KEY_NAMES = frozenset(
    {
        "token",
        "secret",
        "password",
        "passwd",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "client_secret",
        "private_key",
    }
)


@dataclass(frozen=True, slots=True)
class ConnectorConfig:
    name: str
    repo: str | None = None
    api_base: str | None = None
    limit: int = 10
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        offending = _secret_like_keys(self.extra.keys())
        if offending:
            _raise_secret_keys(offending)
        object.__setattr__(self, "extra", dict(self.extra))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConnectorConfig":
        if not data.get("name"):
            raise ValueError("connector config name is required")
        known = {"name", "repo", "api_base", "limit"}
        extra = dict(data.get("extra") or {})
        unknown_keys = [key for key in data if key not in known and key != "extra"]
        offending = _secret_like_keys([*unknown_keys, *extra.keys()])
        if offending:
            _raise_secret_keys(offending)
        for key, value in data.items():
            if key not in known and key != "extra":
                extra[key] = value
        return cls(
            name=str(data["name"]),
            repo=str(data["repo"]) if data.get("repo") is not None else None,
            api_base=str(data["api_base"]) if data.get("api_base") is not None else None,
            limit=int(data.get("limit", 10)),
            extra=extra,
        )


def from_dict(data: dict[str, Any]) -> ConnectorConfig:
    return ConnectorConfig.from_dict(data)


def _secret_like_keys(keys) -> list[str]:
    return sorted({str(key) for key in keys if str(key).lower() in SECRET_KEY_NAMES})


def _raise_secret_keys(keys: list[str]) -> None:
    log.warning("connector config contains secret-shaped key(s): %s", ", ".join(keys))
    raise ValueError(f"connector config must not contain secret-shaped keys: {', '.join(keys)}")


__all__ = ["ConnectorConfig", "SECRET_KEY_NAMES", "from_dict"]
