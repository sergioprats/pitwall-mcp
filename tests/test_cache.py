"""Response cache: TTLs, staleness and raw storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import FAKE_CONTAINER, FAKE_VIN, load_fixture

from pitwall_mcp.storage.cache import CacheStore

NOON = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def test_payload_is_stored_verbatim(db):
    """What BMW sent is what comes back: no normalisation on the way in."""
    payload = load_fixture("telematic_full.json")
    store = CacheStore(db)
    store.put(
        "telematicData",
        payload,
        ttl=timedelta(hours=12),
        vin=FAKE_VIN,
        container_id=FAKE_CONTAINER,
        moment=NOON,
    )
    entry = store.get(
        "telematicData", vin=FAKE_VIN, container_id=FAKE_CONTAINER, moment=NOON
    )
    assert entry.payload == payload


def test_entry_expires_after_its_ttl(db):
    """A 12 h TTL is gone 13 h later."""
    store = CacheStore(db)
    store.put("telematicData", {"a": 1}, ttl=timedelta(hours=12), moment=NOON)
    assert store.get("telematicData", moment=NOON + timedelta(hours=11)) is not None
    assert store.get("telematicData", moment=NOON + timedelta(hours=13)) is None


def test_stale_entries_are_available_on_request(db):
    """The degraded path can still hand back an old reading, clearly labelled."""
    store = CacheStore(db)
    store.put("telematicData", {"a": 1}, ttl=timedelta(hours=12), moment=NOON)
    late = NOON + timedelta(days=3)
    stale = store.get("telematicData", include_stale=True, moment=late)
    assert stale is not None
    assert not stale.is_fresh(late)
    assert stale.age_seconds(late) == 3 * 24 * 3600


def test_keys_do_not_collide(db):
    """Same endpoint, different VIN or container: different rows."""
    store = CacheStore(db)
    store.put("telematicData", {"vin": "A"}, ttl=timedelta(hours=12), vin="A", moment=NOON)
    store.put("telematicData", {"vin": "B"}, ttl=timedelta(hours=12), vin="B", moment=NOON)
    assert store.get("telematicData", vin="A", moment=NOON).payload == {"vin": "A"}
    assert store.get("telematicData", vin="B", moment=NOON).payload == {"vin": "B"}


def test_writing_again_replaces_the_row(db):
    """One live row per key: the newer payload wins."""
    store = CacheStore(db)
    store.put("basicData", {"puStep": "0724"}, ttl=timedelta(days=30), moment=NOON)
    later = NOON + timedelta(days=40)
    store.put("basicData", {"puStep": "0824"}, ttl=timedelta(days=30), moment=later)
    entry = store.get("basicData", moment=later)
    assert entry.payload == {"puStep": "0824"}
    rows = db.connection.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
    assert rows == 1


def test_missing_key_returns_none(db):
    """Nothing cached means `None`, not an invented empty payload."""
    assert CacheStore(db).get("mappings") is None
