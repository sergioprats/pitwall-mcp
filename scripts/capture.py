"""Read one endpoint, once, and keep the raw answer. Run by hand.

This is the instrument for block B: the MCP tools are still skeletons, so this
is what performs the first real calls and turns them into recorded fixtures.
After that the fixtures answer the questions and no further quota is spent.

    python scripts/capture.py --status      # 0 requests: quota and cache only
    python scripts/capture.py --mappings    # 1 request (gives you the VIN)
    python scripts/capture.py --basic       # 1 request
    python scripts/capture.py --telematic   # 1 request
    python scripts/capture.py --tyres       # 1 request

Everything goes through `CarDataAdapter`, so the cache and the quota counter
apply exactly as they do for the MCP server: repeating a capture inside the TTL
costs nothing and simply re-serves the stored payload.

Raw captures land in `captures/`, which `.gitignore` already covers because
they contain the VIN. `--fixture NAME` also writes an anonymised copy into
`tests/fixtures/`, safe to commit.

READ ONLY. Nothing here writes to the vehicle, and nothing creates or deletes
containers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pitwall_mcp.cardata import errors  # noqa: E402
from pitwall_mcp.cardata.client import (  # noqa: E402
    ENDPOINT_BASIC_DATA,
    ENDPOINT_MAPPINGS,
    ENDPOINT_TELEMATIC,
    ENDPOINT_TYRE_DIAGNOSIS,
    CarDataAdapter,
)
from pitwall_mcp.catalogue import load_catalogue  # noqa: E402
from pitwall_mcp.config import load_settings  # noqa: E402
from pitwall_mcp.descriptors import CONTAINER_DESCRIPTORS  # noqa: E402
from pitwall_mcp.formatting import explain_missing, format_moment  # noqa: E402
from pitwall_mcp.storage.db import Database, to_iso, utc_now  # noqa: E402

#: The placeholder every committed fixture uses instead of a real VIN.
FIXTURE_VIN = "WBAU11030P0FAKE01"
FIXTURE_GCID = "gcid-de-prueba"
#: Forma real observada: 13 caracteres alfanumericos, no un UUID.
FIXTURE_CONTAINER = "B00FAKE0CONT1"


# --- Anonymisation ---------------------------------------------------------


def anonymise(payload: Any, replacements: dict[str, str]) -> Any:
    """Return a copy of `payload` with every secret string replaced.

    Walks the whole structure, keys included: a VIN can appear as an object key
    as easily as a value, and one missed occurrence would commit it.
    """
    if isinstance(payload, dict):
        return {
            anonymise(key, replacements): anonymise(value, replacements)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [anonymise(item, replacements) for item in payload]
    if isinstance(payload, str):
        result = payload
        for secret, placeholder in replacements.items():
            if secret:
                result = result.replace(secret, placeholder)
        return result
    return payload


def build_replacements(settings, payload: Any) -> dict[str, str]:  # noqa: ANN001
    """Collect what must never reach the repository: VIN, gcid, container id."""
    replacements: dict[str, str] = {}
    if settings.vin:
        replacements[settings.vin] = FIXTURE_VIN
    if settings.container_id:
        replacements[settings.container_id] = FIXTURE_CONTAINER

    # A mappings response carries VINs we may not have configured yet.
    for vin in _vins_in(payload):
        replacements.setdefault(vin, FIXTURE_VIN)
    return replacements


def _vins_in(payload: Any) -> list[str]:
    """Find values stored under a `vin` key anywhere in the payload."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "vin" and isinstance(value, str) and value:
                found.append(value)
            else:
                found.extend(_vins_in(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_vins_in(item))
    return found


# --- Reporting -------------------------------------------------------------


def report_telematic(payload: Any, settings) -> None:  # noqa: ANN001
    """Say which of the requested descriptors actually came back, and which not.

    This is the question block A could not answer: the catalogue says a
    descriptor exists, but only a real call proves this U11 emits it.
    """
    if not isinstance(payload, dict):
        print("Respuesta inesperada: no es un objeto JSON.")
        return
    entries = payload.get("telematicData")
    if not isinstance(entries, dict):
        print("La respuesta no trae 'telematicData'.")
        return

    catalogue = load_catalogue(settings.catalogue_path)
    arrived = [d for d in CONTAINER_DESCRIPTORS if d in entries]
    missing = [d for d in CONTAINER_DESCRIPTORS if d not in entries]
    extra = [d for d in entries if d not in CONTAINER_DESCRIPTORS]

    print()
    print(f"Descriptores pedidos : {len(CONTAINER_DESCRIPTORS)}")
    print(f"Descriptores llegados: {len(arrived)}")
    print(f"Descriptores ausentes: {len(missing)}")
    if extra:
        print(f"Descriptores NO pedidos que ha devuelto BMW: {len(extra)}")
        for descriptor in extra:
            print(f"  ? {descriptor}")

    print()
    print("LLEGADOS:")
    for descriptor in arrived:
        entry = entries[descriptor] if isinstance(entries[descriptor], dict) else {}
        value = entry.get("value")
        unit = entry.get("unit") or ""
        stamp = entry.get("timestamp")
        shown = value if not isinstance(value, str) or len(value) <= 60 else value[:57] + "..."
        print(f"  + {descriptor}")
        print(f"      valor: {shown!r} {unit}".rstrip())
        print(f"      fecha del dato: {format_moment(stamp)}")

    if missing:
        print()
        print("AUSENTES (no los emite este vehiculo, o no hay lectura reciente):")
        for descriptor in missing:
            print(f"  - {descriptor}")
        print()
        print("Explicacion para el primero, tal y como la daria una herramienta:")
        print(
            "  "
            + explain_missing(
                missing[0],
                in_catalogue=catalogue.get(missing[0]) is not None,
                in_container=True,
                container_id=settings.container_id,
            )
        )

    # The open risk of the whole project.
    cbs = entries.get("vehicle.status.conditionBasedServices")
    print()
    print("=" * 70)
    print("RIESGO Nº 1: estructura de vehicle.status.conditionBasedServices")
    print("=" * 70)
    if cbs is None:
        print("NO ha llegado. Confirmaria la sospecha de que esta ligado a un endpoint")
        print("dedicado y /telematicData no lo devuelve. El desglose CBS por partida no")
        print("seria posible por esta via, y get_vehicle_status tendria que quedarse con")
        print("vehicle.status.serviceDistance.next.")
    else:
        print("SI ha llegado. Contenido crudo, sin interpretar:")
        print(json.dumps(cbs, indent=2, ensure_ascii=False))


def report_mappings(payload: Any) -> None:
    """List the VINs and, crucially, whether we are PRIMARY."""
    items = payload if isinstance(payload, list) else [payload]
    print()
    for item in items:
        if not isinstance(item, dict):
            continue
        vin = item.get("vin")
        mapping = item.get("mappingType")
        print(f"  VIN {vin}  [{mapping}]  mapeado desde {item.get('mappedSince')}")
        if mapping != "PRIMARY":
            print("    ATENCION: CarData exige ser usuario PRIMARY para leer datos.")
    print()
    print("Copia el VIN a PITWALL_VIN en el .env para las siguientes capturas.")


# --- Main ------------------------------------------------------------------

ACTIONS = {
    "mappings": ENDPOINT_MAPPINGS,
    "basic": ENDPOINT_BASIC_DATA,
    "telematic": ENDPOINT_TELEMATIC,
    "tyres": ENDPOINT_TYRE_DIAGNOSIS,
}


def print_quota(adapter: CarDataAdapter, label: str) -> None:
    """Show the quota counter around a capture."""
    status = adapter.quota.status()
    print(f"{label}: {status.used}/{status.limit} gastadas hoy (limite BMW {status.bmw_limit}).")


async def run(args: argparse.Namespace) -> int:
    """Perform the requested capture."""
    settings = load_settings()
    db = Database(settings.db_path)
    adapter = CarDataAdapter(settings, db=db)

    try:
        if args.status:
            from pitwall_mcp.tools.quota_tools import get_api_quota

            print(get_api_quota(adapter, settings))
            return 0

        action = next((name for name in ACTIONS if getattr(args, name)), None)
        if action is None:
            print(
                "Elige que capturar: --status (0 peticiones), --mappings, --basic, "
                "--telematic o --tyres.",
                file=sys.stderr,
            )
            return 2

        print_quota(adapter, "Cuota antes")
        warning = adapter.tokens.refresh_warning()
        if warning:
            print(warning)

        if action == "mappings":
            result = await adapter.list_vehicles()
        elif action == "basic":
            result = await adapter.get_basic_data(_require_vin(settings))
        elif action == "telematic":
            result = await adapter.get_telematic_data(
                _require_vin(settings), _require_container(settings)
            )
        else:
            result = await adapter.get_tyre_diagnosis(_require_vin(settings))

        print()
        print(f"Procedencia: {result.source.upper()}")
        print(f"  leido   : {format_moment(result.fetched_at)}")
        print(f"  caduca  : {format_moment(result.expires_at)}")
        if result.source == "cache":
            print("  (servido de cache: esta captura NO ha gastado cuota)")
        print_quota(adapter, "Cuota despues")

        out_dir = REPO_ROOT / args.out
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = to_iso(utc_now()).replace(":", "").replace("-", "")
        raw_path = out_dir / f"{action}-{stamp}.json"
        raw_path.write_text(
            json.dumps(result.payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print()
        print(f"Respuesta cruda guardada en: {raw_path}")
        print("  (contiene el VIN: 'captures/' esta en .gitignore)")

        if action == "mappings":
            report_mappings(result.payload)
        elif action == "telematic":
            report_telematic(result.payload, settings)

        if args.fixture:
            replacements = build_replacements(settings, result.payload)
            clean = anonymise(result.payload, replacements)
            fixture_path = REPO_ROOT / "tests" / "fixtures" / f"{args.fixture}.json"
            fixture_path.write_text(
                json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print()
            print(f"Fixture anonimizado escrito en: {fixture_path}")
            for secret, placeholder in replacements.items():
                print(f"  sustituido {secret[:4]}...{secret[-4:]} -> {placeholder}")
            print("  REVISALO antes de commitear.")
        return 0

    except errors.PitwallError as err:
        print(f"ERROR: {err.message}", file=sys.stderr)
        return 1
    finally:
        db.close()


def _require_vin(settings) -> str:  # noqa: ANN001
    """Return the configured VIN or explain how to get one."""
    if not settings.vin:
        raise errors.missing_vin()
    return settings.vin


def _require_container(settings) -> str:  # noqa: ANN001
    """Return the configured container id or explain how to create one."""
    if not settings.container_id:
        raise errors.missing_container()
    return settings.container_id


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line. No endpoint flag means no request."""
    parser = argparse.ArgumentParser(
        description="Captura una respuesta real de CarData y la guarda para fixtures."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="cuota y cache; 0 peticiones")
    group.add_argument("--mappings", action="store_true", help="GET /mappings (1 peticion)")
    group.add_argument("--basic", action="store_true", help="GET /basicData (1 peticion)")
    group.add_argument(
        "--telematic", action="store_true", help="GET /telematicData (1 peticion)"
    )
    group.add_argument(
        "--tyres", action="store_true", help="GET /smartMaintenanceTyreDiagnosis (1 peticion)"
    )
    parser.add_argument(
        "--fixture",
        metavar="NOMBRE",
        help="ademas, escribe una copia anonimizada en tests/fixtures/NOMBRE.json",
    )
    parser.add_argument(
        "--out", default="captures", help="directorio de capturas crudas (por defecto: captures)"
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
