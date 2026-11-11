from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
DATA_DIR = BASE_DIR / "data"

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    app_name: str = "EAA Evidence"
    base_url: str = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000")
    database_path: Path = Path(
        os.getenv("DATABASE_PATH", str(DATA_DIR / "eaa_evidence.db"))
    )
    axe_path: Path = Path(
        os.getenv("AXE_PATH", str(APP_DIR / "static" / "vendor" / "axe.min.js"))
    )
    max_pages: int = int(os.getenv("MAX_PAGES", "3"))
    scan_timeout_ms: int = int(os.getenv("SCAN_TIMEOUT_MS", "45000"))
    allow_private_urls: bool = _env_bool("ALLOW_PRIVATE_URLS", False)
    max_nodes_per_issue: int = int(os.getenv("MAX_NODES_PER_ISSUE", "10"))
    api_key: str = os.getenv("API_KEY", "")
    scan_rate_limit_per_hour: int = int(
        os.getenv("SCAN_RATE_LIMIT_PER_HOUR", "60")
    )
    browser_channel: str = os.getenv("BROWSER_CHANNEL", "")


settings = Settings()
