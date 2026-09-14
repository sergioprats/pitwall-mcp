"""Composite tools: maintenance summary and the software-update diagnosis."""

from __future__ import annotations

from ..cardata.client import CarDataAdapter
from ..catalogue import Catalogue
from ..config import Settings
from ..descriptors import (
    AVG_WEEKLY_DISTANCE_SHORT,
    BATTERY_SERVICE_RECHARGE,
    BATTERY_SERVICE_REPLACE,
    BATTERY_STATE_OF_CHARGE,
    BATTERY_VOLTAGE,
    CBS_COUNT,
    DEEP_SLEEP_MODE_ACTIVE,
    FAULT_MEMORY,
    FROZEN_LIFETIME_DESCRIPTORS,
    FUEL_LEVEL,
    FUEL_REMAINING,
    IGNITION_ON,
    INSPECTION_DATE_LEGAL,
    LAST_REMAINING_RANGE,
    SERVICE_DISTANCE_NEXT,
    SERVICE_DISTANCE_YELLOW,
    TRAVELLED_DISTANCE,
    TYRE_PRESSURE_DESCRIPTORS,
    TYRE_PRESSURE_TARGET_DESCRIPTORS,
    WHEEL_POSITIONS,
    tyre_descriptor,
)
from ..fault_memory import parse_fault_memory
from ..forecast import MIN_HISTORY_DAYS, project, rate_from_history
from ..formatting import (
    build_pressure,
    format_battery_recharge,
    format_battery_replace,
    format_moment,
    format_tristate,
    parse_numeric,
)
from ..storage.db import utc_now
from ..storage.history import Reading
from ..telematic import CbsBlock, TelematicSnapshot, parse_cbs
from ..tyre_trend import MIN_EVENTS, AxleTrend, axle_trends
from .readiness import require_ready
from .telematic_tools import render_cbs, render_check_control, render_urgent_cbs


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
    cbs = parse_cbs(snapshot, reported_count=snapshot.get(CBS_COUNT).as_int())

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
    lines.extend(render_urgent_cbs(cbs))

    inspection = snapshot.get(INSPECTION_DATE_LEGAL)
    if inspection.has_value:
        lines.append(f"Proxima inspeccion legal (ITV): {inspection.value}")

    lines.append("")
    lines.extend(render_cbs(cbs))
    if cbs is not None:
        lines.append("")
        lines.extend(
            _render_forecast(
                snapshot, cbs, adapter.history.changes(settings.vin, TRAVELLED_DISTANCE)
            )
        )
    lines.append("")
    lines.extend(render_check_control(snapshot))

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
    lines.extend(
        _render_tyre_trend(
            axle_trends(
                adapter.history.snapshots(
                    settings.vin,
                    (*TYRE_PRESSURE_DESCRIPTORS, *TYRE_PRESSURE_TARGET_DESCRIPTORS),
                )
            )
        )
    )

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
    lines.extend(_render_fuel_and_faults(snapshot))

    lines.append("")
    oldest = snapshot.oldest_moment(excluding=FROZEN_LIFETIME_DESCRIPTORS)
    lines.append(f"Dato mas antiguo utilizado: {format_moment(oldest)}.")
    lines.append(result.provenance(source_timestamp=snapshot.newest_moment()))
    return "\n".join(lines)


def _km(value: float) -> str:
    """Kilometres with a Spanish thousands separator."""
    return f"{value:,.0f}".replace(",", ".")


