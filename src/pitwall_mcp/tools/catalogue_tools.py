"""`search_descriptors` — catalogue search. Costs no quota.

The catalogue is a local file and the only source of truth for descriptors.
When a query finds nothing, the answer says so plainly instead of inventing a
plausible descriptor, and it names the things that are known NOT to exist.
"""

from __future__ import annotations

from ..catalogue import Catalogue, SearchHit
from ..descriptors import CONTAINER_NAME

#: Topics verified as absent from the catalogue by exhaustive search. Nothing
#: may ever be implemented on top of these (CLAUDE.md, "lo que NO existe").
KNOWN_ABSENT: dict[str, str] = {
    "software": (
        "No existe NINGUN descriptor de version de software del vehiculo: ni iStep, ni "
        "version, ni nada relacionado con Remote Software Upgrade, ni estado, "
        "disponibilidad o historial de actualizaciones. Lo unico parecido es 'puStep' en "
        "/basicData, que es el paso de actualizacion de producto y probablemente no cambia "
        "con cada RSU; lo expone report_product_update_step, etiquetado como tal."
    ),
    "istep": (
        "No existe ningun descriptor de iStep ni de version de software del vehiculo."
    ),
    "actualizacion": (
        "No existe ningun descriptor de estado, disponibilidad o historial de "
        "actualizaciones de software."
    ),
    "emergencia": (
        "El estado de las luces de emergencia NO existe en el catalogo. Es una de las tres "
        "condiciones que BMW documenta para no ofrecer una RSU, y CarData no permite "
        "observarla."
    ),
    "inclinacion": (
        "La inclinacion o pendiente de aparcamiento NO existe en el catalogo. Es otra de "
        "las condiciones de la RSU que CarData no permite observar."
    ),
    "pendiente": (
        "La inclinacion o pendiente de aparcamiento NO existe en el catalogo."
    ),
}


def _absence_notes(query: str) -> list[str]:
    """Return the documented explanations that match the query, if any."""
    lowered = query.lower()
    return [note for key, note in KNOWN_ABSENT.items() if key in lowered]


def _render_hit(hit: SearchHit) -> str:
    """Render one search hit as a readable block."""
    entry = hit.entry
    lines = [
        f"{entry.technical_descriptor}",
        f"  Nombre: {entry.name}",
        f"  Categoria: {entry.category}",
    ]
    if entry.description:
        lines.append(f"  Descripcion: {entry.description}")
    detail = []
    if entry.data_type:
        detail.append(f"tipo {entry.data_type}")
    if entry.unit:
        detail.append(f"unidad {entry.unit}")
    if entry.value_range:
        detail.append(f"rango {entry.value_range}")
    detail.append("streamable" if entry.streamable else "no streamable")
    lines.append(f"  Detalle: {', '.join(detail)}")

    if entry.is_in_container:
        lines.append(f"  Estado: incluido en el contenedor '{CONTAINER_NAME}' de este proyecto.")
    elif entry.is_confirmed_for_vehicle:
        lines.append(
            "  Estado: confirmado para este proyecto, pero FUERA del contenedor "
            "(tiene endpoint propio)."
        )
    elif entry.is_electric_only:
        lines.append(
            "  Estado: solo aplica a vehiculos electricos. Este X1 sDrive18i es de gasolina."
        )
    else:
        lines.append(
            "  Estado: existe en el catalogo pero NO esta en el contenedor de este proyecto, "
            "asi que hoy no se lee."
        )
    return "\n".join(lines)


def search_descriptors(
    catalogue: Catalogue,
    query: str,
    *,
    limit: int = 20,
    include_electric: bool = False,
) -> str:
    """Search the local catalogue and render the result as text.

    Accepts Spanish or English. Electric-only descriptors are hidden by default
    because this is a petrol vehicle; `include_electric=True` shows them.
    """
    query = (query or "").strip()
    if not query:
        return (
            "Escribe algo que buscar. El catalogo tiene "
            f"{len(catalogue)} descriptores en {len(catalogue.categories)} categorias."
        )

    result = catalogue.search(query, limit=limit, include_electric=include_electric)
    hits = result.hits
    notes = _absence_notes(query)
    ignored = result.ignored_terms

    header = (
        f"Busqueda en el catalogo telematico local ({len(catalogue)} descriptores). "
        f"Esta herramienta NO gasta cuota de la API."
    )

    if not hits:
        body = [
            f"Sin resultados para '{query}'.",
            "",
            "Que significa: ese descriptor NO existe en el catalogo de BMW. No es que este "
            "vehiculo no lo emita; es que no es un descriptor valido y no se puede pedir.",
        ]
        if ignored:
            body.append(
                "\nNinguna de estas palabras aparece en ningun descriptor del "
                "catalogo: " + ", ".join(ignored) + "."
            )
        body.extend(f"\n{note}" for note in notes)
        if not include_electric:
            body.append(
                "\nNota: los 114 descriptores de vehiculo electrico estan ocultos por "
                "defecto. Repite con include_electric=True si quieres verlos."
            )
        return f"{header}\n\n" + "\n".join(body)

    blocks = [_render_hit(hit) for hit in hits]
    footer = []
    if ignored:
        footer.append(
            "\nSe han ignorado estas palabras, porque no aparecen en ningun "
            "descriptor del catalogo: "
            + ", ".join(ignored)
            + ". La busqueda se ha hecho sin ellas."
        )
    if notes:
        footer.append("")
        footer.extend(notes)
    if not include_electric:
        footer.append(
            "\nNota: los descriptores exclusivos de vehiculo electrico estan excluidos "
            "(include_electric=True para verlos)."
        )
    return (
        f"{header}\n"
        f"{len(hits)} resultado(s) para '{query}', ordenados por relevancia:\n\n"
        + "\n\n".join(blocks)
        + "\n".join(footer)
    )
