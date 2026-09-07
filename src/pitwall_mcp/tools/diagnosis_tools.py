"""Composite tools: maintenance summary and the software-update diagnosis."""

from __future__ import annotations

from ..cardata.client import CarDataAdapter
from ..catalogue import Catalogue
from ..config import Settings
from ..descriptors import (
    BATTERY_SERVICE_RECHARGE,
    BATTERY_SERVICE_REPLACE,
    BATTERY_VOLTAGE,
    CBS_COUNT,
    INSPECTION_DATE_LEGAL,
    SERVICE_DISTANCE_NEXT,
    SERVICE_DISTANCE_YELLOW,
    TRAVELLED_DISTANCE,
    WHEEL_POSITIONS,
    tyre_descriptor,
)
from ..formatting import (
    build_pressure,
    format_battery_recharge,
    format_battery_replace,
    format_moment,
)
from ..telematic import TelematicSnapshot, parse_cbs
from .pending import pending, require_ready
from .telematic_tools import render_cbs


async def get_maintenance_summary(
    adapter: CarDataAdapter, settings: Settings, catalogue: Catalogue
) -> str:
    """One pasteable block: mileage, CBS, pressures and the oldest datum used.

    Everything comes from a single container read, so this costs at most one
    request and usually zero: inside the 12 h TTL it is served from cache.
    """
    require_ready(settings, needs_vin=True, needs_container=True)
    result = await adapter.get_telematic_data(settings.vin, settings.container_id)
    snapshot = TelematicSnapshot.from_payload(result.payload)

    lines = ["RESUMEN DE MANTENIMIENTO", ""]

    mileage = snapshot.get(TRAVELLED_DISTANCE)
    if mileage.has_value:
        km = mileage.as_int()
        shown = f"{km:,}".replace(",", ".") if km is not None else mileage.value
        lines.append(f"Kilometraje: {shown} km")
    else:
        lines.append("Kilometraje: sin lectura en esta respuesta.")

    next_service = snapshot.get(SERVICE_DISTANCE_NEXT)
    yellow = snapshot.get(SERVICE_DISTANCE_YELLOW)
    if next_service.has_value:
        remaining = next_service.as_int()
        lines.append(f"Proximo servicio en: {remaining} km")
        threshold = yellow.as_int()
        if remaining is not None and threshold is not None and remaining <= threshold:
            lines.append(f"  Por debajo del umbral de preaviso ({threshold} km).")

    inspection = snapshot.get(INSPECTION_DATE_LEGAL)
    if inspection.has_value:
        lines.append(f"Proxima inspeccion legal (ITV): {inspection.value}")

    lines.append("")
    lines.extend(render_cbs(parse_cbs(snapshot, reported_count=snapshot.get(CBS_COUNT).as_int())))

    lines.append("")
    lines.append("Presiones de neumaticos:")
    for row, side, label in WHEEL_POSITIONS:
        pressure = snapshot.get(tyre_descriptor(row, side, "pressure"))
        target = snapshot.get(tyre_descriptor(row, side, "pressureTarget"))
        reading = build_pressure(
            label,
            pressure.value if pressure.has_value else None,
            target.value if target.has_value else None,
        )
        lines.append(f"  {reading.describe()}")

    lines.append("")
    lines.append("Bateria de 12V:")
    voltage = snapshot.get(BATTERY_VOLTAGE)
    if voltage.has_value:
        lines.append(f"  Voltaje: {voltage.value} V   ({format_moment(voltage.moment)})")
    else:
        lines.append("  Voltaje: sin lectura en esta respuesta.")
    replace = snapshot.get(BATTERY_SERVICE_REPLACE)
    recharge = snapshot.get(BATTERY_SERVICE_RECHARGE)
    lines.append(f"  Salud: {format_battery_replace(replace.value if replace.has_value else None)}")
    lines.append(
        f"  Pide recarga: {format_battery_recharge(recharge.value if recharge.has_value else None)}"
    )

    lines.append("")
    lines.append(f"Dato mas antiguo utilizado: {format_moment(snapshot.oldest_moment())}.")
    lines.append(result.provenance(source_timestamp=snapshot.newest_moment()))
    return "\n".join(lines)


def diagnose_software_update(settings: Settings) -> str:
    """Bounded verdict on why no Remote Software Upgrade has arrived."""
    return pending(
        "diagnose_software_update",
        settings,
        needs_vin=True,
        needs_container=True,
        note=(
            "LIMITE DEL DIAGNOSTICO, que la herramienta dira literalmente en su salida: "
            "BMW documenta tres condiciones por las que no se ofrece la instalacion de una "
            "RSU (estado de carga bajo de la bateria de 12V, luces de emergencia puestas al "
            "apagar el motor, y aparcar con mas de un 12% de inclinacion). DE ESAS TRES, "
            "CARDATA SOLO PERMITE OBSERVAR UNA: la bateria. Sobre las otras dos solo puede "
            "declarar 'no observable por CarData'.\n"
            "Y con la primera lectura real hay una limitacion mas, peor: stateOfCharge y "
            "deepSleepModeActive llegaron VACIOS, asi que de la bateria solo se observa el "
            "voltaje mas serviceDemand. Falta comprobar si esos campos se rellenan con el "
            "coche despierto antes de escribir el veredicto.\n"
            "EL VALOR ESTA EN LA SERIE, NO EN LA FOTO. Necesita varias lecturas guardadas "
            "en el historico local para poder pronunciarse, y con una sola dira cuantas "
            "tiene y que le falta."
        ),
    )
