"""Token acquisition, refresh and persistence.

The access token lives one hour and the refresh token fourteen days. Tokens are
stored OUTSIDE the repository, by default in `~/.config/pitwall-mcp/tokens.json`
with mode 600, and never logged.

BMW does not tell us when the refresh token expires, so we record when we got it
and assume the documented 14 days. Once fewer than 3 days remain we warn loudly
on every call, because letting it lapse means redoing the manual browser flow.

REFRESHING ROTATES THE REFRESH TOKEN: after a successful refresh the previous
one is dead. With two processes sharing this file (the MCP server and the
phase-2 streaming daemon) a naive implementation loses the race and forces the
user to redo the manual login, which is exactly what the 3-day warning exists to
prevent. Two rules keep that from happening, and both matter:

* Every read-refresh-write cycle happens inside a cross-process file lock.
* Nothing caches the bundle in memory. The file on disk is the only truth, and
  it is re-read before every decision, because the other process may have
  rotated the token a second ago.

See `docs/streaming-design.md`, section 6.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import TracebackType

try:  # POSIX
    import fcntl

    _MSVCRT = None
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
    import msvcrt as _MSVCRT

from bmw_cardata import DeviceCodeFlowClient
from bmw_cardata.exceptions import CarDataAuthError
from bmw_cardata.models.auth import DeviceCodeResponse

from ..config import REFRESH_TOKEN_LIFETIME, REFRESH_TOKEN_WARNING, Settings
from ..storage.db import parse_iso, to_iso, utc_now
from . import errors

_LOGGER = logging.getLogger(__name__)

#: Refresh a little before the hour is up, so a long call cannot expire midway.
ACCESS_TOKEN_MARGIN = timedelta(minutes=5)

#: How long to wait for the other process to finish its refresh. A refresh is
#: one HTTP round trip, so 30 s is generous; waiting is always better than
#: racing, because losing the race costs a manual browser login.
LOCK_TIMEOUT = timedelta(seconds=30)

_LOCK_POLL_SECONDS = 0.1


class TokenLock:
    """Exclusive lock over the token file, shared across processes.

    Uses a sidecar `.lock` file rather than the token file itself: locking a
    file that is replaced atomically on every write would drop the lock along
    with the old inode.

    Acquisition polls instead of blocking, so waiting never freezes the event
    loop of an MCP server serving other tools meanwhile.
    """

    def __init__(self, path: Path, *, timeout: timedelta = LOCK_TIMEOUT) -> None:
        """Bind the lock to a lock-file path and a maximum wait."""
        self.path = path
        self.timeout = timeout
        self._fd: int | None = None

    def _try_acquire(self) -> bool:
        """Attempt one non-blocking acquisition. True when we got the lock."""
        if self._fd is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                os.lseek(self._fd, 0, os.SEEK_SET)
                _MSVCRT.locking(self._fd, _MSVCRT.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def release(self) -> None:
        """Release the lock and close the descriptor. Safe to call twice."""
        if self._fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            else:
                os.lseek(self._fd, 0, os.SEEK_SET)
                _MSVCRT.locking(self._fd, _MSVCRT.LK_UNLCK, 1)
        except OSError as err:
            _LOGGER.debug("Could not release the token lock: %s", err)
        finally:
            os.close(self._fd)
            self._fd = None

    async def __aenter__(self) -> TokenLock:
        """Acquire the lock, waiting up to `timeout`."""
        waited = 0.0
        while not self._try_acquire():
            if waited >= self.timeout.total_seconds():
                self.release()
                raise errors.token_lock_timeout(self.path, self.timeout)
            await asyncio.sleep(_LOCK_POLL_SECONDS)
            waited += _LOCK_POLL_SECONDS
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release the lock on the way out, however we leave."""
        self.release()


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
        self.lock_path = path.with_name(f"{path.name}.lock")

    def lock(self, *, timeout: timedelta = LOCK_TIMEOUT) -> TokenLock:
        """Return the cross-process lock guarding this token file."""
        return TokenLock(self.lock_path, timeout=timeout)

    def load(self) -> TokenBundle | None:
        """Read the bundle from disk, or `None` when there is no token file.

        This ALWAYS hits the disk. Caching the result would reintroduce the
        rotation race the lock exists to prevent: the other process may have
        replaced the file a moment ago, and a stale in-memory copy carries a
        refresh token that BMW has already invalidated.
        """
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

    @property
    def bundle(self) -> TokenBundle | None:
        """The bundle as it is on disk RIGHT NOW.

        Deliberately not memoised: another process may have rotated the tokens
        since the last call, and acting on a stale copy is what invalidates the
        refresh token and forces a manual login.
        """
        return self._store.load()

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
        """Exchange the refresh token for a new access token and persist it.

        The whole read-refresh-write cycle runs under the cross-process lock.
        Once inside, the file is re-read: if the process we waited for already
        refreshed, its brand-new access token is returned and no second refresh
        is sent. Sending one would rotate the token again and invalidate the
        copy the other process just wrote.
        """
        client_id = self._settings.client_id
        if not client_id:
            raise errors.missing_credentials(["PITWALL_CLIENT_ID"])

        async with self._store.lock():
            bundle = self.require_credentials()
            if bundle.access_is_valid():
                _LOGGER.debug("Another process refreshed while we waited; reusing it")
                return bundle.access_token

            async with DeviceCodeFlowClient(
                client_id, refresh_token=bundle.refresh_token
            ) as flow:
                try:
                    token = await flow.refresh()
                except CarDataAuthError as err:
                    raise errors.translate_auth_error(err) from err

            updated = _bundle_from_token(token, previous=bundle)
            self._store.save(updated)

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
        # Only the write is locked, not the polling: waiting for a human with a
        # browser can take minutes, and holding the lock that long would stall
        # any other process that just needed to refresh.
        async with self._store.lock():
            self._store.save(bundle)
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
