"""Composite tools: maintenance summary and the software-update diagnosis."""

from __future__ import annotations

from ..config import Settings
from .pending import pending


def get_maintenance_summary(settings: Settings) -> str:
    """One pasteable block: mileage, CBS, pressures and the oldest datum used."""
    return pending(
        "get_maintenance_summary",
        settings,
        needs_vin=True,
        needs_container=True,
        note=(
            "Cuando funcione devolvera un bloque de texto listo para pegar en un prompt: "
            "kilometraje, cada partida CBS disponible con km y fecha restantes, las "
            "presiones de las cuatro ruedas en bar contra su objetivo, y la fecha del dato "
            "mas antiguo utilizado.\n"
            "Reserva heredada de get_vehicle_status: si el desglose CBS por partida no "
            "llega, lo dira explicitamente y dara solo el valor global de "
            "vehicle.status.serviceDistance.next. Una presion con valor -NA- se mostrara "
            "como 'sin medida', nunca como 0 bar."
        ),
    )


def diagnose_software_update(settings: Settings) -> str:
    """Bounded verdict on why no Remote Software Upgrade has arrived."""
    return pending(
        "diagnose_software_update",
        settings,
        needs_vin=True,
        needs_container=True,
        note=(
            "LIMITE DEL DIAGNOSTICO, que la herramienta dira literalmente en su salida: "
            "BMW documenta tres condiciones por las que no se ofrece la instalacion de una "
            "RSU (estado de carga bajo de la bateria de 12V, luces de emergencia puestas al "
            "apagar el motor, y aparcar con mas de un 12% de inclinacion). DE ESAS TRES, "
            "CARDATA SOLO PERMITE OBSERVAR UNA: la bateria. Sobre las otras dos solo puede "
            "declarar 'no observable por CarData'. No existe descriptor de luces de "
            "emergencia ni de inclinacion, y esta prohibido insinuar una conclusion mas "
            "fuerte que la evidencia.\n"
            "El informe incluira: puStep actual y fecha del ultimo cambio detectado en el "
            "historico local, serie de stateOfCharge y voltage de la bateria de 12V con su "
            "tendencia, serviceDemand.recharge, deepSleepModeActive, kilometraje, y la fecha "
            "del dato mas reciente y del mas antiguo.\n"
            "EL VALOR ESTA EN LA SERIE, NO EN LA FOTO. Con un solo punto no concluira: dira "
            "cuantas lecturas tiene, desde cuando, y que le falta para poder pronunciarse."
        ),
    )
