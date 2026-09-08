"""Download the telematic catalogue into the installed package's data directory.

This repository does not redistribute BMW's documents, so the catalogue has to
be fetched once. `scripts/refresh_catalogue.py` covers a checkout; this module
covers everybody else, because someone who ran `pip install pitwall-mcp` has no
`scripts/` directory. It is reached as `pitwall-mcp --fetch-catalogue`.

TWO THINGS IT MUST NOT DO. It must not spend CarData quota: the download comes
from GitHub, from `zweckj/bmw-cardata` under MIT, and never touches
`api-cardata.bmwgroup.com`. And it must not overwrite a working catalogue with a
bad answer: a captive portal or a proxy replies 200 with HTML, and writing that
over a good file would break a working install to fix nothing.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from .cardata import errors
from .config import _default_catalogue_path

#: Where the catalogue comes from. GitHub, not BMW: this costs no quota.
CATALOGUE_URL: Final = (
    "https://raw.githubusercontent.com/zweckj/bmw-cardata/main/spec/telematic_catalogue.json"
)

#: How long to wait for the download before giving up.
TIMEOUT_SECONDS: Final = 30

Opener = Callable[[str], str]


def download(url: str = CATALOGUE_URL) -> str:
    """Fetch a URL and return its body as text."""
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        return response.read().decode("utf-8")


def looks_like_a_catalogue(payload: Any) -> bool:
    """True only for something that can actually answer a descriptor question.

    `{"categories": []}` parses perfectly and is useless, so an empty catalogue
    counts as a bad answer rather than as an unusual one.
    """
    if not isinstance(payload, dict):
        return False
    categories = payload.get("categories")
    return isinstance(categories, list) and len(categories) > 0


def fetch_catalogue(target: Path | None = None, *, opener: Opener | None = None) -> Path:
    """Download the catalogue to `target` and return where it landed.

    Defaults to the location the server looks in for an installed copy. Raises
    a Spanish `PitwallError` rather than writing anything when the answer is not
    a catalogue, so an existing good file survives a bad download.
    """
    path = Path(target) if target is not None else _default_catalogue_path()
    body = (opener or download)(CATALOGUE_URL)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = None

    if not looks_like_a_catalogue(payload):
        raise errors.bad_catalogue_download(CATALOGUE_URL, path)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def describe(path: Path) -> str:
    """One line per category with its descriptor count, plus the total."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    lines = []
    total = 0
    for category in payload.get("categories", []):
        count = len(category.get("entries", []))
        total += count
        lines.append(f"  {count:4d}  {category.get('category')}")
    lines.append(f"  {total:4d}  TOTAL")
    return "\n".join(lines)
