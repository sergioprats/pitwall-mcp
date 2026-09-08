"""Vehicle identity tools: mappings and basic data.

Written against real answers from this vehicle (2026-09-07). Two findings shape
this module, and neither is what the swagger led us to expect:

* `/basicData` does NOT return `puStep` for this U11, nor `vin`, nor
  `isTelematicsCapable`, nor `modelRange`. It does return `seriesDevt`,
  `colourDescription` and `countryCode`, which the swagger does not document.
* Since `puStep` never arrives, `report_product_update_step` has nothing to
  report. It says so instead of inventing a substitute.
"""

from __future__ import annotations

from typing import Any

from ..cardata.client import CarDataAdapter
from ..config import Settings
from ..formatting import format_moment
from .readiness import require_ready

#: Spanish labels for the fields this vehicle actually returns. Anything not
#: listed is shown with BMW's own key, never dropped and never renamed.
BASIC_LABELS: dict[str, str] = {
    "brand": "Marca",
    "modelName": "Modelo",
    "series": "Serie",
    "seriesDevt": "Serie de desarrollo",
    "modelKey": "Clave de modelo",
    "bodyType": "Carroceria",
    "engine": "Motor",
    "driveTrain": "Traccion",
    "propulsionType": "Propulsion",
    "numberOfDoors": "Puertas",
    "constructionDate": "Fecha de fabricacion",
    "countryCode": "Pais",
    "steering": "Volante",
    "headUnit": "Unidad principal",
    "hasNavi": "Navegador",
    "hasSunRoof": "Techo solar",
    "colourDescription": "Color",
    "colourCodeRaw": "Codigo de color",
    "simStatus": "Estado de la SIM",
    "fullSAList": "Equipamiento opcional (SA)",
}

#: Fields the swagger documents but this vehicle does not return.
NOT_RETURNED_BY_THIS_VEHICLE = ("vin", "puStep", "isTelematicsCapable", "modelRange")


async def list_vehicles(adapter: CarDataAdapter, settings: Settings) -> str:
    """`GET /customers/vehicles/mappings`: VIN, mappedSince and mappingType."""
    require_ready(settings)
    result = await adapter.list_vehicles()
    payload = result.payload
    items = payload if isinstance(payload, list) else [payload]

    lines = [f"Vehiculos mapeados a la cuenta: {len(items)}", ""]
    for item in items:
        if not isinstance(item, dict):
            continue
        mapping = item.get("mappingType")
        lines.append(f"  VIN {item.get('vin')}  [{mapping}]")
        lines.append(f"    mapeado desde: {format_moment(item.get('mappedSince'))}")
        if mapping != "PRIMARY":
            lines.append(
                "    ATENCION: CarData exige ser usuario PRIMARY para leer datos de este "
                "vehiculo. Si necesitas cambiarlo, se hace en la app My BMW; no hay forma "
                "de hacerlo por API."
            )
    lines.append("")
    lines.append(result.provenance(source_timestamp=result.fetched_at))
    return "\n".join(lines)


def render_basic_data(payload: Any) -> list[str]:
    """Render the basic-data fields, listing what BMW did not send."""
    if not isinstance(payload, dict):
        return ["La respuesta no tiene el formato esperado."]

    lines: list[str] = []
    for key, label in BASIC_LABELS.items():
        if key not in payload:
            continue
        value = payload[key]
        if key == "constructionDate":
            lines.append(f"  {label}: {format_moment(value)}")
        elif isinstance(value, bool):
            lines.append(f"  {label}: {'si' if value else 'no'}")
        elif key == "fullSAList" and isinstance(value, str):
            codes = [code for code in value.split(",") if code]
            lines.append(f"  {label}: {len(codes)} codigos")
            lines.append(f"    {value}")
        else:
            lines.append(f"  {label}: {value}")

    unknown = [k for k in payload if k not in BASIC_LABELS]
    if unknown:
        lines.append("")
        lines.append("Campos que BMW devuelve y este proyecto no etiqueta:")
        lines.extend(f"  {key}: {payload[key]!r}" for key in sorted(unknown))

    absent = [key for key in NOT_RETURNED_BY_THIS_VEHICLE if key not in payload]
    if absent:
        lines.append("")
        lines.append(
            "Campos que el swagger documenta y este vehiculo NO devuelve: "
            + ", ".join(absent)
            + "."
        )
    return lines


async def get_vehicle_basic_data(adapter: CarDataAdapter, settings: Settings) -> str:
    """`GET /customers/vehicles/{vin}/basicData`. Cached for 30 days."""
    require_ready(settings, needs_vin=True)
    result = await adapter.get_basic_data(settings.vin)

    lines = ["DATOS BASICOS DEL VEHICULO", ""]
    lines.extend(render_basic_data(result.payload))
    lines.append("")
    lines.append(result.provenance(source_timestamp=result.fetched_at))
    return "\n".join(lines)


async def report_product_update_step(adapter: CarDataAdapter, settings: Settings) -> str:
    """Report `puStep`, or explain that this vehicle does not return it.

    Verified on 2026-09-07: `/basicData` came back without `puStep` for this
    U11. There is nothing to report and nothing to track, so the honest answer
    is to say so rather than to substitute another field for it.
    """
    require_ready(settings, needs_vin=True)
    result = await adapter.get_basic_data(settings.vin)
    payload = result.payload if isinstance(result.payload, dict) else {}
    pu_step = payload.get("puStep")

    lines = ["PASO DE ACTUALIZACION DE PRODUCTO (puStep)", ""]

    if pu_step is None:
        lines.extend(
            [
                "NO DISPONIBLE: /basicData no devuelve 'puStep' para este vehiculo.",
                "",
                "Que significa, exactamente:",
                "  - No es un fallo de configuracion ni de este servidor. El endpoint "
                "responde correctamente y sencillamente no incluye ese campo.",
                "  - puStep TAMPOCO era la version de software del vehiculo. Ese "
                "descriptor no existe en el catalogo de BMW: ni iStep, ni version, ni "
                "estado de Remote Software Upgrade.",
                "  - Por tanto no hay ningun dato, ni en la REST ni en el catalogo "
                "telematico, que permita saber que software lleva el coche ni si le "
                "toca una actualizacion.",
                "",
                "Consecuencia para diagnose_software_update: se queda sin la unica pista "
                "indirecta que le quedaba sobre actualizaciones. Solo puede razonar sobre "
                "la bateria de 12V, y con las limitaciones que ya declara.",
            ]
        )
    else:
        lines.extend(
            [
                f"puStep: {pu_step}",
                "",
                "OJO: puStep es el PASO DE ACTUALIZACION DE PRODUCTO. NO es la version de "
                "software del vehiculo; ese descriptor no existe en el catalogo de BMW. "
                "No lo presentes como equivalente.",
                "Y no se guarda en el historico local: la tabla readings solo recibe "
                "respuestas de /telematicData, asi que no hay serie de puStep con la "
                "que detectar un cambio.",
            ]
        )

    lines.append("")
    lines.append(result.provenance(source_timestamp=result.fetched_at))
    return "\n".join(lines)
