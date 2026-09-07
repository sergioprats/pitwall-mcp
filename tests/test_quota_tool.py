"""`get_api_quota`: what the user reads when they need to know what is going on.

These are the branches that only appear when things are going badly — quota
spent, BMW refusing, cache gone stale — which is exactly when the wording has
to be right.
"""

from __future__ import annotations

from datetime import timedelta

from conftest import FAKE_CONTAINER, FAKE_VIN, FakeTokens, load_fixture

from pitwall_mcp.cardata.client import (
    ENDPOINT_BASIC_DATA,
    ENDPOINT_MAPPINGS,
    ENDPOINT_TELEMATIC,
    CarDataAdapter,
)
from pitwall_mcp.storage.db import utc_now
from pitwall_mcp.storage.quota import QUOTA_ERROR_ID
from pitwall_mcp.tools.quota_tools import get_api_quota


def build(settings, db, **overrides):
    """An adapter with fake tokens, plus the settings the tool renders."""
    resolved = settings.__class__(**{**settings.__dict__, **overrides})
    return CarDataAdapter(resolved, db=db, tokens=FakeTokens()), resolved


def test_a_fresh_installation_reports_an_empty_state(settings, db):
    """Nothing spent, nothing cached, and it says so instead of showing zeros."""
    adapter, resolved = build(settings, db)
    text = get_api_quota(adapter, resolved)

    assert "0 de 20" in text
    assert "Quedan 20" in text
    assert "Cache vacia" in text
    assert "no gasta cuota" in text


def test_spent_requests_are_counted_and_timed(settings, db):
    """Used, remaining, and when the first and last request happened."""
    adapter, resolved = build(settings, db)
    for _ in range(3):
        adapter.quota.record(ENDPOINT_TELEMATIC, vin=FAKE_VIN, http_status=200)

    text = get_api_quota(adapter, resolved)

    assert "3 de 20" in text
    assert "Quedan 17" in text
    assert "Primera peticion de hoy" in text
    assert "Ultima peticion de hoy" in text


def test_reaching_the_local_cap_is_stated_plainly(settings, db):
    """The user needs to know nothing else will be sent today."""
    adapter, resolved = build(settings, db, daily_quota=2)
    adapter.quota.record(ENDPOINT_MAPPINGS, http_status=200)
    adapter.quota.record(ENDPOINT_MAPPINGS, http_status=200)

    text = get_api_quota(adapter, resolved)

    assert "Tope local alcanzado" in text
    assert "Quedan 0" in text


def test_a_cu429_is_reported_as_an_account_problem(settings, db):
    """BMW's own counter is exhausted, which is worse than our cap."""
    adapter, resolved = build(settings, db)
    adapter.quota.record(
        ENDPOINT_TELEMATIC, vin=FAKE_VIN, http_status=403, error_id=QUOTA_ERROR_ID
    )

    text = get_api_quota(adapter, resolved)

    assert QUOTA_ERROR_ID in text
    assert "cuota de la CUENTA" in text
    assert "misma cuenta" in text  # other apps share the budget


def test_the_utc_assumption_is_never_hidden(settings, db):
    """We do not know when BMW resets, and the tool must not pretend we do."""
    adapter, resolved = build(settings, db)
    text = get_api_quota(adapter, resolved)

    assert "Suposicion" in text
    assert "no esta documentado" in text
    assert "Reinicio estimado" in text


def test_a_fresh_cache_entry_is_shown_as_valid(settings, db):
    """Serving from here costs nothing, so the user should see it is there."""
    adapter, resolved = build(settings, db)
    adapter._cache.put(  # noqa: SLF001 - seeding the cache is the point
        ENDPOINT_TELEMATIC,
        load_fixture("telematic_full.json"),
        ttl=timedelta(hours=12),
        vin=FAKE_VIN,
        container_id=FAKE_CONTAINER,
    )

    text = get_api_quota(adapter, resolved)

    assert "vigente" in text
    assert "Cache vacia" not in text
    assert "caduca" in text


def test_an_expired_cache_entry_is_shown_as_expired(settings, db):
    """A stale entry is not the same as a fresh one, and must not look like it."""
    adapter, resolved = build(settings, db)
    adapter._cache.put(  # noqa: SLF001
        ENDPOINT_BASIC_DATA,
        load_fixture("basic_data.json"),
        ttl=timedelta(days=30),
        vin=FAKE_VIN,
        moment=utc_now() - timedelta(days=40),
    )

    text = get_api_quota(adapter, resolved)

    assert "CADUCADA" in text


def test_ttls_are_readable(settings, db):
    """`30 days, 0:00:00` is a repr; the user gets Spanish."""
    adapter, resolved = build(settings, db)
    text = get_api_quota(adapter, resolved)

    assert "TTL 30 dias" in text
    assert "TTL 12 h" in text
    assert "0:00:00" not in text


def test_missing_credentials_are_flagged_even_with_quota_left(settings, db):
    """Having budget is useless if nothing can be sent."""
    adapter, resolved = build(settings, db, client_id=None)
    text = get_api_quota(adapter, resolved)

    assert "faltan credenciales" in text
    assert "scripts/login.py" in text


def test_the_refresh_token_warning_is_surfaced_here_too(settings, db):
    """It is the tool people check; a token about to die belongs in it."""
    resolved = settings.__class__(**settings.__dict__)
    adapter = CarDataAdapter(
        resolved, db=db, tokens=FakeTokens(warning="AVISO: quedan menos de 3 dias")
    )

    assert "menos de 3 dias" in get_api_quota(adapter, resolved)
