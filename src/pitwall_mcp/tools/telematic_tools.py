"""Telematic container tools. Skeletons for now."""

from __future__ import annotations

from ..config import Settings
from ..descriptors import CONTAINER_DESCRIPTORS, CONTAINER_NAME
from .pending import pending


def get_telematic_data(settings: Settings) -> str:
    """`GET /customers/vehicles/{vin}/telematicData` for the configured container."""
    return pending(
        "get_telematic_data",
        settings,
        needs_vin=True,
        needs_container=True,
        note=(
            f"Cuando funcione devolvera los {len(CONTAINER_DESCRIPTORS)} descriptores del "
            f"contenedor '{CONTAINER_NAME}', cada uno con su valor, unidad y timestamp de "
            f"BMW, y guardara todo en el historico local. TTL de cache: 12 h, es decir 2 "
            f"peticiones al dia como mucho.\n"
            "Los descriptores que no lleguen se reportaran uno a uno como no disponibles "
            "para este vehiculo, nunca como inexistentes."
        ),
    )


def get_vehicle_status(settings: Settings) -> str:
    """Mileage plus the CBS block, from the same container."""
    return pending(
        "get_vehicle_status",
        settings,
        needs_vin=True,
        needs_container=True,
        note=(
            "PENDIENTE DE VERIFICACION REAL. El kilometraje "
            "(vehicle.vehicle.travelledDistance) no tiene ninguna duda. El desglose CBS por "
            "partida depende de la estructura de vehicle.status.conditionBasedServices, que "
            "BMW no documenta y que ademas podria estar ligada a un endpoint dedicado y no "
            "llegar por /telematicData. Es el riesgo numero uno del proyecto y se resuelve "
            "con una unica llamada real, que se grabara como fixture.\n"
            "Si el desglose no llega, la herramienta lo dira y se quedara con el valor "
            "global de vehicle.status.serviceDistance.next."
        ),
    )
