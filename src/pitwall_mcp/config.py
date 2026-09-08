"""Configuration, read from the environment and optionally from a .env file.

Nothing here has a credential-shaped default. If a value is missing, the tools
say so in Spanish and point at the script that fixes it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Final

from .storage.quota import BMW_DAILY_LIMIT

DEFAULT_DAILY_QUOTA: Final = 20

#: Cache lifetimes per endpoint. With a 12 h TTL the maintenance container
#: costs 2 requests a day, well inside the local cap.
TTLS: Final[dict[str, timedelta]] = {
    "mappings": timedelta(days=30),
    "basicData": timedelta(days=30),
    "telematicData": timedelta(hours=12),
    "tyreDiagnosis": timedelta(days=7),
    "containers": timedelta(days=30),
}

#: The refresh token lives 14 days. Below this margin we warn loudly, because
#: letting it expire means redoing the manual browser device flow.
REFRESH_TOKEN_WARNING = timedelta(days=3)
REFRESH_TOKEN_LIFETIME = timedelta(days=14)


def _default_token_file() -> Path:
    """Token file location, outside the repository."""
    return Path.home() / ".config" / "pitwall-mcp" / "tokens.json"


def _default_db_path() -> Path:
    """SQLite location, outside the repository."""
    return Path.home() / ".local" / "share" / "pitwall-mcp" / "pitwall.db"


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict without adding a dependency.

    Only KEY=value lines are honoured; blanks and comments are skipped.
    Values are never logged.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    """Resolved runtime configuration."""

    client_id: str | None
    vin: str | None
    container_id: str | None
    token_file: Path
    db_path: Path
    daily_quota: int
    log_level: str
    catalogue_path: Path

    @property
    def has_credentials(self) -> bool:
        """True when a client id is configured and a token file exists."""
        return bool(self.client_id) and self.token_file.is_file()

    def missing_for_api(self) -> list[str]:
        """Names of the settings that still block any real API call."""
        missing: list[str] = []
        if not self.client_id:
            missing.append("PITWALL_CLIENT_ID")
        if not self.token_file.is_file():
            missing.append(f"fichero de tokens ({self.token_file})")
        return missing


def _default_catalogue_path() -> Path:
    """Where a downloaded catalogue lives, next to the database."""
    return Path.home() / ".local" / "share" / "pitwall-mcp" / "telematic_catalogue.json"


def _catalogue_path() -> Path:
    """Locate the telematic catalogue.

    The catalogue is NOT redistributed inside this repository, so it has to be
    fetched once with `scripts/refresh_catalogue.py`. Order of preference: a
    copy packaged into the wheel, the downloaded copy next to the database, and
    finally a checkout's own `spec/` directory. The last candidate is returned
    even when it does not exist, so the error names a concrete path.
    """
    candidates = (
        Path(__file__).parent / "spec" / "telematic_catalogue.json",
        _default_catalogue_path(),
        Path(__file__).resolve().parents[2] / "spec" / "telematic_catalogue.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1]


def find_dotenv(env: dict[str, str] | None = None) -> Path | None:
    """Locate the .env file, or `None` when there is none.

    An MCP client starts this server with whatever working directory it likes,
    so looking only in the cwd means a correctly filled .env is silently
    ignored and the user is told their credentials are missing while staring
    at them. Order: an explicit PITWALL_ENV_FILE, then the cwd, then the
    repository root next to the package.
    """
    source = env if env is not None else dict(os.environ)
    explicit = source.get("PITWALL_ENV_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()

    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    return next((path for path in candidates if path.is_file()), None)


def load_settings(env: dict[str, str] | None = None, *, dotenv: Path | None = None) -> Settings:
    """Build Settings from the process environment, overlaid on a .env file."""
    env_file = dotenv if dotenv is not None else find_dotenv(env)
    file_values = load_dotenv(env_file) if env_file is not None else {}
    source = {**file_values, **(env if env is not None else dict(os.environ))}

    def get(key: str) -> str | None:
        value = source.get(key, "").strip()
        return value or None

    raw_quota = get("PITWALL_DAILY_QUOTA")
    try:
        daily_quota = int(raw_quota) if raw_quota else DEFAULT_DAILY_QUOTA
    except ValueError:
        daily_quota = DEFAULT_DAILY_QUOTA
    daily_quota = max(0, min(daily_quota, BMW_DAILY_LIMIT))

    token_file = Path(get("PITWALL_TOKEN_FILE") or _default_token_file()).expanduser()
    db_path = Path(get("PITWALL_DB_PATH") or _default_db_path()).expanduser()
    catalogue = get("PITWALL_CATALOGUE_PATH")

    return Settings(
        client_id=get("PITWALL_CLIENT_ID"),
        vin=get("PITWALL_VIN"),
        container_id=get("PITWALL_CONTAINER_ID"),
        token_file=token_file,
        db_path=db_path,
        daily_quota=daily_quota,
        log_level=(get("PITWALL_LOG_LEVEL") or "INFO").upper(),
        catalogue_path=Path(catalogue).expanduser() if catalogue else _catalogue_path(),
    )
