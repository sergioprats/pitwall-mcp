"""Human-readable output: units, timestamps, provenance and absences.

Three rules from CLAUDE.md live here:

* Rule 4 — every answer states where it came from: BMW's own timestamp, the
  moment we read it, and whether it is cache, API or local history.
* Rule 6 — a missing value is never `null` and never a plausible-looking
  number. The text says WHY it is missing.
* Tyre pressures arrive in kPa and are shown in bar against their target.
  `-NA-` means "no measurement" and is never rendered as zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .descriptors import ASN_FALSE, ASN_TRUE, ASN_UNKNOWN, BATTERY_REPLACE_CODES, NO_MEASUREMENT
from .storage.db import parse_iso, utc_now

Provenance = Literal["api", "cache", "history"]

PROVENANCE_LABELS: dict[str, str] = {
    "api": "peticion a la API de BMW",
    "cache": "cache local",
    "history": "historico local",
}

#: 1 bar = 100 kPa.
KPA_PER_BAR = 100.0


# --- Values ----------------------------------------------------------------


def is_no_measurement(value: str | None) -> bool:
    """True when BMW explicitly reported the absence of a measurement."""
    return value is None or value.strip() == NO_MEASUREMENT or value.strip() == ""


def parse_numeric(value: str | None) -> float | None:
    """Parse a telematic value as a number, or `None` when it is not one.

    The API types every value as a string, so this is where `-NA-` and any
    other non-numeric surprise stops being treated as data.
    """
    if is_no_measurement(value):
        return None
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        return None


def format_tristate(value: str | None) -> str:
    """Render BMW's tri-state booleans (`ASN_isTrue` and friends) in Spanish."""
    if value is None:
        return "sin dato"
    normalised = value.strip()
    if normalised in (ASN_TRUE, "true", "True", "1"):
        return "si"
    if normalised in (ASN_FALSE, "false", "False", "0"):
        return "no"
    if normalised == ASN_UNKNOWN:
        return "desconocido (el vehiculo dice ASN_isUnknown)"
    return f"valor no reconocido: {normalised!r}"


def format_battery_replace(value: str | None) -> str:
    """Render `serviceDemand.replace`, a coded health value, not a percentage."""
    if is_no_measurement(value):
        return "sin dato"
    code = str(value).strip().split(".")[0]
    label = BATTERY_REPLACE_CODES.get(code)
    if label is None:
        return f"codigo {code} no documentado en el catalogo"
    return f"{label} (codigo {code})"


def format_battery_recharge(value: str | None) -> str:
    """Render `serviceDemand.recharge`: 1 means a recharge is needed."""
    if is_no_measurement(value):
        return "sin dato"
    code = str(value).strip().split(".")[0]
    if code == "1":
        return "si, el vehiculo pide recarga de la bateria de 12V"
    if code == "0":
        return "no"
    return f"valor no reconocido: {value!r}"


# --- Pressures -------------------------------------------------------------


def kpa_to_bar(kpa: float) -> float:
    """Convert kPa to bar."""
    return kpa / KPA_PER_BAR


@dataclass(frozen=True)
class PressureReading:
    """One wheel's pressure against its target, ready to print."""

    label: str
    pressure_kpa: float | None
    target_kpa: float | None

    @property
    def pressure_bar(self) -> float | None:
        """Measured pressure in bar, or `None` when there is no measurement."""
        return None if self.pressure_kpa is None else kpa_to_bar(self.pressure_kpa)

    @property
    def target_bar(self) -> float | None:
        """Target pressure in bar, or `None` when BMW did not send one."""
        return None if self.target_kpa is None else kpa_to_bar(self.target_kpa)

    @property
    def delta_bar(self) -> float | None:
        """Measured minus target, in bar, when both values exist."""
        if self.pressure_bar is None or self.target_bar is None:
            return None
        return self.pressure_bar - self.target_bar

    def describe(self) -> str:
        """One line: measured, target and differential, or why it is missing."""
        if self.pressure_bar is None:
            return f"{self.label}: sin medida (el vehiculo devuelve {NO_MEASUREMENT})"
        text = f"{self.label}: {self.pressure_bar:.2f} bar"
        if self.target_bar is None:
            return f"{text} (sin presion objetivo para comparar)"
        delta = self.delta_bar or 0.0
        sign = "+" if delta >= 0 else ""
        return f"{text} (objetivo {self.target_bar:.2f} bar, {sign}{delta:.2f} bar)"