def _render_fuel_and_faults(snapshot: TelematicSnapshot) -> list[str]:
    """One line each for the tank and the fault memory; the detail has its own tools."""
    level = snapshot.get(FUEL_LEVEL)
    if level.has_value:
        litres = snapshot.get(FUEL_REMAINING)
        reach = snapshot.get(LAST_REMAINING_RANGE)
        fuel = f"Combustible: deposito al {level.value} %"
        if litres.has_value:
            fuel += f" (unos {litres.value} L, +/-6 L)"
        if reach.has_value:
            fuel += f", autonomia {reach.value} km"
        lines = [fuel + ". Detalle en get_fuel_status."]
    else:
        lines = ["Combustible: sin lectura en esta respuesta."]

    faults = snapshot.get(FAULT_MEMORY)
    memory = parse_fault_memory(str(faults.value)) if faults.has_value else None
    if memory is not None:
        lines.append(
            f"Memoria de averias: {len(memory.codes)} codigos en {len(memory.by_ecu())} "
            f"centralitas; detalle y cambios entre lecturas en get_fault_memory."
        )
    else:
        lines.append("Memoria de averias: sin lectura en esta respuesta.")
    return lines


def _render_forecast(
    snapshot: TelematicSnapshot, cbs: CbsBlock, mileage: list[Reading]
) -> list[str]:
    """When each CBS item falls due at the pace the car is really driven.

    Arithmetic on BMW's figures, not a new datum, and said so. Two paces may be
    available; the faster one is used so the estimate never arrives late.
    """
    lines = ["Prevision orientativa (calculo propio sobre los km de BMW, no un dato de BMW):"]
    bmw = snapshot.get(AVG_WEEKLY_DISTANCE_SHORT).as_float()
    local = rate_from_history(mileage)

    paces: list[tuple[float, str]] = []
    if bmw:
        paces.append((bmw, "media semanal que da BMW"))
    if local is not None:
        paces.append((local.km_per_week, f"tu historico local, {local.days:.0f} dias"))
    if local is None:
        lines.append(
            f"  El historico local aun no permite medir tu ritmo: hacen falta lecturas de "
            f"kilometraje separadas al menos {MIN_HISTORY_DAYS:.0f} dias."
        )
    if not paces:
        lines.append("  Sin un ritmo de uso con el que calcular, no hay prevision.")
        return lines

    pace, source = max(paces)
    header = f"  Ritmo usado: {_km(pace)} km/semana ({source})"
    if len(paces) == 2:
        other, other_source = min(paces)
        header += f"; {other_source}: {_km(other)}. Se usa el mayor para no quedarse corto"
    lines.append(header + ".")

    reference = snapshot.get(TRAVELLED_DISTANCE).moment or utc_now()
    items = sorted(
        (item for item in cbs.items if item.distance_km is not None),
        key=lambda item: item.distance_km or 0,
    )
    for item in items:
        projection = project(item.distance_km, item.date_text, pace, reference)
        if projection.weeks is None or projection.due_date is None:
            continue
        weeks = f"{projection.weeks:.1f}".replace(".", ",")
        line = (
            f"  - {item.label}: {_km(item.distance_km or 0)} km, unas {weeks} semanas, "
            f"hacia el {projection.due_date:%d-%m-%Y}"
        )
        if item.date_text:
            if projection.first == "km":
                line += f"; vence antes por km que por fecha ({item.date_text})"
            else:
                line += f"; vence antes por fecha ({item.date_text}) que por km"
        lines.append(line + ".")
    return lines


def _render_tyre_trend(trends: list[AxleTrend] | None) -> list[str]:
    """Each axle's left-right gap over the history, and whether it is growing."""
    if trends is None:
        return [
            f"  Tendencia por eje: aun no hay lecturas suficientes (hacen falta "
            f"{MIN_EVENTS}, separadas al menos un dia)."
        ]
    lines = [
        f"  Tendencia por eje ({trends[0].events} lecturas): cada rueda se compara con su "
        f"pareja de eje en la misma lectura, que comparte temperatura y carga. El "
        f"sensor mide en pasos de 10 kPa."
    ]
    for trend in trends:
        first, last = abs(trend.first_gap), abs(trend.last_gap)
        if trend.flagged:
            lines.append(
                f"    OJO, eje {trend.axle}: la rueda {trend.lower_side} pierde presion "
                f"respecto a su pareja (de {first} a {last} kPa). Posible fuga lenta: "
                f"compruebala con un manometro."
            )
        elif trend.first_gap == trend.last_gap == 0:
            lines.append(f"    Eje {trend.axle}: sin diferencia entre ruedas.")
        elif trend.last_gap == 0:
            lines.append(
                f"    Eje {trend.axle}: hoy sin diferencia entre ruedas (en la primera "
                f"lectura habia {first} kPa); sin indicio de fuga lenta."
            )
        else:
            lines.append(
                f"    Eje {trend.axle}: la rueda {trend.lower_side} va por debajo de su "
                f"pareja, de {first} a {last} kPa; la diferencia no crece, sin indicio "
                f"de fuga lenta."
            )
    return lines


