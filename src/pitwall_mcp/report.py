"""A self-contained HTML report built from the local reading history.

READ ONLY, AND FREE. This module never touches the network and never asks the
adapter for anything: it renders what the `readings` table already holds, so
generating a report costs zero requests out of the daily 50.

Its reason to exist is that the SERIES is invisible everywhere else. The tools
answer with a snapshot; this answers with the evidence behind it: how many
distinct observations each descriptor has, since when, and which of the three
empty states it is in.

Everything that reasons about a trend goes through `HistoryStore.changes()`,
never `series()`. Two descriptors are stamped at request time rather than at
measurement time, so counting raw rows would overstate the evidence
(see `storage/history.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape

from .descriptors import (
    BATTERY_VOLTAGE,
    CBS_COUNT,
    CONDITION_BASED_SERVICES,
    CONTAINER_DESCRIPTORS,
    TRAVELLED_DISTANCE,
    WHEEL_POSITIONS,
    tyre_descriptor,
)
from .formatting import build_pressure, is_no_measurement, parse_numeric
from .storage.db import utc_now
from .storage.history import HistoryStore, Reading
from .telematic import CbsBlock, TelematicEntry, TelematicSnapshot, parse_cbs

#: How many trailing characters of the VIN survive masking.
VIN_VISIBLE_CHARS = 4

#: Sparkline geometry, in user units. The SVG scales itself to its box.
SPARK_WIDTH = 320
SPARK_HEIGHT = 60
SPARK_PADDING = 4


def mask_vin(vin: str) -> str:
    """Hide everything but the last four characters of a VIN."""
    if len(vin) <= VIN_VISIBLE_CHARS:
        return vin
    return "*" * (len(vin) - VIN_VISIBLE_CHARS) + vin[-VIN_VISIBLE_CHARS:]


def render_report(
    history: HistoryStore,
    vin: str,
    *,
    now: datetime | None = None,
    full_vin: bool = False,
) -> str:
    """Render the whole report for one VIN as a single HTML document."""
    moment = now or utc_now()
    shown_vin = vin if full_vin else mask_vin(vin)
    stats = history.descriptor_stats(vin)

    body = [
        "<header class='masthead'>",
        "<h1>Cuaderno del vehiculo</h1>",
        f"<p class='plate'>{escape(shown_vin)}</p>",
        f"<p class='standfirst'>Todo lo que hay aqui sale del historico guardado en tu disco. "
        f"Generado el {moment.strftime('%Y-%m-%d a las %H:%M UTC')}. "
        "Ninguna peticion a la API de BMW se ha hecho para construir esta pagina.</p>",
        f"<p class='window'>{_window_note(stats)}</p>",
        "</header>",
    ]

    if not stats:
        body.append(
            "<section><p class='empty'>El historico local no tiene ninguna lectura todavia. "
            "Lee el contenedor con una herramienta del servidor y vuelve a generar el "
            "informe: esta pagina se llena sola.</p></section>"
        )
        return _document("\n".join(body))

    body.append(_ledger_section(history, vin))
    body.append(_series_section(history, vin, TRAVELLED_DISTANCE, "Kilometraje", decimals=0))
    body.append(_cbs_section(history, vin))
    body.append(_pressure_section(history, vin))
    body.append(_series_section(history, vin, BATTERY_VOLTAGE, "Bateria de 12V: voltaje"))
    body.append(_coverage_section(history, vin))

    return _document("\n".join(body))


def _window_note(stats: dict[str, tuple[int, datetime | None, datetime | None]]) -> str:
    """Say how many readings there are and the window they span.

    The size of the window is what decides whether anything here supports a
    trend, so it belongs in the masthead rather than buried at the bottom.
    """
    if not stats:
        return "Historico vacio."
    total = sum(count for count, _, _ in stats.values())
    firsts = [first for _, first, _ in stats.values() if first is not None]
    lasts = [last for _, _, last in stats.values() if last is not None]
    plural = "lectura guardada" if total == 1 else "lecturas guardadas"
    if not firsts or not lasts:
        return f"{total} {plural}."
    start, end = min(firsts), max(lasts)
    if start.date() == end.date():
        return f"{total} {plural}, todas del {start.strftime('%Y-%m-%d')}."
    return (
        f"{total} {plural}, entre el {start.strftime('%Y-%m-%d')} y el {end.strftime('%Y-%m-%d')}."
    )


# --- Tyre pressures --------------------------------------------------------


def _pressure_section(history: HistoryStore, vin: str) -> str:
    """Render the four wheels in bar against their target."""
    parts = ["<section><h2>Presiones</h2><ul>"]
    for row, side, label in WHEEL_POSITIONS:
        measured = history.latest(vin, tyre_descriptor(row, side, "pressure"))
        target = history.latest(vin, tyre_descriptor(row, side, "pressureTarget"))
        if measured is None:
            parts.append(f"<li>{escape(label)}: sin ninguna lectura guardada.</li>")
            continue
        reading = build_pressure(
            label,
            measured.value,
            target.value if target is not None else None,
        )
        parts.append(f"<li>{escape(reading.describe())}</li>")
    parts.append("</ul></section>")
    return "\n".join(parts)


# --- Coverage: which of the three empty states each descriptor is in -------


#: The four states a descriptor can be in, and how each one is named.
#: "Absent", "present but empty" and `-NA-` are different facts, and rendering
#: them all as "no data" would hide which one actually happened. That
#: distinction is the single most useful thing this report has to say.
STATE_LABELS: dict[str, str] = {
    "value": "con valor",
    "empty": "presente y vacio",
    "na": "sin medida (-NA-)",
    "absent": "nunca ha llegado",
}


def _state_key(reading: Reading | None) -> str:
    """Return which of the four states a stored reading is in."""
    if reading is None:
        return "absent"
    if reading.value is None:
        return "empty"
    if is_no_measurement(reading.value):
        return "na"
    return "value"


def _coverage_state(reading: Reading | None) -> str:
    """Name the state of a descriptor in Spanish."""
    return STATE_LABELS[_state_key(reading)]


def _ledger_section(history: HistoryStore, vin: str) -> str:
    """The headline: one mark per container descriptor, coloured by state.

    This goes first because absence is the dominant fact about this vehicle.
    Eleven of the thirty-two keys came back empty, and a report that opened
    with a big mileage number would bury exactly the thing worth knowing.
    """
    marks = []
    tally: dict[str, int] = dict.fromkeys(STATE_LABELS, 0)
    for descriptor in CONTAINER_DESCRIPTORS:
        key = _state_key(history.latest(vin, descriptor))
        tally[key] += 1
        title = f"{descriptor}: {STATE_LABELS[key]}"
        marks.append(f"<span class='tick tick--{key}' title='{escape(title)}'></span>")

    total = len(CONTAINER_DESCRIPTORS)
    legend = " ".join(
        f"<span class='key'><span class='swatch swatch--{key}'></span>{STATE_LABELS[key]}"
        f" ({tally[key]})</span>"
        for key in STATE_LABELS
    )
    return (
        "<section class='ledger'>"
        "<h2>Que se puede observar de este coche</h2>"
        f"<div class='strip'>{''.join(marks)}</div>"
        f"<p class='headline'>{tally['value']} de {total} descriptores con valor</p>"
        f"<p class='legend'>{legend}</p>"
        "</section>"
    )


def _coverage_section(history: HistoryStore, vin: str) -> str:
    """One row per container descriptor: state, evidence and last reading."""
    parts = [
        "<section><h2>Cobertura de descriptores</h2>",
        "<p>Los tres estados vacios son hechos distintos y se dicen por separado: "
        "un descriptor que <strong>nunca ha llegado</strong> no es lo mismo que uno "
        "<strong>presente y vacio</strong>, ni que uno donde el vehiculo declara "
        "<strong>sin medida (-NA-)</strong>.</p>",
        "<div class='scroll'><table><thead><tr><th>Descriptor</th><th>Estado</th>",
        "<th>Observaciones</th><th>Ultima lectura</th></tr></thead><tbody>",
    ]
    for descriptor in CONTAINER_DESCRIPTORS:
        latest = history.latest(vin, descriptor)
        distinct = len(history.changes(vin, descriptor))
        last = latest.recorded_at.strftime("%Y-%m-%d %H:%M UTC") if latest is not None else "-"
        parts.append(
            f"<tr><td><code>{escape(descriptor)}</code></td>"
            f"<td>{_coverage_state(latest)}</td>"
            f"<td>{distinct}</td>"
            f"<td>{escape(last)}</td></tr>"
        )
    parts.append("</tbody></table></div></section>")
    return "\n".join(parts)


# --- Series ----------------------------------------------------------------


@dataclass(frozen=True)
class Series:
    """One descriptor's distinct observations, ready to render.

    Built from `changes()`, so a value re-read ten times inside an afternoon
    counts once. `points` holds only the observations that are actually
    numbers: a series of `-NA-` has observations but nothing to plot.
    """

    descriptor: str
    label: str
    readings: list[Reading]

    @property
    def latest(self) -> Reading | None:
        """The most recent distinct observation."""
        return self.readings[-1] if self.readings else None

    @property
    def points(self) -> list[float]:
        """The numeric observations, in order."""
        return [n for n in (parse_numeric(r.value) for r in self.readings) if n is not None]


def _series_section(
    history: HistoryStore,
    vin: str,
    descriptor: str,
    label: str,
    *,
    decimals: int | None = None,
) -> str:
    """Render one descriptor as a headline value plus its evidence."""
    series = Series(descriptor, label, history.changes(vin, descriptor))
    parts = [f"<section><h2>{escape(label)}</h2>"]

    latest = series.latest
    if latest is None:
        parts.append(f"<p>Sin ninguna lectura de <code>{escape(descriptor)}</code>.</p>")
        parts.append("</section>")
        return "\n".join(parts)

    parts.append(f"<p class='headline'>{_format_reading(latest, decimals=decimals)}</p>")
    parts.append(f"<p class='evidence'>{_evidence(series)}</p>")
    if len(series.points) >= 2:
        parts.append(_sparkline(series.points))
        parts.append(f"<p class='range'>{_range_note(series, latest.unit)}</p>")
    parts.append("</section>")
    return "\n".join(parts)


def _range_note(series: Series, unit: str | None) -> str:
    """Give the sparkline a scale, so the line is data and not decoration."""
    low, high = min(series.points), max(series.points)
    suffix = f" {escape(unit)}" if unit else ""
    if low == high:
        return f"Plano en {low:g}{suffix} a lo largo de toda la serie."
    return f"Recorre de {low:g} a {high:g}{suffix}, el punto lleno es la ultima lectura."


def _evidence(series: Series) -> str:
    """State how much the series actually supports, in plain Spanish."""
    count = len(series.readings)
    if count == 1:
        return "1 observacion distinta. Un solo punto: no hay tendencia."
    return f"{count} observaciones distintas."


def _format_reading(reading: Reading, *, decimals: int | None = None) -> str:
    """Render a stored value with its unit, or say it carries no measurement."""
    number = parse_numeric(reading.value)
    if number is None:
        raw = (reading.value or "").strip()
        return f"sin medida (el vehiculo devuelve {escape(raw)})" if raw else "sin lectura"
    text = f"{int(number):,}".replace(",", ".") if decimals == 0 else f"{number:g}"
    unit = f" {escape(reading.unit)}" if reading.unit else ""
    return f"{text}{unit}"


# --- Condition Based Services ----------------------------------------------


def _stored_cbs(history: HistoryStore, vin: str) -> CbsBlock | None:
    """Decode the last stored CBS block, reusing the telematic parser.

    The history keeps BMW's value verbatim, so this rebuilds a one-entry
    snapshot rather than re-implementing the double `json.loads` and the string
    sentinels that `telematic.parse_cbs` already gets right.
    """
    stored = history.latest(vin, CONDITION_BASED_SERVICES)
    if stored is None or stored.value is None:
        return None
    snapshot = TelematicSnapshot(
        entries={
            CONDITION_BASED_SERVICES: TelematicEntry.from_raw(
                CONDITION_BASED_SERVICES,
                {
                    "value": stored.value,
                    "unit": stored.unit,
                    "timestamp": stored.source_timestamp,
                },
            )
        }
    )
    counter = history.latest(vin, CBS_COUNT)
    reported = parse_numeric(counter.value) if counter is not None else None
    return parse_cbs(snapshot, None if reported is None else int(reported))


def _cbs_section(history: HistoryStore, vin: str) -> str:
    """Render the CBS breakdown, and both counts when they disagree."""
    parts = ["<section><h2>Mantenimiento (CBS)</h2>"]
    block = _stored_cbs(history, vin)
    if block is None:
        parts.append(
            "<p>Sin ningun bloque <code>conditionBasedServices</code> guardado en el historico.</p>"
            "</section>"
        )
        return "\n".join(parts)

    parts.append("<div class='scroll'><table><thead><tr><th>Partida</th><th>Estado</th>")
    parts.append("<th>Restante</th><th>Fecha</th></tr></thead><tbody>")
    for item in block.items:
        distance = (
            f"{item.distance_km:,}".replace(",", ".") + " km"
            if item.distance_km is not None
            else "sin kilometraje"
        )
        parts.append(
            f"<tr><td>{escape(item.label)}</td>"
            f"<td>{escape(item.status or 'sin estado')}</td>"
            f"<td>{distance}</td>"
            f"<td>{escape(item.date_text or 'sin fecha')}</td></tr>"
        )
    parts.append("</tbody></table></div>")
    parts.append(f"<p class='evidence'>{_cbs_count_note(block)}</p>")
    parts.append("</section>")
    return "\n".join(parts)


def _cbs_count_note(block: CbsBlock) -> str:
    """State both numbers, and never pretend a mismatch is not there."""
    total = len(block.items)
    if block.reported_count is None:
        return f"El bloque trae {total} partidas. No hay contador guardado con el que comparar."
    if block.count_matches:
        return (
            f"El bloque trae {total} partidas y el contador "
            f"<code>conditionBasedServicesCount</code> dice {block.reported_count}: coinciden."
        )
    return (
        f"El contador <code>conditionBasedServicesCount</code> dice {block.reported_count} "
        f"mientras el bloque trae {total} partidas. Es una discrepancia sin explicar: "
        f"se dan los dos numeros y no se ajusta ninguno."
    )


def _sparkline(points: list[float]) -> str:
    """Draw the series as an inline SVG polyline. No library, no network."""
    low, high = min(points), max(points)
    span = (high - low) or 1.0
    step = (SPARK_WIDTH - 2 * SPARK_PADDING) / (len(points) - 1)
    plotted = []
    for index, value in enumerate(points):
        x = SPARK_PADDING + index * step
        ratio = (value - low) / span
        y = SPARK_HEIGHT - SPARK_PADDING - ratio * (SPARK_HEIGHT - 2 * SPARK_PADDING)
        plotted.append(f"{x:.1f},{y:.1f}")
    last_x, last_y = plotted[-1].split(",")
    return (
        f"<svg class='spark' viewBox='0 0 {SPARK_WIDTH} {SPARK_HEIGHT}' "
        f"role='img' aria-label='Serie de {len(points)} observaciones'>"
        f"<polyline points='{' '.join(plotted)}'/>"
        f"<circle cx='{last_x}' cy='{last_y}' r='3.5'/></svg>"
    )


#: The whole stylesheet, inlined. No CDN, no font download, no request: the
#: file has to render identically on a laptop with no network, and printing it
#: has to produce something a workshop would accept.
STYLESHEET = """
:root {
  --paper: #f5f6f3;
  --ink: #17241e;
  --muted: #5c6b62;
  --rule: #c9cfc7;
  --live: #2f6f5b;
  --flag: #b0651e;
  --hollow: #9aa39b;
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper: #131714;
    --ink: #e6ebe6;
    --muted: #9aa79f;
    --rule: #2f3a34;
    --live: #6fbfa1;
    --flag: #d9974a;
    --hollow: #5c6b62;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 3rem 1.5rem 5rem;
  background: var(--paper);
  color: var(--ink);
  font: 1rem/1.6 "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
}
header, section { max-width: 58rem; margin: 0 auto; }
section { margin-top: 3.5rem; }
h1 { margin: 0; font-size: clamp(2rem, 5vw, 3rem); font-weight: 600; letter-spacing: -0.02em; }
h2 { margin: 0 0 1rem; font-size: 1.25rem; font-weight: 600; }
p { margin: 0 0 0.75rem; }
.masthead { padding-bottom: 1.75rem; border-bottom: 2px solid var(--ink); }
.plate {
  margin: 0.35rem 0 1rem;
  font-family: ui-monospace, "Cascadia Mono", "SF Mono", Consolas, monospace;
  font-size: 1.05rem;
  color: var(--muted);
}
.standfirst, .empty, .window { max-width: 42rem; color: var(--muted); }
.headline { font-size: 1.6rem; margin: 1rem 0 0.35rem; }
.evidence { color: var(--muted); font-style: italic; }
.strip { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 1.25rem; }
.tick { width: 15px; height: 30px; border-radius: 2px; background: var(--hollow); }
.tick--value { background: var(--live); }
.tick--na { background: var(--flag); }
.tick--empty { background: var(--hollow); }
.tick--absent { background: transparent; border: 1px solid var(--rule); }
.legend { display: flex; flex-wrap: wrap; gap: 1.25rem; color: var(--muted); font-size: 0.9rem; }
.key { display: inline-flex; align-items: center; gap: 0.4rem; }
.swatch { width: 10px; height: 10px; border-radius: 2px; background: var(--hollow); }
.swatch--value { background: var(--live); }
.swatch--na { background: var(--flag); }
.swatch--empty { background: var(--hollow); }
.swatch--absent { background: transparent; border: 1px solid var(--rule); }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.95rem; }
th {
  text-align: left;
  font-weight: 600;
  padding: 0.4rem 0.6rem 0.4rem 0;
  border-bottom: 1px solid var(--ink);
}
td {
  padding: 0.45rem 0.6rem 0.45rem 0;
  border-bottom: 1px solid var(--rule);
  vertical-align: top;
}
td + td, th + th { padding-left: 1.25rem; }
td:nth-child(3), th:nth-child(3) { text-align: right; font-variant-numeric: tabular-nums; }
.range { color: var(--muted); font-size: 0.9rem; margin-top: 0.35rem; }
td:nth-child(2), td:last-child { white-space: nowrap; }
code {
  font-family: ui-monospace, "Cascadia Mono", "SF Mono", Consolas, monospace;
  font-size: 0.85em;
}
ul { list-style: none; margin: 0; padding: 0; }
li { padding: 0.5rem 0; border-bottom: 1px solid var(--rule); }
.spark { display: block; width: 100%; max-width: 20rem; height: auto; margin: 0.5rem 0 0; }
.spark circle { fill: var(--live); }
.spark polyline {
  fill: none;
  stroke: var(--live);
  stroke-width: 2;
  stroke-linejoin: round;
  stroke-linecap: round;
}
@media print {
  body { background: #fff; color: #000; padding: 0; font-size: 11pt; }
  section { break-inside: avoid; margin-top: 2rem; }
  .legend { color: #333; }
}
"""


def _document(body: str) -> str:
    """Wrap the body in a self-contained HTML document."""
    return (
        "<!doctype html>\n<html lang='es'>\n<head>\n<meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        "<title>Cuaderno del vehiculo</title>\n"
        f"<style>{STYLESHEET}</style>\n"
        f"</head>\n<body>\n{body}\n</body>\n</html>\n"
    )
