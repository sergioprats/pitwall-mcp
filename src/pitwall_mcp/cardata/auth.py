"""Token acquisition, refresh and persistence.

The access token lives one hour and the refresh token fourteen days. Tokens are
stored OUTSIDE the repository, by default in `~/.config/pitwall-mcp/tokens.json`
with mode 600, and never logged.

BMW does not tell us when the refresh token expires, so we record when we got it
and assume the documented 14 days. Once fewer than 3 days remain we warn loudly
on every call, because letting it lapse means redoing the manual browser flow.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from bmw_cardata import DeviceCodeFlowClient
from bmw_cardata.exceptions import CarDataAuthError
from bmw_cardata.models.auth import DeviceCodeResponse

from ..config import REFRESH_TOKEN_LIFETIME, REFRESH_TOKEN_WARNING, Settings
from ..storage.db import parse_iso, to_iso, utc_now
from . import errors

_LOGGER = logging.getLogger(__name__)

#: Refresh a little before the hour is up, so a long call cannot expire midway.
ACCESS_TOKEN_MARGIN = timedelta(minutes=5)


@dataclass
class TokenBundle:
    """Everything we keep from a token response. Never logged, never printed."""

    access_token: str
    refresh_token: str
    gcid: str
    expires_at: str
    refresh_obtained_at: str
    id_token: str | None = None
    scope: str | None = None

    @property
    def access_expires_at(self) -> datetime | None:
        """When the access token stops being valid."""
        return parse_iso(self.expires_at)

    @property
    def refresh_expires_at(self) -> datetime | None:
        """Assumed refresh-token expiry: 14 days after we obtained it."""
        obtained = parse_iso(self.refresh_obtained_at)
        return None if obtained is None else obtained + REFRESH_TOKEN_LIFETIME

    def access_is_valid(self, now: datetime | None = None) -> bool:
        """True while the access token can still be used."""
        expires = self.access_expires_at
        if expires is None:
            return False
        return (now or utc_now()) + ACCESS_TOKEN_MARGIN < expires

    def refresh_time_left(self, now: datetime | None = None) -> timedelta | None:
        """Time left on the refresh token under the 14-day assumption."""
        expires = self.refresh_expires_at
        return None if expires is None else expires - (now or utc_now())


class TokenStore:
    """Reads and writes the token file with restrictive permissions."""

    def __init__(self, path: Path) -> None:
        """Bind the store to a token file path outside the repository."""
        self.path = path

    def load(self) -> TokenBundle | None:
        """Return the stored bundle, or `None` when there is no token file."""
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return TokenBundle(**data)
        except (json.JSONDecodeError, TypeError, ValueError) as err:
            _LOGGER.warning("Unreadable token file at %s: %s", self.path, err)
            return None

    def save(self, bundle: TokenBundle) -> None:
        """Write the bundle atomically and restrict it to the owner (mode 600)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(bundle), indent=2), encoding="utf-8")
        try:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as err:  # Windows may refuse; the file still stays local.
            _LOGGER.debug("Could not chmod 600 the token file: %s", err)
        temporary.replace(self.path)


class TokenManager:
    """Provides fresh access tokens to the REST adapter."""

    def __init__(self, settings: Settings, *, store: TokenStore | None = None) -> None:
        """Bind the manager to the resolved settings and a token store."""
        self._settings = settings
        self._store = store or TokenStore(settings.token_file)
        self._bundle: TokenBundle | None = None

    @property
    def bundle(self) -> TokenBundle | None:
        """The bundle currently in memory, loading it from disk on first use."""
        if self._bundle is None:
            self._bundle = self._store.load()
        return self._bundle

    @property
    def gcid(self) -> str | None:
        """The account's GCID, needed for the phase-2 streaming topic."""
        return self.bundle.gcid if self.bundle else None

    def require_credentials(self) -> TokenBundle:
        """Return the stored bundle or raise a Spanish, actionable error."""
        if not self._settings.client_id:
            raise errors.missing_credentials(self._settings.missing_for_api())
        bundle = self.bundle
        if bundle is None:
            raise errors.missing_credentials(self._settings.missing_for_api())
        return bundle

    def refresh_warning(self, now: datetime | None = None) -> str | None:
        """A visible warning when fewer than 3 days of refresh token remain."""
        bundle = self.bundle
        if bundle is None:
            return None
        left = bundle.refresh_time_left(now)
        if left is None:
            return None
        if left <= timedelta(0):
            return (
                "AVISO: el refresh token ha caducado (dura 14 dias). Ejecuta "
                "'python scripts/login.py' y completa el device flow en el navegador."
            )
        if left <= REFRESH_TOKEN_WARNING:
            hours = int(left.total_seconds() // 3600)
            return (
                f"AVISO: al refresh token le quedan menos de 3 dias ({hours} h). "
                f"Cuando caduque habra que repetir el login manual: ejecuta "
                f"'python scripts/login.py' antes de que ocurra."
            )
        return None

    async def access_token(self) -> str:
        """Return a valid access token, refreshing it if needed.

        This is the callable handed to `CarDataClient`, so the adapter never
        holds a stale token. Refreshing does NOT touch the CarData REST API and
        therefore does not consume quota.
        """
        bundle = self.require_credentials()
        if bundle.access_is_valid():
            return bundle.access_token
        return await self.refresh()

    async def refresh(self) -> str:
        """Exchange the refresh token for a new access token and persist it."""
        bundle = self.require_credentials()
        client_id = self._settings.client_id
        if not client_id:
            raise errors.missing_credentials(["PITWALL_CLIENT_ID"])

        async with DeviceCodeFlowClient(client_id, refresh_token=bundle.refresh_token) as flow:
            try:
                token = await flow.refresh()
            except CarDataAuthError as err:
                raise errors.translate_auth_error(err) from err

        updated = _bundle_from_token(token, previous=bundle)
        self._store.save(updated)
        self._bundle = updated
        _LOGGER.info("Access token refreshed; refresh token rotated")
        return updated.access_token

    async def device_login(self, on_prompt) -> TokenBundle:  # noqa: ANN001 - callback
        """Run the full device-code flow, calling `on_prompt` with the user code.

        Used only by `scripts/login.py`; the MCP server never starts a login on
        its own, because the flow needs a human with a browser.
        """
        client_id = self._settings.client_id
        if not client_id:
            raise errors.missing_credentials(["PITWALL_CLIENT_ID"])

        async with DeviceCodeFlowClient(client_id) as flow:
            try:
                device: DeviceCodeResponse = await flow.request_device_code()
                on_prompt(device)
                token = await flow.poll_for_token(device.device_code, interval=device.interval)
            except CarDataAuthError as err:
                raise errors.translate_auth_error(err) from err

        bundle = _bundle_from_token(token)
        self._store.save(bundle)
        self._bundle = bundle
        return bundle


def _bundle_from_token(token, previous: TokenBundle | None = None) -> TokenBundle:  # noqa: ANN001
    """Build a `TokenBundle` from a library `TokenResponse`.

    A refresh response rotates the refresh token, so `refresh_obtained_at` is
    reset every time: the 14-day clock starts again on each successful refresh.
    """
    now = utc_now()
    return TokenBundle(
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        gcid=token.gcid or (previous.gcid if previous else ""),
        expires_at=to_iso(now + timedelta(seconds=int(token.expires_in or 3600))),
        refresh_obtained_at=to_iso(now),
        id_token=token.id_token,
        scope=token.scope,
    )
