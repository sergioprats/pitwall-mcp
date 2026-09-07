"""`get_api_quota` — how much of today's request budget is left. Costs no quota.

Everything here is derived from local SQLite: the `quota_log` ledger and the
`api_cache` table. It sends nothing.
"""

from __future__ import annotations

from ..cardata.client import (
    ENDPOINT_BASIC_DATA,
    ENDPOINT_MAPPINGS,
    ENDPOINT_TELEMATIC,
    ENDPOINT_TYRE_DIAGNOSIS,
    CarDataAdapter,
)
from ..config import TTLS, Settings
from ..formatting import format_moment, format_ttl
from ..storage.db import utc_now

_ENDPOINT_LABELS = {
    ENDPOINT_MAPPINGS: "Lista de vehiculos (/mappings)",
    ENDPOINT_BASIC_DATA: "Datos basicos (/basicData)",
    ENDPOINT_TELEMATIC: "Datos telematicos (/telematicData)",
    ENDPOINT_TYRE_DIAGNOSIS: "Diagnostico de neumaticos (/smartMaintenanceTyreDiagnosis)",
}


def get_api_quota(adapter: CarDataAdapter, settings: Settings) -> str:
    """Report today's consumption, the cache state and the token warning."""
    now = utc_now()
    status = adapter.quota.status(now)

    lines = [
        "Cuota de la API de BMW CarData (esta herramienta no gasta cuota).",
        "",
        f"Gastadas hoy: {status.used} de {status.limit} (tope local). "
        f"Quedan {status.remaining}.",
        f"Limite real de BMW: {status.bmw_limit} peticiones cada 24 h y por cuenta. "
        f"El tope local deja margen a proposito.",
        f"Ventana contada desde: {format_moment(status.window_start, now=now)}.",
        f"Reinicio estimado: {format_moment(status.resets_at, now=now)}.",
        "Suposicion: BMW resetea en dia natural UTC. El huso real no esta documentado, "
        "asi que el contador es conservador.",
    ]

    if status.first_request_at:
        lines.append(f"Primera peticion de hoy: {format_moment(status.first_request_at, now=now)}.")
    if status.last_request_at:
        lines.append(f"Ultima peticion de hoy: {format_moment(status.last_request_at, now=now)}.")

    if status.remote_denied:
        lines.append(
            "\nBMW ya ha rechazado hoy una peticion con 403 CU-429: la cuota de la CUENTA "
            "esta agotada, no solo el tope local. Recuerda que cualquier otra aplicacion "
            "con la misma cuenta consume del mismo bote."
        )
    elif status.exhausted:
        lines.append("\nTope local alcanzado: no se enviara ninguna peticion mas hoy.")

    lines.append("\nEstado de la cache (servir de aqui no gasta cuota):")
    vin = settings.vin or ""
    container = settings.container_id or ""
    any_cached = False
    for endpoint, label in _ENDPOINT_LABELS.items():
        entry = adapter.cached(
            endpoint,
            vin=vin if endpoint != ENDPOINT_MAPPINGS else "",
            container_id=container if endpoint == ENDPOINT_TELEMATIC else "",
        )
        ttl = TTLS.get(endpoint)
        ttl_text = f"TTL {format_ttl(ttl)}" if ttl else "sin TTL definido"
        if entry is None:
            lines.append(f"  - {label}: sin datos en cache ({ttl_text}).")
            continue
        any_cached = True
        state = "vigente" if entry.is_fresh(now) else "CADUCADA"
        lines.append(
            f"  - {label}: {state}, leida {format_moment(entry.fetched_at, now=now)}, "
            f"caduca {format_moment(entry.expires_at, now=now)}."
        )
    if not any_cached:
        lines.append(
            "  (Cache vacia: todavia no se ha leido nada de la API en esta instalacion.)"
        )

    warning = adapter.tokens.refresh_warning(now)
    if warning:
        lines.append(f"\n{warning}")

    missing = settings.missing_for_api()
    if missing:
        lines.append(
            "\nAviso: faltan credenciales (" + ", ".join(missing) + "), asi que ahora mismo "
            "no se puede gastar cuota aunque quede. Ejecuta 'python scripts/login.py'."
        )

    return "\n".join(lines)
