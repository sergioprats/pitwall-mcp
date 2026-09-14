"""Fuel tool: tank, range, refuels, consumption, and a frozen OBFCM figure.

One container read, through the cache like everything else. The refuels and
the consumption come from the local history, not from BMW: BMW sends the tank
as it is now, and only the series says when it went up.
"""

from __future__ import annotations

from ..cardata.client import CarDataAdapter
from ..config import Settings
from ..descriptors import (
    FUEL_LEVEL,
    FUEL_REMAINING,
    LAST_REMAINING_RANGE,
    OBFCM_DISTANCE,
    OBFCM_FUEL,
    REMAINING_RANGE,
    TRAVELLED_DISTANCE,
)
from ..formatting import explain_missing, format_moment, parse_numeric
from ..fuel import FLOAT_TOLERANCE_L, MIN_CONSUMPTION_KM, consumption_since_refuel, detect_refuels
from ..telematic import TelematicEntry, TelematicSnapshot, ValueState
from .readiness import require_ready


def _km(value: float) -> str:
    """Kilometres with a Spanish thousands separator."""
    return f"{value:,.0f}".replace(",", ".")


def _decimal(value: float, places: int = 1) -> str:
    """A number with a Spanish decimal comma."""
    return f"{value:.{places}f}".replace(".", ",")


def _why_not(entry: TelematicEntry, settings: Settings) -> str:
    """Why a fuel descriptor carries no value (rule 6)."""
    if entry.state is ValueState.EMPTY:
        return "llega vacio: el vehiculo conoce el campo y no da valor."
    if entry.state is ValueState.NO_MEASUREMENT:
        return "el vehiculo responde -NA-, sin medida."
    return explain_missing(
        entry.descriptor,
        in_catalogue=True,
        in_container=True,
        container_id=settings.container_id,
    )


def _tank(snapshot: TelematicSnapshot, settings: Settings) -> list[str]:
    """Tank level, litres and range, as the car sent them."""
    lines = []
    level = snapshot.get(FUEL_LEVEL)
    if level.has_value:
        lines.append(f"Deposito: {level.value} %   ({format_moment(level.moment)})")
    else:
        lines.append(f"Deposito: {_why_not(level, settings)}")

    litres = snapshot.get(FUEL_REMAINING)
    if litres.has_value:
        lines.append(
            f"  unos {litres.value} L. El catalogo avisa de que puede desviarse hasta "
            f"+/-{FLOAT_TOLERANCE_L:.0f} L segun la posicion del flotador, y BMW no manda "
            f"la unidad: la de litros sale del catalogo."
        )
    elif level.has_value:
        lines.append(f"  Litros: {_why_not(litres, settings)}")

    last_range = snapshot.get(LAST_REMAINING_RANGE)
    if last_range.has_value and (km := last_range.as_float()) is not None:
        lines.append(
            f"Autonomia: {_km(km)} km, la ultima que envio el coche   "
            f"({format_moment(last_range.moment)})"
        )
    else:
        lines.append(f"Autonomia: {_why_not(last_range, settings)}")

    navigation = snapshot.get(REMAINING_RANGE)
    if navigation.has_value:
        lines.append(f"  Autonomia del navegador: {navigation.value} km")
    elif navigation.state is ValueState.EMPTY:
        lines.append("  La autonomia del navegador (navigation.remainingRange) llega vacia.")
    return lines


def _obfcm(snapshot: TelematicSnapshot, settings: Settings) -> list[str]:
    """The lifetime consumption, labelled for what it is."""
    fuel = snapshot.get(OBFCM_FUEL)
    distance = snapshot.get(OBFCM_DISTANCE)
    litres, km = fuel.as_float(), distance.as_float()
    if not litres or not km:
        missing = fuel if not litres else distance
        return [f"Consumo homologado (OBFCM): {_why_not(missing, settings)}"]
    stamp = fuel.moment
    dated = f"{stamp:%d-%m-%Y}" if stamp else "sin fecha"
    return [
        f"Consumo homologado (OBFCM): {_decimal(litres / km * 100)} l/100 km "
        f"({_decimal(litres, 2)} l en {_km(km)} km de referencia), con sello del {dated}.",
        "  NO es tu consumo actual. El catalogo describe su transferencia como algo que "
        "se hace en el taller, por cable, y este valor no se ha movido desde esa fecha. "
        "Es una referencia de largo plazo, no el consumo del dia a dia.",
    ]


def _history(adapter: CarDataAdapter, settings: Settings) -> list[str]:
    """Refuels and consumption, worked out from the local series."""
    snapshots = adapter.history.snapshots(
        settings.vin, (FUEL_LEVEL, FUEL_REMAINING, TRAVELLED_DISTANCE)
    )
    readings = sum(1 for _, values in snapshots if parse_numeric(values.get(FUEL_LEVEL)))
    refuels = detect_refuels(snapshots)

    lines = []
    if refuels:
        counted = "1 repostaje detectado" if len(refuels) == 1 else (
            f"{len(refuels)} repostajes detectados"
        )
        lines.append(f"{counted} en el historico local:")
        for refuel in refuels:
            lines.append(
                f"  - en la lectura del {format_moment(refuel.moment)}: del "
                f"{refuel.level_before:.0f} % al {refuel.level_after:.0f} %"
            )
    else:
        lines.append(
            f"Repostajes: ningun repostaje detectado en el historico local "
            f"({readings} lectura{'s' if readings != 1 else ''} de deposito)."
        )

    consumption = consumption_since_refuel(snapshots)
    if consumption is None:
        lines.append(
            f"Consumo real: aun no. Hacen falta dos lecturas de deposito separadas al "
            f"menos {MIN_CONSUMPTION_KM:.0f} km sin repostar entre medias; con menos, "
            f"el error del aforador pesa mas que la cifra."
        )
    else:
        origin = "el ultimo repostaje" if refuels else "la primera lectura de deposito"
        lines.append(
            f"Consumo desde {origin}: {_decimal(consumption.l_per_100km)} l/100 km "
            f"+/-{_decimal(consumption.margin)} ({_decimal(consumption.litres, 0)} L en "
            f"{_km(consumption.km)} km, del {consumption.since:%d-%m-%Y} al "
            f"{consumption.until:%d-%m-%Y}). El margen sale de los "
            f"+/-{FLOAT_TOLERANCE_L:.0f} L del aforador en cada extremo."
        )
    return lines


async def get_fuel_status(adapter: CarDataAdapter, settings: Settings) -> str:
    """Tank, range, refuels and consumption, and the OBFCM figure for what it is."""
    require_ready(settings, needs_vin=True, needs_container=True)
    result = await adapter.get_telematic_data(settings.vin, settings.container_id)
    snapshot = TelematicSnapshot.from_payload(result.payload)

    lines = ["COMBUSTIBLE", ""]
    lines.extend(_tank(snapshot, settings))
    lines.append("")
    lines.extend(_history(adapter, settings))
    lines.append("")
    lines.extend(_obfcm(snapshot, settings))
    lines.append("")
    lines.append(result.provenance(source_timestamp=snapshot.newest_moment()))
    return "\n".join(lines)