# --- The software-update diagnosis -----------------------------------------

#: Resting voltage below which a 12V battery is usually considered low. Only a
#: reference point: a reading taken with the engine running says nothing about
#: the resting state, and `isIgnitionOn` is empty on this vehicle.
LOW_VOLTAGE = 12.2

#: Above this the alternator is charging. No 12V battery rests this high, so
#: such a reading says nothing about the resting state the update check cares
#: about. Both voltages this vehicle has sent so far (14.39 V, 14.35 V) are here.
CHARGING_VOLTAGE = 13.5

#: Below this many distinct observations the tool refuses to talk about trends.
MIN_OBSERVATIONS = 2

#: How much the voltage has to move before the change is called a direction
#: rather than noise.
TREND_EPSILON = 0.05

NO_OBSERVABLE = "NO OBSERVABLE POR CARDATA"


def _voltage_series(adapter: CarDataAdapter, vin: str) -> list[Reading]:
    """Distinct voltage observations, oldest measurement first.

    `changes()` collapses consecutive repetitions, which matters because a value
    re-read inside one afternoon would otherwise look like several observations.
    The result is then ordered by BMW's own timestamp rather than by the moment
    we happened to store it, so a backfilled reading cannot fake a trend.
    """
    readings = adapter.history.changes(vin, BATTERY_VOLTAGE)
    return sorted(readings, key=lambda r: r.source_moment or r.recorded_at)


def _trend(series: list[Reading]) -> str:
    """Name the direction of the series, or refuse to name one."""
    values = [parse_numeric(r.value) for r in series]
    numbers = [v for v in values if v is not None]
    if len(numbers) < MIN_OBSERVATIONS:
        return "sin tendencia"
    delta = numbers[-1] - numbers[0]
    ends = f"de {numbers[0]:.2f} V a {numbers[-1]:.2f} V"
    if delta < -TREND_EPSILON:
        return f"baja {ends} entre la primera y la ultima observacion"
    if delta > TREND_EPSILON:
        return f"sube {ends} entre la primera y la ultima observacion"
    return f"se mantiene plana, {ends}"


def _is_charging(reading: Reading) -> bool:
    """True when the reading is an alternator voltage rather than a battery at rest."""
    value = parse_numeric(reading.value)
    return value is not None and value >= CHARGING_VOLTAGE


