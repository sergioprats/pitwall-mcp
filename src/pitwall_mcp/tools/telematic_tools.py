"""Telematic container tools.

`get_telematic_data` and `get_vehicle_status` read the maintenance container
through the adapter, so they pass through the cache and the quota counter like
everything else. Inside a 12 h TTL they cost nothing.

They report the four value states separately (see `telematic.py`): a key that
arrived empty is not the same as one that never arrived, and neither is `-NA-`.
"""

from __future__ import annotations

from ..cardata.client import CarDataAdapter
from ..catalogue import Catalogue
from ..config import Settings
from ..descriptors import (
    CBS_COUNT,
    CONTAINER_DESCRIPTORS,
    SERVICE_DISTANCE_NEXT,
    SERVICE_DISTANCE_YELLOW,
    TRAVELLED_DISTANCE,
)
from ..formatting import explain_missing, format_moment
from ..telematic import CbsBlock, TelematicSnapshot, ValueState, parse_cbs
from .pending import require_ready


async def _read(adapter: CarDataAdapter, settings: Settings):
    """Fetch the container and parse it. Raises the Spanish errors if not ready."""
    require_ready(settings, needs_vin=True, needs_container=True)
    result = await adapter.get_telematic_data(settings.vin, settings.container_id)
    return result, TelematicSnapshot.from_payload(result.payload)


def render_cbs(block: CbsBlock | None) -> list[str]:
    """Render the CBS breakdown, or say plainly why there is none."""
    if block is None:
        return [
            "Desglose CBS: no disponible en esta lectura. "
            "vehicle.status.conditionBasedServices ha llegado sin valor, asi que solo "
            "se puede dar la cifra global de serviceDistance.next."
        ]

    lines = [f"Partidas CBS: {len(block.items)}"]
    for item in sorted(
        block.items, key=lambda i: (i.distance_km is None, i.distance_km or 0)
    ):
        parts = []
        if item.distance_km is not None:
            parts.append(f"{item.distance_km:,} km".replace(",", "."))
        if item.date_text:
            parts.append(f"hasta {item.date_text}")
        if not parts:
            parts.append("sin plazo informado por BMW")
        estado = f" [{item.status}]" if item.status else ""
        lines.append(f"  - {item.label}{estado}: {', '.join(parts)}")

    if block.count_matches is False:
        lines.append(
            f"  AVISO: conditionBasedServicesCount dice {block.reported_count} pero el "
            f"desglose trae {len(block.items)} partidas. BMW no documenta la diferencia y "
            f"no se explica aqui: se dan los dos numeros tal cual."
        )
    return lines


def render_states(
    snapshot: TelematicSnapshot, settings: Settings, catalogue: Catalogue
) -> list[str]:
    """List what came back empty or absent, and why that is not the same thing."""
    lines: list[str] = []
    empty = [d for d in snapshot.with_state(ValueState.EMPTY) if d in CONTAINER_DESCRIPTORS]
    na = [d for d in snapshot.with_state(ValueState.NO_MEASUREMENT) if d in CONTAINER_DESCRIPTORS]
    absent = snapshot.missing_from(CONTAINER_DESCRIPTORS)

    if empty:
        lines.append("")
        lines.append(
            f"Sin lectura ({len(empty)} de {len(CONTAINER_DESCRIPTORS)}): el vehiculo "
            f"conoce estos campos pero ha devuelto valor vacio. No es que no existan, y "
            f"tampoco es un cero."
        )
        lines.extend(f"  - {descriptor}" for descriptor in empty)
    if na:
        lines.append("")
        lines.append(f"Sin medida ({len(na)}): el vehiculo responde -NA- explicitamente.")
        lines.extend(f"  - {descriptor}" for descriptor in na)
    if absent:
        lines.append("")
        lines.append(f"Ausentes de la respuesta ({len(absent)}):")
        for descriptor in absent:
            lines.append(
                "  - "
                + explain_missing(
                    descriptor,
                    in_catalogue=catalogue.get(descriptor) is not None,
                    in_container=descriptor in CONTAINER_DESCRIPTORS,
                    container_id=settings.container_id,
                )
            )
    return lines


async def get_telematic_data(
    adapter: CarDataAdapter, settings: Settings, catalogue: Catalogue
) -> str:
    """Read the whole container and report every descriptor and its state."""
    result, snapshot = await _read(adapter, settings)

    with_value = [d for d in CONTAINER_DESCRIPTORS if snapshot.get(d).has_value]
    lines = [
        f"Contenedor telematico: {len(with_value)} de {len(CONTAINER_DESCRIPTORS)} "
        f"descriptores con valor.",
        result.provenance(source_timestamp=snapshot.newest_moment()),
        f"Dato mas antiguo utilizado: {format_moment(snapshot.oldest_moment())}.",
        "",
        "CON VALOR:",
    ]
    for descriptor in with_value:
        entry = snapshot.get(descriptor)
        unit = f" {entry.unit}" if entry.unit else ""
        value = str(entry.value)
        shown = value if len(value) <= 70 else value[:67] + "..."
        lines.append(f"  {descriptor}")
        lines.append(f"    {shown}{unit}   ({format_moment(entry.moment)})")

    lines.extend(render_states(snapshot, settings, catalogue))
    return "\n".join(lines)


async def get_vehicle_status(
    adapter: CarDataAdapter, settings: Settings, catalogue: Catalogue
) -> str:
    """Mileage plus the CBS breakdown, from the same single request."""
    result, snapshot = await _read(adapter, settings)

    mileage = snapshot.get(TRAVELLED_DISTANCE)
    next_service = snapshot.get(SERVICE_DISTANCE_NEXT)
    yellow = snapshot.get(SERVICE_DISTANCE_YELLOW)
    block = parse_cbs(snapshot, reported_count=snapshot.get(CBS_COUNT).as_int())

    lines = ["ESTADO DEL VEHICULO", ""]

    if mileage.has_value:
        km = mileage.as_int()
        shown = f"{km:,}".replace(",", ".") if km is not None else mileage.value
        lines.append(f"Kilometraje: {shown} km   ({format_moment(mileage.moment)})")
    else:
        lines.append(
            "Kilometraje: "
            + explain_missing(
                TRAVELLED_DISTANCE,
                in_catalogue=catalogue.get(TRAVELLED_DISTANCE) is not None,
                in_container=True,
                container_id=settings.container_id,
            )
        )

    if next_service.has_value:
        remaining = next_service.as_int()
        threshold = yellow.as_int()
        note = ""
        if remaining is not None and threshold is not None:
            if remaining <= threshold:
                note = f"  <- por debajo del umbral de preaviso ({threshold} km)"
            else:
                note = f"  (umbral de preaviso: {threshold} km, faltan {remaining - threshold})"
        lines.append(f"Proximo servicio en: {remaining} km{note}")

    lines.append("")
    lines.extend(render_cbs(block))

    lines.append("")
    lines.append(result.provenance(source_timestamp=snapshot.newest_moment()))
    lines.append(f"Dato mas antiguo utilizado: {format_moment(snapshot.oldest_moment())}.")
    lines.extend(render_states(snapshot, settings, catalogue))
    return "\n".join(lines)
