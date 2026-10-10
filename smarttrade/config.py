"""Application settings.

Everything has a safe default. No secret or API key is required for the
application to start. Values can be overridden with environment variables or
an optional ``.env`` file in the project root.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no external dependency). Existing env wins."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str
    port: int
    log_level: str
    enable_network_providers: bool

    @property
    def db_path(self) -> Path:
        return self.data_dir / "smarttrade.db"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "smarttrade.log"

    @property
    def import_dir(self) -> Path:
        return self.data_dir / "imports"


def load_settings() -> Settings:
    _load_dotenv(PROJECT_ROOT / ".env")
    data_dir = Path(os.environ.get("SMARTTRADE_DATA_DIR", PROJECT_ROOT / "data")).resolve()
    return Settings(
        data_dir=data_dir,
        host=os.environ.get("SMARTTRADE_HOST", "127.0.0.1"),
        port=int(os.environ.get("SMARTTRADE_PORT", "8000")),
        log_level=os.environ.get("SMARTTRADE_LOG_LEVEL", "INFO").upper(),
        enable_network_providers=_as_bool(
            os.environ.get("SMARTTRADE_ENABLE_NETWORK_PROVIDERS"), True
        ),
    )


settings = load_settings()