def _verdict(series: list[Reading], *, ignition_known: bool) -> list[str]:
    """The bounded verdict. Never stronger than the evidence behind it."""
    numeric = [r for r in series if parse_numeric(r.value) is not None]
    count = len(numeric)
    resting = [r for r in numeric if not _is_charging(r)]
    charging = count - len(resting)

    if len(resting) < MIN_OBSERVATIONS:
        counted = "1 observacion distinta" if count == 1 else f"{count} observaciones distintas"
        lines = [
            "SIN VEREDICTO.",
            f"El historico local tiene {counted} de voltaje de la bateria de 12V, y hacen "
            f"falta al menos 2 observaciones separadas en el tiempo, y tomadas en reposo, "
            f"para hablar de una tendencia.",
        ]
        if charging:
            lines.append(
                f"De ellas, {charging} pasa{'n' if charging > 1 else ''} de "
                f"{CHARGING_VOLTAGE} V: eso es el alternador cargando con el motor en "
                f"marcha, y no dice nada de la bateria en reposo, que es lo que cuenta "
                f"para la actualizacion. No se usa{'n' if charging > 1 else ''}."
            )
        lines.append(
            "Sobre como conseguir mas puntos: battery.voltage viaja en un grupo de nueve "
            "descriptores que por REST se refresca muy de tarde en tarde. Su sello se "
            "quedo 34 horas quieto a lo largo de cuatro lecturas y dos trayectos (7 y 8 "
            "de septiembre de 2026); la siguiente lectura, el 13, ya lo traia renovado, "
            "pero con el motor en marcha (14.35 V). Repetir la lectura REST puede anadir "
            "puntos, pocos y, hasta ahora, siempre con el alternador cargando. Una serie "
            "en reposo necesita el streaming MQTT de la Fase 2, que hoy es diseno y no "
            "codigo."
        )
        return lines

    lowest = min(parse_numeric(r.value) for r in resting)
    lines = [f"Serie de {count} observaciones distintas."]
    if charging:
        lines.append(
            f"{charging} pasa{'n' if charging > 1 else ''} de {CHARGING_VOLTAGE} V "
            f"(alternador cargando) y se deja{'n' if charging > 1 else ''} fuera. En las "
            f"{len(resting)} restantes el voltaje {_trend(resting)}."
        )
    else:
        lines.append(f"El voltaje {_trend(resting)}.")
    if not ignition_known:
        lines.append(
            "Esa pendiente mezcla lecturas tomadas en condiciones desconocidas: sin "
            "isIgnitionOn, parte de la diferencia puede ser solo motor en marcha frente a "
            "motor parado, y no una bateria descargandose."
        )
    if lowest < LOW_VOLTAGE:
        lines.extend(
            [
                f"Alguna observacion cae por debajo de {LOW_VOLTAGE} V (minimo observado: "
                f"{lowest:.2f} V), que es donde suele considerarse baja una bateria de 12V "
                f"en reposo.",
                "Eso hace la hipotesis de la bateria COMPATIBLE con lo observado, pero "
                "no demuestra que sea la causa de que no llegue la actualizacion: no se "
                "sabe si esas medidas se tomaron en reposo, y las otras dos condiciones "
                f"siguen siendo {NO_OBSERVABLE}.",
            ]
        )
    else:
        lines.extend(
            [
                f"Ninguna observacion cae por debajo de {LOW_VOLTAGE} V (minimo observado: "
                f"{lowest:.2f} V).",
                "Con esta evidencia la hipotesis de la bateria queda debilitada, pero eso "
                f"no descarta nada por si solo: las otras dos condiciones son {NO_OBSERVABLE}, "
                "y stateOfCharge, que es la medida que BMW usa de verdad, llega vacia.",
            ]
        )
    return lines


