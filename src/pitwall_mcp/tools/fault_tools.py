"""Fault-memory tool: what the car stores for the workshop, counted and compared.

The value is not a translation of the codes, which the catalogue does not
provide and which would therefore be invented. It is seeing what changes: a
code that appears between two readings, or the ones a workshop visit clears.
"""

from __future__ import annotations

from ..cardata.client import CarDataAdapter
from ..config import Settings
from ..descriptors import FAULT_MEMORY
from ..fault_memory import Code, compare, parse_fault_memory
from ..formatting import explain_missing, format_moment
from ..telematic import TelematicSnapshot, ValueState
from .readiness import require_ready


def _codes(codes: list[Code]) -> str:
    """Codes as "ecu/code", or "ninguno"."""
    return ", ".join(f"{ecu}/{code}" for ecu, code in codes) or "ninguno"


def _changes(adapter: CarDataAdapter, settings: Settings) -> list[str]:
    """What appeared and what was cleared since the previous distinct memory."""
    memories = [
        (reading, memory)
        for reading in adapter.history.changes(settings.vin, FAULT_MEMORY)
        if (memory := parse_fault_memory(reading.value)) is not None
    ]
    if len(memories) < 2:
        return [
            "Cambios: es la primera memoria de averias del historico local, asi que no "
            "hay con que comparar. La proxima lectura dira que codigos aparecen o "
            "desaparecen."
        ]
    (previous_reading, previous), (_, latest) = memories[-2], memories[-1]
    appeared, cleared = compare(previous, latest)
    since = format_moment(previous_reading.source_moment or previous_reading.recorded_at)
    if not appeared and not cleared:
        return [f"Cambios desde la memoria anterior ({since}): ninguno."]
    return [
        f"Cambios desde la memoria anterior ({since}):",
        f"  Nuevos ({len(appeared)}): {_codes(appeared)}",
        f"  Ya no estan ({len(cleared)}): {_codes(cleared)}",
    ]


async def get_fault_memory(adapter: CarDataAdapter, settings: Settings) -> str:
    """The fault memory, grouped by ECU, with the changes since the last one."""
    require_ready(settings, needs_vin=True, needs_container=True)
    result = await adapter.get_telematic_data(settings.vin, settings.container_id)
    snapshot = TelematicSnapshot.from_payload(result.payload)
    entry = snapshot.get(FAULT_MEMORY)
    provenance = result.provenance(source_timestamp=snapshot.newest_moment())

    lines = ["MEMORIA DE AVERIAS", ""]
    if not entry.has_value:
        if entry.state is ValueState.EMPTY:
            lines.append(
                "La memoria de averias llega vacia en esta lectura: el vehiculo conoce el "
                "campo y no da valor."
            )
        else:
            lines.append(
                explain_missing(
                    FAULT_MEMORY,
                    in_catalogue=True,
                    in_container=True,
                    container_id=settings.container_id,
                )
            )
        return "\n".join([*lines, "", provenance])

    memory = parse_fault_memory(str(entry.value))
    if memory is None:
        lines.append(
            f"Llega un valor que no se ha podido leer como memoria de averias. Crudo: "
            f"{str(entry.value)[:200]!r}"
        )
        return "\n".join([*lines, "", provenance])

    by_ecu = memory.by_ecu()
    lines.append(
        f"{len(memory.codes)} codigos en {len(by_ecu)} centralitas   "
        f"({format_moment(entry.moment)})"
    )
    if memory.declared_count is not None and memory.declared_count != len(memory.codes):
        lines.append(
            f"  La cabecera dice {memory.declared_count}, pero el XML trae "
            f"{len(memory.codes)} entradas. BMW no documenta que es esa cabecera. El "
            f"contador de avisos CBS resulto ser un maximo transmisible y no un recuento, "
            f"asi que esta podria serlo tambien, pero eso es una sospecha, no un dato: se "
            f"dan los dos numeros y no se ajusta ninguno."
        )
    lines.extend(
        [
            "",
            "Que es y que no es:",
            "  Es la memoria de averias pensada para el taller. Cada entrada solo lleva la "
            "direccion de la centralita y el codigo: sin estado (activo o almacenado), sin "
            "fecha y sin descripcion. Muchos pueden ser antiguos o sin importancia, y sin "
            "estado ni fecha no hay forma de saberlo.",
            "  El significado de los codigos no esta en el catalogo telematico y no se "
            "traduce: seria inventarlo. Lo que si aporta es ver que cambia entre lecturas.",
            "",
            "Por centralita (direccion tal cual la da BMW):",
        ]
    )
    for ecu, codes in sorted(
        by_ecu.items(), key=lambda item: (-len(item[1]), item[0] if item[0] is not None else -1)
    ):
        label = ecu if ecu is not None else "sin direccion"
        lines.append(f"  Centralita {label}: {len(codes)} ({', '.join(codes)})")

    lines.append("")
    lines.extend(_changes(adapter, settings))
    lines.extend(["", provenance])
    return "\n".join(lines)
