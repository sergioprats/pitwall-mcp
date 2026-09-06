"""Tyre diagnosis tool. Skeleton for now."""

from __future__ import annotations

from ..config import Settings
from .pending import pending


def get_tyre_diagnosis(settings: Settings) -> str:
    """`GET /customers/vehicles/{vin}/smartMaintenanceTyreDiagnosis`."""
    return pending(
        "get_tyre_diagnosis",
        settings,
        needs_vin=True,
        note=(
            "Cuando funcione devolvera, por rueda: desgaste (tyreWear.dueMileage), "
            "defectos, dimensiones, fecha de montaje y de fabricacion, temporada, runflat, "
            "fabricante y dibujo. TTL de cache: 7 dias.\n"
            "ESTE ENDPOINT NO DEVUELVE PRESIONES. Las presiones vienen del contenedor "
            "telematico, en kPa, y son una fuente distinta. Si la pregunta es por presiones, "
            "la herramienta correcta es get_maintenance_summary, no esta."
        ),
    )