async def diagnose_software_update(adapter: CarDataAdapter, settings: Settings) -> str:
    """Bounded verdict on why no Remote Software Upgrade has arrived.

    Reads the maintenance container and `/basicData`, both through the cache, so
    inside their TTLs this costs nothing. The reasoning itself runs on the local
    history: the value is in the SERIES, never in one snapshot.
    """
    require_ready(settings, needs_vin=True, needs_container=True)
    result = await adapter.get_telematic_data(settings.vin, settings.container_id)
    snapshot = TelematicSnapshot.from_payload(result.payload)
    basic = await adapter.get_basic_data(settings.vin)
    basic_payload = basic.payload if isinstance(basic.payload, dict) else {}

    lines = [
        "DIAGNOSTICO: POR QUE NO LLEGA UNA ACTUALIZACION DE SOFTWARE",
        "",
        "1. LIMITE DEL DIAGNOSTICO",
        "BMW documenta tres condiciones por las que no se ofrece la instalacion de una "
        "Remote Software Upgrade. De esas tres, CarData solo permite observar una:",
        "  - Estado de carga bajo de la bateria de 12V: parcialmente observable.",
        f"  - Luces de emergencia puestas al apagar el motor: {NO_OBSERVABLE}.",
        f"  - Aparcar con mas de un 12% de inclinacion: {NO_OBSERVABLE}.",
        "Nada de lo que sigue puede confirmar ni descartar esas dos ultimas.",
        "",
        "2. VERSION DE SOFTWARE",
        "En el catalogo telematico de BMW no existe ningun descriptor de version de "
        "software: ni iStep, ni version, ni estado o historial de Remote Software Upgrade.",
    ]

    pu_step = basic_payload.get("puStep")
    if pu_step is None:
        lines.append(
            "puStep: /basicData no lo devuelve para este vehiculo, asi que no queda ni "
            "esa pista indirecta. Ademas no hay historico de puStep: el historico local "
            "solo guarda respuestas de /telematicData."
        )
    else:
        lines.append(
            f"puStep: {pu_step}. NO es la version de software, es el paso de actualizacion "
            "de producto. Y no hay historico de puStep con el que detectar un cambio: el "
            "historico local solo guarda respuestas de /telematicData."
        )

    lines.extend(["", "3. BATERIA DE 12V (la unica condicion observable)"])
    voltage = snapshot.get(BATTERY_VOLTAGE)
    if voltage.has_value:
        lines.append(f"Voltaje en la ultima lectura: {voltage.value} V")
        lines.append(f"  Medido: {format_moment(voltage.moment)}.")
    else:
        lines.append("Voltaje: sin lectura en esta respuesta.")

    ignition = snapshot.get(IGNITION_ON)
    if not ignition.has_value:
        lines.append(
            "  isIgnitionOn ha llegado vacio, asi que no se puede saber en que condicion "
            "se tomo esa medida: con el motor en marcha el alternador da mas de 14 V y la "
            "cifra no dice nada del estado en reposo."
        )
    else:
        lines.append(f"  Contacto en esa lectura: {format_tristate(ignition.value)}.")

    soc = snapshot.get(BATTERY_STATE_OF_CHARGE)
    if soc.has_value:
        lines.append(f"stateOfCharge: {soc.value} %")
    else:
        lines.append(
            "stateOfCharge: presente y vacio. El vehiculo conoce el campo y no ha devuelto "
            "valor, asi que la medida que BMW usa de verdad para decidir NO esta disponible. "
            "No se sustituye por el voltaje."
        )

    replace = snapshot.get(BATTERY_SERVICE_REPLACE)
    recharge = snapshot.get(BATTERY_SERVICE_RECHARGE)
    lines.append(
        f"serviceDemand.replace (salud): "
        f"{format_battery_replace(replace.value if replace.has_value else None)}"
    )
    lines.append(
        f"serviceDemand.recharge (pide recarga): "
        f"{format_battery_recharge(recharge.value if recharge.has_value else None)}"
    )

    lines.extend(["", "4. CONTEXTO"])
    deep_sleep = snapshot.get(DEEP_SLEEP_MODE_ACTIVE)
    if deep_sleep.has_value:
        lines.append(f"deepSleepModeActive: {format_tristate(deep_sleep.value)}")
    else:
        lines.append(
            "deepSleepModeActive: presente y vacio. La hipotesis del sueno profundo, que "
            "explicaria tanto la falta de actualizaciones como las lecturas antiguas, no "
            "es observable en esta lectura."
        )

    mileage = snapshot.get(TRAVELLED_DISTANCE)
    km = mileage.as_int()
    if km is not None:
        lines.append(f"Kilometraje: {km:,} km".replace(",", "."))
    else:
        lines.append("Kilometraje: sin lectura en esta respuesta.")

    lines.extend(
        [
            "",
            "5. VEREDICTO",
            *_verdict(_voltage_series(adapter, settings.vin), ignition_known=ignition.has_value),
        ]
    )

    lines.append("")
    lines.append(f"Dato mas reciente utilizado: {format_moment(snapshot.newest_moment())}.")
    oldest = snapshot.oldest_moment(excluding=FROZEN_LIFETIME_DESCRIPTORS)
    lines.append(f"Dato mas antiguo utilizado: {format_moment(oldest)}.")
    lines.append(result.provenance(source_timestamp=snapshot.newest_moment()))
    return "\n".join(lines)
