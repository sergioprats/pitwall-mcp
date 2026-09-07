"""Tyre diagnosis.

Verified on 2026-09-07: this vehicle answers `/smartMaintenanceTyreDiagnosis`
with a COMPLETE BUT EMPTY skeleton. Every node is there with its label, and
every value is a placeholder: `dimension` all zeros, `tyreWear.dueMileage` 0,
`runFlat` false, and no season, tread, part number, mounting or production date.
`errors` came back as an empty list, so BMW is not reporting a failure either.

That zero is the whole reason this module is careful. Printing
`tyreWear.dueMileage: 0` as data would tell the user their tyres are due for
replacement RIGHT NOW. It is not a measurement; it is an unfilled field.
"""

from __future__ import annotations

from typing import Any

from ..cardata.client import CarDataAdapter
from ..config import Settings
from .pending import require_ready

WHEELS: tuple[tuple[str, str], ...] = (
    ("frontLeft", "Delantera izquierda"),
    ("frontRight", "Delantera derecha"),
    ("rearLeft", "Trasera izquierda"),
    ("rearRight", "Trasera derecha"),
)

#: Nodes carrying a value alongside their label, and the key that holds it.
VALUE_KEYS: dict[str, str] = {
    "season": "season",
    "tread": "treadDesign",
    "partNumber": "partNumber",
    "mountingDate": "mountingDate",
    "tyreProductionDate": "value",
    "qualityStatus": "qualityStatus",
    "optimizedForOem": "optimizedForOem",
    "tyreDefect": "value",
}


def _meaningful(value: Any) -> bool:
    """True when a value is real data rather than an unfilled placeholder.

    Zero and false are placeholders here, not measurements: a rim diameter of
    0 inches and a service due in 0 km are both impossible.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip() not in ("", "-", "null")
    return bool(value)


def wheel_has_data(wheel: Any) -> bool:
    """True when a wheel node carries at least one real value."""
    if not isinstance(wheel, dict):
        return False
    for key, node in wheel.items():
        if key == "label" or not isinstance(node, dict):
            continue
        for field, value in node.items():
            if field == "label":
                continue
            if _meaningful(value):
                return True
    return False


def describe_wheel(wheel: Any, label: str) -> list[str]:
    """Render one wheel, or say plainly that it carries nothing."""
    if not wheel_has_data(wheel):
        return [f"  {label}: sin datos (BMW devuelve la estructura vacia)"]

    lines = [f"  {label}:"]
    dimension = wheel.get("dimension") or {}
    width, ratio, rim = (
        dimension.get("sectionWidth"),
        dimension.get("aspectRatio"),
        dimension.get("rimDiameter"),
    )
    if all(_meaningful(v) for v in (width, ratio, rim)):
        load = dimension.get("loadIndex")
        medida = f"{width}/{ratio} R{rim}"
        if _meaningful(load):
            medida += f" {load}"
        lines.append(f"    Medida: {medida}")

    wear = wheel.get("tyreWear") or {}
    due = wear.get("dueMileage")
    if _meaningful(due):
        lines.append(f"    Cambio previsto en: {due} {wear.get('unit') or 'km'}")
    status = wear.get("status") or wear.get("value")
    if _meaningful(status):
        lines.append(f"    Desgaste: {status}")

    for node_name, value_key in VALUE_KEYS.items():
        node = wheel.get(node_name) or {}
        value = node.get(value_key) or node.get("value")
        if _meaningful(value):
            lines.append(f"    {node.get('label', node_name)}: {value}")

    run_flat = (wheel.get("runFlat") or {}).get("runFlat")
    if run_flat:
        lines.append("    Runflat: si")
    return lines


async def get_tyre_diagnosis(adapter: CarDataAdapter, settings: Settings) -> str:
    """`GET /customers/vehicles/{vin}/smartMaintenanceTyreDiagnosis`."""
    require_ready(settings, needs_vin=True)
    result = await adapter.get_tyre_diagnosis(settings.vin)
    payload = result.payload if isinstance(result.payload, dict) else {}

    lines = ["DIAGNOSTICO DE NEUMATICOS", ""]
    lines.append(
        "Este endpoint NO devuelve presiones. Da desgaste, defectos, dimensiones y "
        "fechas. Para las presiones usa get_maintenance_summary, que las lee del "
        "contenedor telematico: son dos fuentes distintas."
    )

    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        lines.append("")
        lines.append(f"BMW ha devuelto {len(errors)} error(es) en el diagnostico:")
        for error in errors:
            if isinstance(error, dict):
                lines.append(f"  - {error.get('type')}: {error.get('message')}")

    mounted = ((payload.get("passengerCar") or {}).get("mountedTyres")) or {}
    wheels = {key: mounted.get(key) for key, _ in WHEELS}
    with_data = [key for key, wheel in wheels.items() if wheel_has_data(wheel)]

    lines.append("")
    if not with_data:
        lines.extend(
            [
                "SIN DATOS: BMW responde con la estructura completa pero todos los campos "
                "vacios. Las dimensiones vienen a 0, el desgaste a 0 y no hay temporada, "
                "dibujo, fabricante ni fechas.",
                "",
                "Un 0 aqui NO significa 'cero kilometros para el cambio': es un campo sin "
                "rellenar. Este servidor no lo presenta como medida.",
                "",
                "Causa probable: el diagnostico de neumaticos requiere que el taller o el "
                "propio vehiculo hayan registrado los neumaticos montados, y en este U11 "
                "no consta. La lista 'errors' vino vacia, asi que BMW tampoco reporta un "
                "fallo.",
                "",
                "Lo que si funciona son las presiones, por el contenedor telematico.",
            ]
        )
    else:
        for key, label in WHEELS:
            lines.extend(describe_wheel(wheels.get(key), label))

    lines.append("")
    lines.append(result.provenance(source_timestamp=result.fetched_at))
    return "\n".join(lines)
