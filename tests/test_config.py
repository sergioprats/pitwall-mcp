"""Configuration: where the tokens live, where the database lives, and the cap.

A bug here is silent and expensive. Misreading `PITWALL_DAILY_QUOTA` would let
the server spend more of the 50 daily requests than intended; misreading a path
would put a token file somewhere the user did not expect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pitwall_mcp import config
from pitwall_mcp.cardata.errors import PitwallError
from pitwall_mcp.catalogue import load_catalogue
from pitwall_mcp.config import (
    DEFAULT_DAILY_QUOTA,
    TTLS,
    Settings,
    find_dotenv,
    load_dotenv,
    load_settings,
)
from pitwall_mcp.storage.quota import BMW_DAILY_LIMIT


def write_env(tmp_path: Path, body: str) -> Path:
    """Write a .env file and return its path."""
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    return path


# --- .env parsing ----------------------------------------------------------


def test_dotenv_parses_the_shapes_people_actually_write(tmp_path):
    """Comments, blanks, quotes and spaces around the equals sign."""
    path = write_env(
        tmp_path,
        "\n".join(
            [
                "# un comentario",
                "",
                "PITWALL_CLIENT_ID=abc-123",
                '  PITWALL_VIN = "WBAU11030P0FAKE01" ',
                "PITWALL_CONTAINER_ID='c-999'",
                "ESTO_NO_ES_UNA_ASIGNACION",
            ]
        ),
    )
    values = load_dotenv(path)

    assert values["PITWALL_CLIENT_ID"] == "abc-123"
    assert values["PITWALL_VIN"] == "WBAU11030P0FAKE01"
    assert values["PITWALL_CONTAINER_ID"] == "c-999"
    assert "ESTO_NO_ES_UNA_ASIGNACION" not in values


def test_a_missing_dotenv_is_not_an_error(tmp_path):
    """Not having a .env is a normal state, not a crash."""
    assert load_dotenv(tmp_path / "no-existe.env") == {}


def test_a_value_containing_an_equals_sign_survives(tmp_path):
    """Only the first `=` separates; the rest belongs to the value."""
    path = write_env(tmp_path, "PITWALL_DB_PATH=C:/ruta/con=igual/pitwall.db")
    assert load_dotenv(path)["PITWALL_DB_PATH"] == "C:/ruta/con=igual/pitwall.db"


# --- Precedence ------------------------------------------------------------


def test_the_environment_wins_over_the_dotenv(tmp_path):
    """A variable set for the process overrides the file."""
    path = write_env(tmp_path, "PITWALL_CLIENT_ID=del-fichero")
    settings = load_settings({"PITWALL_CLIENT_ID": "del-entorno"}, dotenv=path)
    assert settings.client_id == "del-entorno"


def test_the_dotenv_fills_what_the_environment_lacks(tmp_path):
    """The file is the fallback, not a second-class citizen."""
    path = write_env(tmp_path, "PITWALL_VIN=WBAU11030P0FAKE01")
    settings = load_settings({}, dotenv=path)
    assert settings.vin == "WBAU11030P0FAKE01"


def test_an_empty_value_counts_as_absent(tmp_path):
    """`.env.example` ships every key empty; empty must mean "not configured"."""
    path = write_env(tmp_path, "PITWALL_CLIENT_ID=\nPITWALL_VIN=   ")
    settings = load_settings({}, dotenv=path)
    assert settings.client_id is None
    assert settings.vin is None


# --- The quota cap ---------------------------------------------------------


def test_the_default_cap_leaves_margin(tmp_path):
    """20 of BMW's 50, on purpose."""
    settings = load_settings({}, dotenv=tmp_path / "no-existe.env")
    assert settings.daily_quota == DEFAULT_DAILY_QUOTA == 20


def test_the_cap_can_never_exceed_bmws_limit(tmp_path):
    """Asking for 500 does not create requests that do not exist."""
    settings = load_settings({"PITWALL_DAILY_QUOTA": "500"}, dotenv=tmp_path / "x.env")
    assert settings.daily_quota == BMW_DAILY_LIMIT == 50


def test_a_nonsense_cap_falls_back_to_the_default(tmp_path):
    """A typo must not disable the counter, and must not crash the server."""
    settings = load_settings({"PITWALL_DAILY_QUOTA": "veinte"}, dotenv=tmp_path / "x.env")
    assert settings.daily_quota == DEFAULT_DAILY_QUOTA


def test_a_negative_cap_becomes_zero(tmp_path):
    """Zero means "send nothing", which is a coherent, safe reading."""
    settings = load_settings({"PITWALL_DAILY_QUOTA": "-5"}, dotenv=tmp_path / "x.env")
    assert settings.daily_quota == 0


# --- Paths -----------------------------------------------------------------


def test_state_lives_outside_the_repository_by_default(tmp_path):
    """Tokens and database must never default to somewhere inside the repo."""
    settings = load_settings({}, dotenv=tmp_path / "x.env")
    repo_root = Path(__file__).resolve().parents[1]

    assert repo_root not in settings.token_file.parents
    assert repo_root not in settings.db_path.parents
    assert settings.token_file.name == "tokens.json"


