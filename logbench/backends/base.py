from abc import ABC, abstractmethod
from typing import Any

import polars as pl


class Backend(ABC):
    """Base class encapsulating all backend-specific knowledge."""

    name: str       # e.g. "qdrant"
    env_prefix: str  # e.g. "QDRANT"

    def __init__(self, env: dict[str, str]):
        self._env = env

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "Backend":
        return cls(env)

    def is_configured(self) -> bool:
        """Return True if the primary env var for this backend is set."""
        key = self._primary_env_key()
        return bool(self._env.get(key))

    def _primary_env_key(self) -> str:
        """The env var that indicates this backend is configured."""
        if self.env_prefix == "PGVECTOR":
            return f"{self.env_prefix}_HOST"
        return f"{self.env_prefix}_URL"

    @property
    def instance_id(self) -> str | None:
        """CloudWatch EC2 instance ID, or None."""
        return self._env.get(f"{self.env_prefix}_INSTANCE_ID") or None

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the backend is reachable."""
        ...

    @abstractmethod
    async def seed(self, df: pl.DataFrame, index_mode: str, batch_size: int = 1000) -> None:
        """Bulk-load data into the backend."""
        ...

    @abstractmethod
    def qstorm_provider_config(self) -> dict[str, Any]:
        """Return the provider block for a qstorm YAML config."""
        ...

    @abstractmethod
    def logstorm_sink_config(self, index_mode: str) -> dict[str, Any]:
        """Return a single sink entry for logstorm config."""
        ...