def build_pressure(label: str, pressure: str | None, target: str | None) -> PressureReading:
    """Build a `PressureReading` from the two raw kPa strings."""
    return PressureReading(
        label=label,
        pressure_kpa=parse_numeric(pressure),
        target_kpa=parse_numeric(target),
    )


# --- Timestamps ------------------------------------------------------------


def humanize_age(seconds: float) -> str:
    """Render an age in Spanish, coarsely: minutes, hours or days.

    A negative age is a moment in the future (the quota reset, a cache expiry)
    and reads as "dentro de", not as "hace".
    """
    future = seconds < 0
    seconds = abs(int(seconds))
    prefix = "dentro de" if future else "hace"
    if seconds < 90:
        return f"{prefix} menos de 2 minutos"
    minutes = seconds // 60
    if minutes < 90:
        return f"{prefix} {minutes} minutos"
    hours = minutes // 60
    if hours < 48:
        return f"{prefix} {hours} horas"
    days = hours // 24
    return f"{prefix} {days} dias"


def format_ttl(ttl) -> str:  # noqa: ANN001 - timedelta
    """Render a TTL as plain Spanish: hours or days, not `30 days, 0:00:00`."""
    total = int(ttl.total_seconds())
    if total % 86400 == 0:
        days = total // 86400
        return f"{days} dia" if days == 1 else f"{days} dias"
    hours = total // 3600
    return f"{hours} h"


def format_moment(moment: datetime | str | None, *, now: datetime | None = None) -> str:
    """Render a timestamp as absolute UTC plus a relative age."""
    parsed = moment if isinstance(moment, datetime) else parse_iso(moment)
    if parsed is None:
        if isinstance(moment, str) and moment.strip():
            return f"{moment} (formato no reconocido)"
        return "sin marca de tiempo"
    reference = now or utc_now()
    age = (reference - parsed).total_seconds()
    return f"{parsed.strftime('%Y-%m-%d %H:%M UTC')} ({humanize_age(age)})"


def provenance_line(
    source: Provenance,
    *,
    source_timestamp: datetime | str | None,
    read_at: datetime | str | None,
    now: datetime | None = None,
) -> str:
    """The mandatory provenance line every tool answer carries (rule 4)."""
    label = PROVENANCE_LABELS.get(source, source)
    return (
        f"Procedencia: {label}. "
        f"Fecha del dato (BMW): {format_moment(source_timestamp, now=now)}. "
        f"Lectura realizada: {format_moment(read_at, now=now)}."
    )


# --- Absences --------------------------------------------------------------


def explain_missing(
    descriptor: str,
    *,
    in_catalogue: bool,
    in_container: bool,
    container_id: str | None = None,
) -> str:
    """Say WHY a descriptor has no value, never just that it has none (rule 6).

    The four documented reasons, in the order they must be checked:
    not in the catalogue, not in the container, not emitted by this vehicle,
    or no recent reading.
    """
    if not in_catalogue:
        return (
            f"'{descriptor}' no existe en el catalogo telematico de BMW. "
            f"No es que este vehiculo no lo emita: no es un descriptor valido. "
            f"Usa search_descriptors para ver que si existe."
        )
    if not in_container:
        target = f" '{container_id}'" if container_id else ""
        return (
            f"'{descriptor}' existe en el catalogo, pero NO esta en el contenedor{target}, "
            f"asi que /telematicData no lo devuelve. Para incluirlo hay que recrear el "
            f"contenedor con scripts/bootstrap_containers.py."
        )
    return (
        f"'{descriptor}' esta en el catalogo y en el contenedor, pero el vehiculo no lo "
        f"ha devuelto en esta lectura. Puede que este U11 no lo emita, o que no haya una "
        f"lectura reciente (el coche pasa semanas parado y en sueno profundo apaga la "
        f"mayoria de funciones telematicas). Se documenta como no disponible para este "
        f"vehiculo, no como inexistente."
    )
