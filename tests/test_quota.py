"""Quota ledger: counting, the daily cap, the UTC window and CU-429."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pitwall_mcp.storage.quota import (
    BMW_DAILY_LIMIT,
    QUOTA_ERROR_ID,
    QuotaExceededError,
    QuotaStore,
)

NOON = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def test_counter_starts_at_zero(db):
    """A fresh installation has spent nothing."""
    status = QuotaStore(db, daily_limit=20).status(NOON)
    assert status.used == 0
    assert status.remaining == 20
    assert not status.exhausted


def test_every_request_counts(db):
    """One row per request actually sent."""
    store = QuotaStore(db, daily_limit=20)
    for _ in range(3):
        store.record("telematicData", vin="VIN", http_status=200, moment=NOON)
    assert store.status(NOON).used == 3


def test_failed_requests_count_too(db):
    """BMW counts a rejected request; so do we."""
    store = QuotaStore(db, daily_limit=20)
    store.record("telematicData", http_status=500, moment=NOON)
    assert store.status(NOON).used == 1


def test_local_cap_can_never_exceed_the_bmw_limit(db):
    """Configuring 500 does not give us 500 requests."""
    assert QuotaStore(db, daily_limit=500).daily_limit == BMW_DAILY_LIMIT


def test_cap_blocks_further_requests(db):
    """`check` raises once the local cap is reached."""
    store = QuotaStore(db, daily_limit=2)
    store.record("mappings", http_status=200, moment=NOON)
    store.record("mappings", http_status=200, moment=NOON)
    with pytest.raises(QuotaExceededError) as excinfo:
        store.check(NOON)
    message = excinfo.value.status.spanish_message()
    assert "Cuota agotada" in message
    assert "2/2" in message
    assert "se reinicia" in message


def test_cu429_marks_the_account_as_denied(db):
    """A CU-429 from BMW exhausts the day even if our own cap has room."""
    store = QuotaStore(db, daily_limit=20)
    store.record("telematicData", http_status=403, error_id=QUOTA_ERROR_ID, moment=NOON)
    status = store.status(NOON)
    assert status.remote_denied
    assert status.exhausted
    assert status.remaining == 19  # the local cap alone would still allow more
    assert QUOTA_ERROR_ID in status.spanish_message()


def test_window_is_the_natural_utc_day(db):
    """Yesterday's requests do not count against today."""
    store = QuotaStore(db, daily_limit=20)
    store.record("mappings", http_status=200, moment=NOON - timedelta(days=1))
    store.record("mappings", http_status=200, moment=NOON)
    status = store.status(NOON)
    assert status.used == 1
    assert status.window_start == datetime(2026, 9, 5, 0, 0, tzinfo=UTC)
    assert status.resets_at == datetime(2026, 9, 6, 0, 0, tzinfo=UTC)


def test_message_states_the_utc_assumption(db):
    """The reset timezone is unknown, and the message says so."""
    store = QuotaStore(db, daily_limit=1)
    store.record("mappings", http_status=200, moment=NOON)
    assert "UTC" in store.status(NOON).spanish_message()


def test_counter_is_derived_not_stored(db):
    """Deleting the log resets the counter: there is no second source of truth."""
    store = QuotaStore(db, daily_limit=20)
    store.record("mappings", http_status=200, moment=NOON)
    db.connection.execute("DELETE FROM quota_log")
    assert store.status(NOON).used == 0
