"""Loop policy value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 2
    backoff: tuple[float, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if any(delay < 0 for delay in self.backoff):
            raise ValueError("backoff delays must be >= 0")
        object.__setattr__(self, "backoff", tuple(self.backoff))

    def to_dict(self) -> dict[str, Any]:
        return {"max_attempts": self.max_attempts, "backoff": list(self.backoff)}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RetryPolicy":
        if data is None:
            return cls()
        return cls(max_attempts=int(data.get("max_attempts", 2)), backoff=tuple(data.get("backoff", ())))


@dataclass(frozen=True, slots=True)
class LoopPolicy:
    require_verifier: bool = True
    require_separate_verifier_worker: bool = True
    default_retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    auto_merge: bool = False
    max_concurrent_attempts: int = 1
    default_maker_model: str | None = None
    default_verifier_model: str | None = None

    def __post_init__(self) -> None:
        if self.auto_merge:
            raise ValueError("loop auto_merge is forbidden")
        if self.max_concurrent_attempts != 1:
            raise ValueError("max_concurrent_attempts must equal 1")
        if not isinstance(self.default_retry_policy, RetryPolicy):
            object.__setattr__(
                self,
                "default_retry_policy",
                RetryPolicy.from_dict(self.default_retry_policy),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "require_verifier": self.require_verifier,
            "require_separate_verifier_worker": self.require_separate_verifier_worker,
            "default_retry_policy": self.default_retry_policy.to_dict(),
            "auto_merge": self.auto_merge,
            "max_concurrent_attempts": self.max_concurrent_attempts,
            "default_maker_model": self.default_maker_model,
            "default_verifier_model": self.default_verifier_model,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LoopPolicy":
        if data is None:
            return cls()
        return cls(
            require_verifier=bool(data.get("require_verifier", True)),
            require_separate_verifier_worker=bool(data.get("require_separate_verifier_worker", True)),
            default_retry_policy=RetryPolicy.from_dict(data.get("default_retry_policy")),
            auto_merge=bool(data.get("auto_merge", False)),
            max_concurrent_attempts=int(data.get("max_concurrent_attempts", 1)),
            default_maker_model=data.get("default_maker_model"),
            default_verifier_model=data.get("default_verifier_model"),
        )
