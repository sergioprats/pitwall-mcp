"""Token persistence, and the refresh race that would cost a manual login.

BMW rotates the refresh token on every refresh. Two processes share one token
file (the MCP server and the phase-2 streaming daemon), so refreshing without
coordination invalidates the other's copy and forces the user back through the
browser device flow. These tests pin down the lock and the no-caching rule that
prevent it. See `docs/streaming-design.md`, section 6.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import timedelta

import pytest

from pitwall_mcp.cardata import auth, errors
from pitwall_mcp.cardata.auth import TokenBundle, TokenLock, TokenManager, TokenStore
from pitwall_mcp.storage.db import to_iso, utc_now


@dataclass
class FakeTokenResponse:
    """What the library returns from a refresh."""

    access_token: str
    refresh_token: str
    gcid: str = "gcid-de-prueba"
    expires_in: int = 3600
    id_token: str | None = "id-token-de-prueba"
    scope: str | None = "openid cardata:api:read"


class FakeFlow:
    """Stands in for `DeviceCodeFlowClient`, counting refreshes."""

    calls: list[str] = []
    latency: float = 0.0
    counter: int = 0

    def __init__(self, client_id: str, *, refresh_token: str | None = None) -> None:
        """Record which refresh token the caller brought."""
        self.refresh_token = refresh_token

    async def __aenter__(self):
        """Enter the async context."""
        return self

    async def __aexit__(self, *args) -> None:
        """Leave the async context."""

    async def refresh(self) -> FakeTokenResponse:
        """Rotate the token, as BMW does."""
        FakeFlow.calls.append(self.refresh_token or "")
        FakeFlow.counter += 1
        if FakeFlow.latency:
            await asyncio.sleep(FakeFlow.latency)
        n = FakeFlow.counter
        return FakeTokenResponse(access_token=f"access-{n}", refresh_token=f"refresh-{n}")


@pytest.fixture
def fake_flow(monkeypatch):
    """Install `FakeFlow` in place of the library's device-code client."""
    FakeFlow.calls = []
    FakeFlow.latency = 0.0
    FakeFlow.counter = 0
    monkeypatch.setattr(auth, "DeviceCodeFlowClient", FakeFlow)
    return FakeFlow


def write_bundle(
    settings,
    *,
    access_token="access-0",
    expired=False,
    refresh_age=timedelta(days=2),
) -> TokenBundle:
    """Put a bundle on disk.

    `expired` kills the one-hour access token; `refresh_age` ages the 14-day
    refresh token, which is what drives the "menos de 3 dias" warning.
    """
    now = utc_now()
    bundle = TokenBundle(
        access_token=access_token,
        refresh_token="refresh-0",
        gcid="gcid-de-prueba",
        expires_at=to_iso(now - timedelta(minutes=1) if expired else now + timedelta(hours=1)),
        refresh_obtained_at=to_iso(now - refresh_age),
        id_token="id-token-de-prueba",
    )
    TokenStore(settings.token_file).save(bundle)
    return bundle


# --- The lock --------------------------------------------------------------


async def test_the_lock_is_exclusive(tmp_path):
    """A second acquisition waits, and gives up with an actionable error."""
    first = TokenLock(tmp_path / "tokens.json.lock")
    second = TokenLock(tmp_path / "tokens.json.lock", timeout=timedelta(seconds=0.3))

    async with first:
        with pytest.raises(errors.TokenLockTimeoutError) as excinfo:
            async with second:
                pass

    assert "renovando los tokens" in excinfo.value.message
    assert "streaming" in excinfo.value.message


async def test_the_lock_is_released_on_exit(tmp_path):
    """Once released, the next process gets it immediately."""
    path = tmp_path / "tokens.json.lock"
    async with TokenLock(path):
        pass
    async with TokenLock(path, timeout=timedelta(seconds=0.3)):
        pass  # no exception: the lock was free


async def test_the_lock_is_released_even_if_the_body_raises(tmp_path):
    """A failed refresh must not leave the lock held forever."""
    path = tmp_path / "tokens.json.lock"
    with pytest.raises(RuntimeError):
        async with TokenLock(path):
            raise RuntimeError("refresh fallido")
    async with TokenLock(path, timeout=timedelta(seconds=0.3)):
        pass


