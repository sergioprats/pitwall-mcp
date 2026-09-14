"""Create (or inspect) the `pitwall-maintenance` container. Run by hand.

Container creation lives HERE and nowhere else. The MCP server only ever
consumes the `container_id` left in `.env`; it never creates or deletes
anything (CLAUDE.md, rule 2).

    python scripts/bootstrap_containers.py --dry-run    # default, sends nothing
    python scripts/bootstrap_containers.py --list       # 1 request
    python scripts/bootstrap_containers.py --create     # 1 request
    python scripts/bootstrap_containers.py --delete ID  # 1 request

Every real call is logged into `quota_log` before anything else, because BMW
counts container calls against the same 50-per-24 h budget as the rest.

`--dry-run` is the default on purpose: nothing is sent unless you say so.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bmw_cardata import CarDataClient  # noqa: E402
from bmw_cardata.exceptions import CarDataError, CarDataHTTPError  # noqa: E402
from bmw_cardata.models.container import CreateContainerRequest  # noqa: E402

from pitwall_mcp.cardata import errors  # noqa: E402
from pitwall_mcp.cardata.auth import TokenManager  # noqa: E402
from pitwall_mcp.catalogue import load_catalogue  # noqa: E402
from pitwall_mcp.config import load_settings  # noqa: E402
from pitwall_mcp.descriptors import (  # noqa: E402
    CONTAINER_DESCRIPTORS,
    CONTAINER_NAME,
    CONTAINER_PURPOSE,
    TRIAL_DESCRIPTORS,
    TYRE_DIAGNOSIS,
)
from pitwall_mcp.storage.db import Database  # noqa: E402
from pitwall_mcp.storage.quota import QuotaStore  # noqa: E402

ENDPOINT = "containers"


def build_request(include_diagnosis: bool = False) -> CreateContainerRequest:
    """Build the exact payload that would be POSTed: confirmed, then on trial."""
    descriptors = [*CONTAINER_DESCRIPTORS, *TRIAL_DESCRIPTORS]
    if include_diagnosis:
        descriptors.append(TYRE_DIAGNOSIS)
    return CreateContainerRequest(
        name=CONTAINER_NAME,
        purpose=CONTAINER_PURPOSE,
        technical_descriptors=descriptors,
    )


def verify_against_catalogue(settings, request: CreateContainerRequest) -> list[str]:  # noqa: ANN001
    """Return the descriptors that are NOT in the catalogue. Should be empty.

    The catalogue is the only source of truth. A container built from anything
    else would be a rule-5 violation, so this check runs before every send and
    before every dry run.
    """
    catalogue = load_catalogue(settings.catalogue_path)
    return [d for d in request.technical_descriptors if catalogue.get(d) is None]


def print_dry_run(request: CreateContainerRequest, unknown: list[str]) -> int:
    """Print exactly what would be sent, and send nothing."""
    print("MODO SECO (--dry-run): no se envia NADA. Cuota gastada: 0.")
    print()
    print("Peticion que se enviaria:")
    print("  POST https://api-cardata.bmwgroup.com/customers/containers")
    print("  Cabeceras: Authorization: Bearer <access token>, x-version: v1")
    print("  Cuerpo:")
    print(
        json.dumps(request.to_dict(), indent=2, ensure_ascii=False)
    )
    print()
    print(f"Total de descriptores: {len(request.technical_descriptors)}")
    print(
        f"De ellos, {len(TRIAL_DESCRIPTORS)} estan en prueba (combustible, consumo, memoria "
        f"de averias...): existen en el catalogo, pero no se sabe si este coche los "
        f"emite. Ninguna herramienta depende de ellos hasta verlos llegar."
    )
    print(
        "El contenedor actual sigue funcionando: nada cambia hasta que copies el id "
        "nuevo a PITWALL_CONTAINER_ID en el .env. Cuando compruebes que el nuevo "
        "funciona, borra el antiguo con --delete ID (1 peticion)."
    )
    print(
        f"Excluido a proposito: {TYRE_DIAGNOSIS}: tiene endpoint dedicado "
        f"(/smartMaintenanceTyreDiagnosis) y /telematicData no lo devuelve. "
        f"Usa --include-diagnosis si quieres comprobarlo empiricamente."
    )
    print()
    if unknown:
        print("ERROR: estos descriptores NO estan en el catalogo telematico:")
        for descriptor in unknown:
            print(f"  - {descriptor}")
        print("No se enviaria nada. Corrige descriptors.py antes de seguir.")
        return 1
    print(
        f"Verificacion: los {len(request.technical_descriptors)} descriptores existen "
        f"en el catalogo local."
    )
    print()
    print("No se sabe si BMW limita el numero de descriptores por contenedor. Si el POST")
    print("real falla por tamano, hay que partirlo en dos y cada lectura pasara a costar")
    print("2 peticiones en vez de 1: reflejalo en el presupuesto de cuota.")
    print()
    print("Cuando quieras enviarlo de verdad: --create (gasta 1 peticion).")
    return 0


async def _with_client(settings, quota: QuotaStore, endpoint: str, action):  # noqa: ANN001
    """Run one API action, logging it in `quota_log` whatever the outcome."""
    tokens = TokenManager(settings)
    tokens.require_credentials()
    async with CarDataClient(tokens.access_token) as client:
        try:
            result = await action(client)
        except CarDataHTTPError as err:
            quota.record(
                endpoint,
                http_status=err.status,
                error_id=err.error_id,
                note=(err.message or "")[:200],
            )
            raise errors.translate_http_error(err) from err
        except CarDataError as err:
            quota.record(endpoint, note=str(err)[:200])
            raise errors.translate(err) from err
    quota.record(endpoint, http_status=200)
    return result


async def do_list(settings, quota: QuotaStore) -> int:  # noqa: ANN001
    """List the containers on the account. Costs 1 request."""
    listing = await _with_client(settings, quota, ENDPOINT, lambda c: c.list_containers())
    containers = listing.raw_data.get("containers", []) if listing.raw_data else []
    if not containers:
        print("La cuenta no tiene ningun contenedor.")
        return 0
    print(f"{len(containers)} contenedor(es):")
    for container in containers:
        print(
            f"  - {container.get('containerId')}  {container.get('name')!r} "
            f"[{container.get('state')}] creado {container.get('created')}"
        )
    return 0


async def do_create(settings, quota: QuotaStore, request: CreateContainerRequest) -> int:  # noqa: ANN001
    """POST the container. Costs 1 request."""
    details = await _with_client(
        settings, quota, ENDPOINT, lambda c: c.create_container(request)
    )
    container_id = details.container_id
    print("Contenedor creado.")
    print(f"  containerId: {container_id}")
    print(f"  descriptores aceptados: {len(details.technical_descriptors)}")
    missing = set(request.technical_descriptors) - set(details.technical_descriptors)
    if missing:
        print(f"  ATENCION: BMW no acepto {len(missing)} descriptor(es):")
        for descriptor in sorted(missing):
            print(f"    - {descriptor}")
    print()
    print(f"Copia esto al .env:  PITWALL_CONTAINER_ID={container_id}")
    return 0


async def do_delete(settings, quota: QuotaStore, container_id: str) -> int:  # noqa: ANN001
    """DELETE a container. Costs 1 request. Never called by the MCP server."""
    await _with_client(settings, quota, ENDPOINT, lambda c: c.delete_container(container_id))
    print(f"Contenedor {container_id} borrado.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line. Dry run is the default."""
    parser = argparse.ArgumentParser(
        description=(
            "Crea el contenedor pitwall-maintenance. Por defecto no envia nada."
        )
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="muestra la peticion exacta sin enviarla (por defecto)",
    )
    group.add_argument("--create", action="store_true", help="crea el contenedor (1 peticion)")
    group.add_argument("--list", action="store_true", help="lista los contenedores (1 peticion)")
    group.add_argument("--delete", metavar="ID", help="borra un contenedor (1 peticion)")
    parser.add_argument(
        "--include-diagnosis",
        action="store_true",
        help=(
            "incluye vehicle.chassis.axle.wheel.tire.diagnosis, que tiene endpoint propio "
            "y que /telematicData no deberia devolver"
        ),
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)
    settings = load_settings()
    request = build_request(include_diagnosis=args.include_diagnosis)
    unknown = verify_against_catalogue(settings, request)

    if not (args.create or args.list or args.delete):
        return print_dry_run(request, unknown)
    if args.dry_run:
        return print_dry_run(request, unknown)
    if unknown:
        print("ERROR: hay descriptores fuera del catalogo. No se envia nada.", file=sys.stderr)
        return 1

    db = Database(settings.db_path)
    quota = QuotaStore(db, daily_limit=settings.daily_quota)
    try:
        status = quota.check()
    except Exception as err:  # QuotaExceededError
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(f"Cuota antes de empezar: {status.used}/{status.limit} gastadas hoy.")

    try:
        if args.list:
            return await do_list(settings, quota)
        if args.create:
            return await do_create(settings, quota, request)
        if args.delete:
            return await do_delete(settings, quota, args.delete)
    except errors.PitwallError as err:
        print(f"ERROR: {err.message}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
