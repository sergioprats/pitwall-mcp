"""Re-download the telematic catalogue and report what changed.

    python scripts/refresh_catalogue.py [--check]

Downloads from GitHub, not from BMW: it does not touch the CarData API and
costs no quota. `--check` compares without writing, so it can run in CI.

If the catalogue ever drops a descriptor this project relies on, that is a
finding, not a detail: the tools built on it must stop claiming it exists.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitwall_mcp.descriptors import ALL_CONFIRMED_DESCRIPTORS  # noqa: E402

CATALOGUE_URL = (
    "https://raw.githubusercontent.com/zweckj/bmw-cardata/main/spec/telematic_catalogue.json"
)
#: Default destination: the checkout's own spec/, which is gitignored. The
#: catalogue is not redistributed inside this repository (see spec/README.md).
TARGET = Path(__file__).resolve().parents[1] / "spec" / "telematic_catalogue.json"


def descriptors_of(payload: dict) -> set[str]:
    """Return every technical descriptor in a catalogue payload."""
    return {
        entry.get("technical_descriptor", "")
        for category in payload.get("categories", [])
        for entry in category.get("entries", [])
    }


def summarise(payload: dict) -> str:
    """One line per category, with its descriptor count."""
    lines = []
    total = 0
    for category in payload.get("categories", []):
        count = len(category.get("entries", []))
        total += count
        lines.append(f"  {count:4d}  {category.get('category')}")
    lines.append(f"  {total:4d}  TOTAL")
    return "\n".join(lines)


def resolve_target(out: str | None) -> Path:
    """Where to write the catalogue: --out if given, the checkout's spec/ otherwise."""
    return Path(out).expanduser() if out else TARGET


def main(argv: list[str] | None = None) -> int:
    """Download the catalogue and report (or apply) the difference."""
    parser = argparse.ArgumentParser(description="Actualiza el catalogo telematico local.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="solo compara y devuelve codigo 1 si hay cambios; no escribe nada",
    )
    parser.add_argument(
        "--out",
        metavar="RUTA",
        default=None,
        help=f"donde escribirlo (por defecto: {TARGET})",
    )
    args = parser.parse_args(argv)
    target = resolve_target(args.out)

    print(f"Descargando {CATALOGUE_URL}")
    with urllib.request.urlopen(CATALOGUE_URL, timeout=30) as response:  # noqa: S310
        raw = response.read().decode("utf-8")
    remote = json.loads(raw)

    local = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}

    remote_descriptors = descriptors_of(remote)
    local_descriptors = descriptors_of(local)

    print()
    print("Catalogo remoto:")
    print(summarise(remote))

    added = sorted(remote_descriptors - local_descriptors)
    removed = sorted(local_descriptors - remote_descriptors)

    if not added and not removed:
        print("\nSin cambios respecto al catalogo local.")
        return 0

    print(f"\nDescriptores nuevos: {len(added)}")
    for descriptor in added[:40]:
        print(f"  + {descriptor}")
    print(f"Descriptores eliminados: {len(removed)}")
    for descriptor in removed[:40]:
        print(f"  - {descriptor}")

    lost = [d for d in ALL_CONFIRMED_DESCRIPTORS if d not in remote_descriptors]
    if lost:
        print("\nATENCION: el catalogo nuevo ya NO contiene descriptores en los que se")
        print("apoya este proyecto. Hay que revisar descriptors.py y las herramientas:")
        for descriptor in lost:
            print(f"  ! {descriptor}")

    if args.check:
        print("\n--check: no se ha escrito nada.")
        return 1

    TARGET.write_text(raw, encoding="utf-8")
    print(f"\nEscrito {TARGET}")
    print("Recuerda actualizar docs/paso-0-descriptores.md con la fecha y el resumen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