async def test_the_lock_holds_against_a_real_second_process(tmp_path):
    """Not a mock: another OS process must actually be kept out.

    This is the case that matters, because the phase-2 daemon is a separate
    process, not a task in this event loop.
    """
    lock_path = tmp_path / "tokens.json.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import asyncio, sys
                sys.path.insert(0, {str(tmp_path.parents[0])!r})
                from pitwall_mcp.cardata.auth import TokenLock
                from pathlib import Path

                async def main():
                    async with TokenLock(Path({str(lock_path)!r})):
                        print("held", flush=True)
                        await asyncio.sleep(5)

                asyncio.run(main())
                """
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(errors.TokenLockTimeoutError):
            async with TokenLock(lock_path, timeout=timedelta(seconds=0.5)):
                pass
    finally:
        holder.kill()
        holder.wait()

    # And once that process is gone, the lock is free again.
    async with TokenLock(lock_path, timeout=timedelta(seconds=2)):
        pass


# --- No caching ------------------------------------------------------------


def test_the_bundle_is_never_cached_in_memory(settings):
    """The file on disk is the only truth; another process may have rewritten it."""
    write_bundle(settings, access_token="access-viejo")
    manager = TokenManager(settings)
    assert manager.bundle.access_token == "access-viejo"

    # Another process rotates the tokens behind our back.
    write_bundle(settings, access_token="access-nuevo")

    assert manager.bundle.access_token == "access-nuevo"


def test_a_deleted_token_file_is_noticed(settings):
    """Losing the file is reported, not papered over with a stale copy."""
    write_bundle(settings)
    manager = TokenManager(settings)
    assert manager.bundle is not None

    settings.token_file.unlink()

    assert manager.bundle is None
    with pytest.raises(errors.MissingCredentialsError):
        manager.require_credentials()


# --- Refreshing ------------------------------------------------------------


async def test_a_valid_access_token_is_not_refreshed(settings, fake_flow):
    """No point rotating a token that still works."""
    write_bundle(settings, access_token="access-vigente")
    token = await TokenManager(settings).access_token()

    assert token == "access-vigente"
    assert fake_flow.calls == []


async def test_an_expired_token_is_refreshed_and_persisted(settings, fake_flow):
    """The new bundle reaches disk, so the other process can see it."""
    write_bundle(settings, expired=True)
    manager = TokenManager(settings)

    token = await manager.access_token()

    assert token == "access-1"
    assert fake_flow.calls == ["refresh-0"]
    stored = json.loads(settings.token_file.read_text(encoding="utf-8"))
    assert stored["access_token"] == "access-1"
    assert stored["refresh_token"] == "refresh-1"


async def test_refreshing_restarts_the_fourteen_day_clock(settings, fake_flow):
    """BMW rotates the refresh token, so its 14 days start again."""
    # 12 days old: 2 days left, so the "menos de 3 dias" warning is due.
    write_bundle(settings, expired=True, refresh_age=timedelta(days=12))
    manager = TokenManager(settings)
    assert "menos de 3 dias" in manager.refresh_warning()

    await manager.refresh()

    assert manager.refresh_warning() is None


async def test_only_one_refresh_happens_when_two_managers_race(settings, fake_flow):
    """THE test: the loser reuses the winner's token instead of rotating again.

    A second refresh would invalidate the token the first process just wrote,
    and the user would have to redo the browser login.
    """
    write_bundle(settings, expired=True)
    fake_flow.latency = 0.2

    first = TokenManager(settings)
    second = TokenManager(settings)
    tokens = await asyncio.gather(first.access_token(), second.access_token())

    assert fake_flow.calls == ["refresh-0"], "se ha refrescado dos veces: token rotado en vano"
    assert tokens == ["access-1", "access-1"]


async def test_the_loser_never_reuses_the_dead_refresh_token(settings, fake_flow):
    """Whatever happens, nobody sends a refresh token that was already rotated."""
    write_bundle(settings, expired=True)
    fake_flow.latency = 0.1

    managers = [TokenManager(settings) for _ in range(4)]
    await asyncio.gather(*(m.access_token() for m in managers))

    assert len(fake_flow.calls) == len(set(fake_flow.calls))


async def test_a_failed_refresh_leaves_the_old_bundle_untouched(settings, fake_flow, monkeypatch):
    """A rejected refresh must not corrupt the file for the other process."""
    write_bundle(settings, expired=True)
    before = settings.token_file.read_text(encoding="utf-8")

    async def explode(self):
        from bmw_cardata.exceptions import ExpiredTokenError

        raise ExpiredTokenError("invalid_grant", "refresh token expired")

    monkeypatch.setattr(FakeFlow, "refresh", explode)

    with pytest.raises(errors.RefreshTokenExpiredError):
        await TokenManager(settings).refresh()

    assert settings.token_file.read_text(encoding="utf-8") == before


async def test_a_failed_refresh_releases_the_lock(settings, fake_flow, monkeypatch):
    """Otherwise the daemon would be locked out until the server restarts."""
    write_bundle(settings, expired=True)

    async def explode(self):
        from bmw_cardata.exceptions import CarDataAuthError

        raise CarDataAuthError("temporarily_unavailable")

    monkeypatch.setattr(FakeFlow, "refresh", explode)
    manager = TokenManager(settings)
    with pytest.raises(errors.PitwallError):
        await manager.refresh()

    async with TokenLock(TokenStore(settings.token_file).lock_path, timeout=timedelta(seconds=1)):
        pass


# --- The file itself -------------------------------------------------------


def test_the_lock_file_sits_beside_the_token_file_not_on_it(settings):
    """The token file is replaced atomically; a lock on it would vanish with it."""
    store = TokenStore(settings.token_file)
    assert store.lock_path != store.path
    assert store.lock_path.name.endswith(".lock")
    assert store.lock_path.parent == store.path.parent


def test_saving_writes_the_whole_bundle(settings):
    """Round trip, including the fields phase 2 needs."""
    original = write_bundle(settings)
    reloaded = TokenStore(settings.token_file).load()
    assert reloaded == original
    assert reloaded.gcid == "gcid-de-prueba"
    assert reloaded.id_token == "id-token-de-prueba"


def test_an_unreadable_token_file_is_reported_as_missing(settings):
    """Corrupt JSON must not crash a tool; it means "log in again"."""
    settings.token_file.parent.mkdir(parents=True, exist_ok=True)
    settings.token_file.write_text("{esto no es json", encoding="utf-8")
    assert TokenStore(settings.token_file).load() is None


# --- Device login ----------------------------------------------------------


class FakeDevice:
    """The device-code response the user has to act on."""

    user_code = "ABCD-1234"
    device_code = "device-code-de-prueba"
    verification_uri = "https://customer.bmwgroup.com/gcdm/oauth/device"
    verification_uri_complete = None
    expires_in = 600
    interval = 1


async def test_device_login_stores_the_bundle_and_prompts_the_user(
    settings, fake_flow, monkeypatch
):
    """The flow is useless unless the user is told the code and the URL."""
    prompted = []

    async def request_device_code(self):
        return FakeDevice()

    async def poll_for_token(self, device_code, *, code_verifier=None, interval=5):
        assert device_code == FakeDevice.device_code
        return FakeTokenResponse(access_token="access-login", refresh_token="refresh-login")

    monkeypatch.setattr(FakeFlow, "request_device_code", request_device_code, raising=False)
    monkeypatch.setattr(FakeFlow, "poll_for_token", poll_for_token, raising=False)

    bundle = await TokenManager(settings).device_login(prompted.append)

    assert prompted and prompted[0].user_code == "ABCD-1234"
    assert bundle.access_token == "access-login"
    assert bundle.gcid == "gcid-de-prueba"
    assert json.loads(settings.token_file.read_text(encoding="utf-8"))["refresh_token"] == (
        "refresh-login"
    )


async def test_device_login_does_not_hold_the_lock_while_waiting_for_the_human(
    settings, fake_flow, monkeypatch
):
    """Polling can take minutes; the daemon must still be able to refresh."""
    lock_free_while_polling = False

    async def request_device_code(self):
        return FakeDevice()

    async def poll_for_token(self, device_code, *, code_verifier=None, interval=5):
        nonlocal lock_free_while_polling
        try:
            async with TokenLock(
                TokenStore(settings.token_file).lock_path, timeout=timedelta(seconds=0.3)
            ):
                lock_free_while_polling = True
        except errors.TokenLockTimeoutError:
            lock_free_while_polling = False
        return FakeTokenResponse(access_token="access-login", refresh_token="refresh-login")

    monkeypatch.setattr(FakeFlow, "request_device_code", request_device_code, raising=False)
    monkeypatch.setattr(FakeFlow, "poll_for_token", poll_for_token, raising=False)

    await TokenManager(settings).device_login(lambda device: None)

    assert lock_free_while_polling, "device_login retiene el cerrojo mientras espera al usuario"


async def test_device_login_without_a_client_id_says_what_to_configure(
    bare_settings, fake_flow
):
    """No client id, no flow, and the message names the variable."""
    with pytest.raises(errors.MissingCredentialsError) as excinfo:
        await TokenManager(bare_settings).device_login(lambda device: None)
    assert "PITWALL_CLIENT_ID" in excinfo.value.message