def test_a_tilde_in_a_path_is_expanded(tmp_path):
    """`~/pitwall.db` must not become a directory literally called `~`."""
    settings = load_settings({"PITWALL_DB_PATH": "~/pitwall.db"}, dotenv=tmp_path / "x.env")
    assert "~" not in str(settings.db_path)
    assert settings.db_path == Path.home() / "pitwall.db"


def test_the_catalogue_is_found_without_configuration(tmp_path):
    """search_descriptors must work out of the box: it is the no-quota tool."""
    settings = load_settings({}, dotenv=tmp_path / "x.env")
    assert settings.catalogue_path.is_file()


# --- What is missing -------------------------------------------------------


def test_missing_for_api_names_both_blockers(tmp_path):
    """The user needs to know everything that is missing, not just the first.

    The token file is pinned to a temporary path on purpose: pointing at the
    default location would make this test pass or fail depending on whether
    whoever runs it happens to have logged in on that machine.
    """
    settings = load_settings(
        {"PITWALL_TOKEN_FILE": str(tmp_path / "tokens.json")},
        dotenv=tmp_path / "x.env",
    )
    missing = settings.missing_for_api()

    assert "PITWALL_CLIENT_ID" in missing
    assert any("tokens" in item for item in missing)
    assert not settings.has_credentials


def test_credentials_count_as_present_only_with_a_token_file(tmp_path):
    """A client id alone authorises nothing."""
    token_file = tmp_path / "tokens.json"
    base = load_settings(
        {"PITWALL_CLIENT_ID": "abc", "PITWALL_TOKEN_FILE": str(token_file)},
        dotenv=tmp_path / "x.env",
    )
    assert not base.has_credentials

    token_file.write_text("{}", encoding="utf-8")
    assert base.has_credentials
    assert base.missing_for_api() == []


# --- TTLs ------------------------------------------------------------------


def test_ttls_match_the_documented_budget():
    """12 h on the container is what keeps normal use at 2 requests a day."""
    assert TTLS["telematicData"].total_seconds() == 12 * 3600
    assert TTLS["tyreDiagnosis"].days == 7
    assert TTLS["basicData"].days == 30
    assert TTLS["mappings"].days == 30


def test_settings_are_frozen(tmp_path):
    """Nothing may quietly raise the cap at runtime."""
    settings = load_settings({}, dotenv=tmp_path / "x.env")
    try:
        settings.daily_quota = 50  # type: ignore[misc]
    except Exception as err:
        assert isinstance(err, AttributeError)
    else:
        raise AssertionError("Settings deberia ser inmutable")


def test_settings_can_be_built_directly(tmp_path):
    """The dataclass is usable without the environment, which tests rely on."""
    settings = Settings(
        client_id=None,
        vin=None,
        container_id=None,
        token_file=tmp_path / "t.json",
        db_path=tmp_path / "p.db",
        daily_quota=20,
        log_level="INFO",
        catalogue_path=tmp_path / "c.json",
    )
    assert settings.daily_quota == 20


# --- Where the .env is looked for ------------------------------------------


def test_the_dotenv_is_found_from_any_working_directory(tmp_path, monkeypatch):
    """An MCP client starts the server with an arbitrary cwd.

    Looking only in the cwd meant a correctly filled .env was ignored in
    silence and the user was told their credentials were missing.
    """
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)

    assert find_dotenv({}) in (None, repo_root / ".env")
    if (repo_root / ".env").is_file():
        assert find_dotenv({}) == repo_root / ".env"


def test_the_working_directory_wins_over_the_repository(tmp_path, monkeypatch):
    """A .env next to where you launched the server is the more specific one."""
    local = tmp_path / ".env"
    local.write_text("PITWALL_CLIENT_ID=local", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert find_dotenv({}) == local


def test_an_explicit_env_file_overrides_the_search(tmp_path, monkeypatch):
    """PITWALL_ENV_FILE is the escape hatch for unusual deployments."""
    elsewhere = tmp_path / "custom" / "mi.env"
    elsewhere.parent.mkdir()
    elsewhere.write_text("PITWALL_CLIENT_ID=explicito", encoding="utf-8")
    (tmp_path / ".env").write_text("PITWALL_CLIENT_ID=local", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    settings = load_settings({"PITWALL_ENV_FILE": str(elsewhere)})

    assert settings.client_id == "explicito"


def test_no_dotenv_anywhere_is_not_an_error(tmp_path, monkeypatch):
    """Configuring everything through the environment is a valid setup."""
    monkeypatch.chdir(tmp_path)
    settings = load_settings({"PITWALL_ENV_FILE": str(tmp_path / "no-existe.env")})
    assert settings.client_id is None


# --- The catalogue lives outside the repository now ------------------------


def test_the_catalogue_is_looked_for_in_the_user_data_directory(tmp_path, monkeypatch):
    """It is no longer redistributed inside the repo, so it has to have a home.

    Order: the copy packaged into the wheel, then the downloaded one next to the
    database, then a checkout's own spec/ directory.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    downloaded = tmp_path / ".local" / "share" / "pitwall-mcp" / "telematic_catalogue.json"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_text("{}", encoding="utf-8")

    assert config._catalogue_path() == downloaded


def test_a_missing_catalogue_says_which_script_downloads_it(tmp_path):
    """Rule 6: an absence explains itself and names the fix."""
    with pytest.raises(PitwallError, match="refresh_catalogue.py"):
        load_catalogue(tmp_path / "no-esta.json")
