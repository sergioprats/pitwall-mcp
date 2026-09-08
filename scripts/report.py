"""Build the local HTML report from the reading history. Run by hand.

    python scripts/report.py              # captures/report-*.html, VIN enmascarado
    python scripts/report.py --full-vin   # con el VIN entero, para ti
    python scripts/report.py --out ruta   # otro directorio de salida

ZERO REQUESTS. This reads the SQLite history and nothing else: no token is
used, no endpoint is called, and the daily quota is untouched. It is safe to
run as often as you like.

READ ONLY. Nothing here writes to the vehicle, and nothing creates or deletes
containers.

The output lands in `captures/` by default, which `.gitignore` already covers,
and the VIN is masked unless you ask for it: the page is meant to be screenshot
and shared without a second thought.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pitwall_mcp.cardata import errors  # noqa: E402
from pitwall_mcp.config import Settings, load_settings  # noqa: E402
from pitwall_mcp.report import mask_vin, render_report  # noqa: E402
from pitwall_mcp.storage.db import Database, utc_now  # noqa: E402
from pitwall_mcp.storage.history import HistoryStore  # noqa: E402

#: Reports carry vehicle data, so they default to the ignored capture directory.
DEFAULT_OUTPUT_DIR = REPO_ROOT / "captures"


def generate(
    settings: Settings,
    *,
    out_dir: Path | None = None,
    now: datetime | None = None,
    full_vin: bool = False,
) -> Path:
    """Render the report for the configured VIN and return the file written."""
    if not settings.vin:
        raise errors.missing_vin()

    moment = now or utc_now()
    with Database(settings.db_path) as database:
        html = render_report(HistoryStore(database), settings.vin, now=moment, full_vin=full_vin)

    directory = Path(out_dir) if out_dir is not None else DEFAULT_OUTPUT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = moment.strftime("%Y%m%d-%H%M")
    path = directory / f"report-{mask_vin(settings.vin)[-4:]}-{stamp}.html"
    path.write_text(html, encoding="utf-8")
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        description="Genera el informe HTML del historico local. No gasta ninguna peticion."
    )
    parser.add_argument(
        "--out",
        default=None,
        help=f"directorio de salida (por defecto: {DEFAULT_OUTPUT_DIR.name})",
    )
    parser.add_argument(
        "--full-vin",
        action="store_true",
        help="muestra el VIN completo en la pagina (por defecto va enmascarado)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Generate the report and print where it landed."""
    args = parse_args(argv)
    settings = load_settings()
    try:
        path = generate(
            settings,
            out_dir=Path(args.out) if args.out else None,
            full_vin=args.full_vin,
        )
    except errors.CarDataError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Informe escrito en {path}")
    print("Ninguna peticion a la API. Abrelo en el navegador.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
