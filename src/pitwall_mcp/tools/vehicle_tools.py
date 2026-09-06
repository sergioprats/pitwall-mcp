"""Vehicle identity tools: mappings and basic data. Skeletons for now."""

from __future__ import annotations

from ..config import Settings
from .pending import pending


def list_vehicles(settings: Settings) -> str:
    """`GET /customers/vehicles/mappings`: VIN, mappedSince and mappingType."""
    return pending(
        "list_vehicles",
        settings,
        note=(
            "Cuando funcione devolvera, por cada VIN de la cuenta: el VIN, desde cuando "
            "esta mapeado y si eres usuario PRIMARY o SECONDARY. CarData exige PRIMARY "
            "para leer datos del vehiculo. TTL de cache: 30 dias, asi que cuesta 1 "
            "peticion al mes."
        ),
    )


def get_vehicle_basic_data(settings: Settings) -> str:
    """`GET /customers/vehicles/{vin}/basicData`."""
    return pending(
        "get_vehicle_basic_data",
        settings,
        needs_vin=True,
        note=(
            "Cuando funcione devolvera modelRange, series, modelName, engine, "
            "constructionDate, fullSAList y puStep. TTL de cache: 30 dias."
        ),
    )


def report_product_update_step(settings: Settings) -> str:
    """`puStep` from /basicData, explicitly labelled as what it is."""
    return pending(
        "report_product_update_step",
        settings,
        needs_vin=True,
        note=(
            "IMPORTANTE: puStep es el PASO DE ACTUALIZACION DE PRODUCTO, no la version de "
            "software del vehiculo. No existe ningun descriptor de version de software en "
            "el catalogo de BMW, asi que esta herramienta sustituye a la inexistente "
            "get_software_version y no debe presentarse como equivalente.\n"
            "Cuando funcione guardara cada lectura de puStep en el historico local para "
            "poder detectar si alguna vez cambia. Riesgo conocido: puede no moverse nunca "
            "con las RSU; si tras meses de historico no cambia, se documentara como inutil "
            "para el diagnostico en lugar de mantener la ficcion."
        ),
    )
