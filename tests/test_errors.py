"""Every error must tell the user, in Spanish, what to do next."""

from __future__ import annotations

from bmw_cardata.exceptions import CarDataAuthError, CarDataHTTPError, ExpiredTokenError
from conftest import load_fixture

from pitwall_mcp.cardata import errors


def _http_error(status: int, fixture: str) -> CarDataHTTPError:
    """Build a library HTTP error from an error fixture."""
    payload = load_fixture(fixture)
    return CarDataHTTPError(
        status=status,
        message=payload["exveErrorMsg"],
        error_id=payload["exveErrorId"],
        error_ref=payload["exveErrorRef"],
        note=payload.get("exveNote"),
    )


def test_cu429_is_reported_as_an_account_wide_quota_problem():
    """403 CU-429 is BMW's counter, not ours, and the message says so."""
    translated = errors.translate(_http_error(403, "error_403_cu429.json"))
    assert isinstance(translated, errors.QuotaExhaustedError)
    assert "CU-429" in translated.message
    assert "50 peticiones" in translated.message
    assert "misma cuenta" in translated.message


def test_missing_scope_names_the_scope_and_the_portal():
    """The user needs to know which scope and where to authorise it."""
    translated = errors.translate(_http_error(403, "error_403_scope.json"))
    assert isinstance(translated, errors.MissingScopeError)
    assert "cardata:api:read" in translated.message
    assert "bmw-cardata.bmwgroup.com" in translated.message
    assert "scripts/login.py" in translated.message


def test_401_points_at_the_login_script():
    """An expired token has exactly one fix, and it is a manual one."""
    translated = errors.translate(_http_error(401, "error_401.json"))
    assert isinstance(translated, errors.RefreshTokenExpiredError)
    assert "scripts/login.py" in translated.message
    assert "14 dias" in translated.message


def test_expired_refresh_token_is_recognised():
    """The OAuth `invalid_grant` case, from its fixture."""
    payload = load_fixture("oauth_error_expired.json")
    translated = errors.translate(
        CarDataAuthError(payload["error"], payload["error_description"])
    )
    assert isinstance(translated, errors.RefreshTokenExpiredError)
    assert "device flow" in translated.message


def test_expired_token_subclass_is_recognised_too():
    """The library's dedicated exception maps to the same instruction."""
    translated = errors.translate(ExpiredTokenError("expired_token", "device code expired"))
    assert isinstance(translated, errors.RefreshTokenExpiredError)


def test_server_error_warns_that_quota_was_still_spent():
    """A 500 costs a request all the same; the user should know."""
    error = CarDataHTTPError(status=503, message="temporary failure")
    translated = errors.translate(error)
    assert "consumido cuota" in translated.message


def test_missing_credentials_lists_what_is_missing():
    """Two things are missing, and both are named."""
    translated = errors.missing_credentials(["PITWALL_CLIENT_ID", "fichero de tokens"])
    assert "PITWALL_CLIENT_ID" in translated.message
    assert "scripts/login.py" in translated.message


def test_missing_container_points_at_the_bootstrap_script():
    """The server never creates containers, so the fix is the hand-run script."""
    assert "bootstrap_containers.py" in errors.missing_container().message


def test_empty_container_mentions_data_selection_for_streaming():
    """Streaming selection is portal-only, and that is a different failure."""
    message = errors.empty_container("c1").message
    assert "bootstrap_containers.py --list" in message
    assert "Data Selection" in message


def test_non_primary_vin_is_explained():
    """CarData requires PRIMARY, and no amount of retrying changes that."""
    error = CarDataHTTPError(
        status=403, message="Vehicle mapping is not PRIMARY for this customer"
    )
    translated = errors.translate(error, vin="WBAU11030P0FAKE01")
    assert isinstance(translated, errors.VinNotPrimaryError)
    assert "PRIMARY" in translated.message
