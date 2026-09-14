"""The confirmed technical descriptors for this vehicle, in one place.

Every descriptor here was verified to exist in `spec/telematic_catalogue.json`
(294 descriptors, 8 categories). The catalogue is generic for the whole BMW
range: existing there does NOT prove this particular U11 emits it. That is only
confirmed by a real call, and anything that never arrives gets documented as
"not available for this vehicle", never as "nonexistent".

Nothing outside this list may be turned into a tool. See CLAUDE.md, rule 5.
"""

from __future__ import annotations

from typing import Final

# --- Mileage ---------------------------------------------------------------
TRAVELLED_DISTANCE: Final = "vehicle.vehicle.travelledDistance"
AVG_WEEKLY_DISTANCE_SHORT: Final = "vehicle.vehicle.averageWeeklyDistanceShortTerm"
AVG_WEEKLY_DISTANCE_LONG: Final = "vehicle.vehicle.averageWeeklyDistanceLongTerm"

MILEAGE_DESCRIPTORS: Final[tuple[str, ...]] = (
    TRAVELLED_DISTANCE,
    AVG_WEEKLY_DISTANCE_SHORT,
    AVG_WEEKLY_DISTANCE_LONG,
)

# --- Condition Based Services / maintenance --------------------------------
SERVICE_DISTANCE_NEXT: Final = "vehicle.status.serviceDistance.next"
INSPECTION_DATE_LEGAL: Final = "vehicle.status.serviceTime.inspectionDateLegal"
CONDITION_BASED_SERVICES: Final = "vehicle.status.conditionBasedServices"
CBS_COUNT: Final = "vehicle.status.conditionBasedServicesCount"
CBS_AVERAGE_DISTANCE_PER_DAY: Final = "vehicle.status.conditionBasedServicesAverageDistancePerDay"
SERVICE_DISTANCE_YELLOW: Final = "vehicle.status.serviceDistance.yellow"
SERVICE_TIME_YELLOW: Final = "vehicle.status.serviceTime.yellow"
HU_AU_SERVICE_YELLOW: Final = "vehicle.status.serviceTime.hUandAuServiceYellow"
CHECK_CONTROL_MESSAGES: Final = "vehicle.status.checkControlMessages"

MAINTENANCE_DESCRIPTORS: Final[tuple[str, ...]] = (
    SERVICE_DISTANCE_NEXT,
    INSPECTION_DATE_LEGAL,
    CONDITION_BASED_SERVICES,
    CBS_COUNT,
    CBS_AVERAGE_DISTANCE_PER_DAY,
    SERVICE_DISTANCE_YELLOW,
    SERVICE_TIME_YELLOW,
    HU_AU_SERVICE_YELLOW,
    CHECK_CONTROL_MESSAGES,
)

# --- Tyres -----------------------------------------------------------------
# Positions as BMW names them: row1 = front axle, row2 = rear axle.
WHEEL_POSITIONS: Final[tuple[tuple[str, str, str], ...]] = (
    ("row1", "left", "Delantera izquierda"),
    ("row1", "right", "Delantera derecha"),
    ("row2", "left", "Trasera izquierda"),
    ("row2", "right", "Trasera derecha"),
)


def tyre_descriptor(row: str, side: str, measure: str) -> str:
    """Build a tyre descriptor from axle row, side and measurement name."""
    return f"vehicle.chassis.axle.{row}.wheel.{side}.tire.{measure}"


TYRE_PRESSURE_DESCRIPTORS: Final[tuple[str, ...]] = tuple(
    tyre_descriptor(row, side, "pressure") for row, side, _ in WHEEL_POSITIONS
)
TYRE_PRESSURE_TARGET_DESCRIPTORS: Final[tuple[str, ...]] = tuple(
    tyre_descriptor(row, side, "pressureTarget") for row, side, _ in WHEEL_POSITIONS
)
TYRE_TEMPERATURE_DESCRIPTORS: Final[tuple[str, ...]] = tuple(
    tyre_descriptor(row, side, "temperature") for row, side, _ in WHEEL_POSITIONS
)

TYRE_DESCRIPTORS: Final[tuple[str, ...]] = (
    *TYRE_PRESSURE_DESCRIPTORS,
    *TYRE_PRESSURE_TARGET_DESCRIPTORS,
    *TYRE_TEMPERATURE_DESCRIPTORS,
)

# Not streamable, and its catalogue entry points at the dedicated
# /smartMaintenanceTyreDiagnosis endpoint. /telematicData does not return it,
# so it is deliberately kept OUT of the container (see CONTAINER_DESCRIPTORS).
TYRE_DIAGNOSIS: Final = "vehicle.chassis.axle.wheel.tire.diagnosis"

# --- 12V battery -----------------------------------------------------------
BATTERY_STATE_OF_CHARGE: Final = "vehicle.electricalSystem.battery.stateOfCharge"
BATTERY_SOC_PLAUSIBILITY: Final = "vehicle.electricalSystem.battery.stateOfChargePlausibility"
BATTERY_VOLTAGE: Final = "vehicle.electricalSystem.battery.voltage"
BATTERY_SERVICE_RECHARGE: Final = "vehicle.electricalSystem.battery.serviceDemand.recharge"
BATTERY_SERVICE_REPLACE: Final = "vehicle.electricalSystem.battery.serviceDemand.replace"

BATTERY_DESCRIPTORS: Final[tuple[str, ...]] = (
    BATTERY_STATE_OF_CHARGE,
    BATTERY_SOC_PLAUSIBILITY,
    BATTERY_VOLTAGE,
    BATTERY_SERVICE_RECHARGE,
    BATTERY_SERVICE_REPLACE,
)

# --- Usage context ---------------------------------------------------------
DEEP_SLEEP_MODE_ACTIVE: Final = "vehicle.vehicle.deepSleepModeActive"
IGNITION_ON: Final = "vehicle.drivetrain.engine.isIgnitionOn"
ENGINE_ACTIVE: Final = "vehicle.drivetrain.engine.isActive"

# isIgnitionOn arrives empty on this car; isMoving was added hoping it would
# tell a charging voltage from a resting one. It arrived empty too (2026-09-14).
IS_MOVING: Final = "vehicle.isMoving"

CONTEXT_DESCRIPTORS: Final[tuple[str, ...]] = (
    DEEP_SLEEP_MODE_ACTIVE,
    IGNITION_ON,
    ENGINE_ACTIVE,
    IS_MOVING,
)

# --- Fuel ------------------------------------------------------------------
# Added on 2026-09-14 and seen arriving that same day: what the official app
# does not show. The litres come with a null unit and, per the catalogue, up to
# 6 L of float error. The OBFCM pair arrives stamped 30 Oct 2024: a lifetime
# figure refreshed at the workshop, not today's consumption.
FUEL_LEVEL: Final = "vehicle.drivetrain.fuelSystem.level"
FUEL_REMAINING: Final = "vehicle.drivetrain.fuelSystem.remainingFuel"
REMAINING_RANGE: Final = "vehicle.cabin.infotainment.navigation.remainingRange"
LAST_REMAINING_RANGE: Final = "vehicle.drivetrain.lastRemainingRange"
OBFCM_FUEL: Final = "vehicle.drivetrain.fuelSystem.consumptionOverLifeTime.overall.fuel"
OBFCM_DISTANCE: Final = (
    "vehicle.drivetrain.fuelSystem.consumptionOverLifeTime.overall.referenceDistance"
)

FUEL_DESCRIPTORS: Final[tuple[str, ...]] = (
    FUEL_LEVEL,
    FUEL_REMAINING,
    REMAINING_RANGE,
    LAST_REMAINING_RANGE,
    OBFCM_FUEL,
    OBFCM_DISTANCE,
)

# --- Diagnostics -----------------------------------------------------------
# The fault memory arrives as XML inside the string: ECU address and code, no
# status, no date, no description. The codes are the manufacturer's and are not
# in the catalogue: they are counted and compared, never translated.
FAULT_MEMORY: Final = "vehicle.electronicControlUnit.diagnosticTroubleCodes.raw"
COOLANT_TEMPERATURE: Final = "vehicle.drivetrain.internalCombustionEngine.engine.ect"
# Not streamable and empty on the first read: possibly bound to an endpoint of
# its own, like the tyre diagnosis.
LIVE_DIAGNOSTICS: Final = "vehicle.serviceDemand.defect.id"

DIAGNOSTIC_DESCRIPTORS: Final[tuple[str, ...]] = (
    FAULT_MEMORY,
    COOLANT_TEMPERATURE,
    LIVE_DIAGNOSTICS,
)

#: Stamped 30 Oct 2024 and not used by the maintenance tools: kept out of their
#: "oldest datum used", which would otherwise report a date they never read.
FROZEN_LIFETIME_DESCRIPTORS: Final[tuple[str, ...]] = (OBFCM_FUEL, OBFCM_DISTANCE)

# --- The container ---------------------------------------------------------
CONTAINER_NAME: Final = "pitwall-maintenance"
CONTAINER_PURPOSE: Final = (
    "Read-only maintenance overview: mileage, CBS, 12V battery, tyre pressures, "
    "sleep state, fuel and fault memory."
)

#: Everything a single /telematicData call must bring back. One fat container,
#: so one request covers mileage, CBS, battery, pressures and sleep state.
CONTAINER_DESCRIPTORS: Final[tuple[str, ...]] = (
    *MILEAGE_DESCRIPTORS,
    *MAINTENANCE_DESCRIPTORS,
    *TYRE_DESCRIPTORS,
    *BATTERY_DESCRIPTORS,
    *CONTEXT_DESCRIPTORS,
    *FUEL_DESCRIPTORS,
    *DIAGNOSTIC_DESCRIPTORS,
)

#: Every confirmed descriptor, including the ones that live outside the
#: container. Used by tests and documentation, not to build requests.
ALL_CONFIRMED_DESCRIPTORS: Final[tuple[str, ...]] = (*CONTAINER_DESCRIPTORS, TYRE_DIAGNOSIS)

# --- On trial: in the catalogue, never yet seen from this car --------------
# Asked for by the bootstrap script on top of the container, used by no tool,
# and never reported as missing. A descriptor leaves the trial only after
# arriving in a real read that is recorded as a fixture. The ten tried on
# 2026-09-14 were promoted into the fuel, diagnostics and context groups above.
TRIAL_DESCRIPTORS: Final[tuple[str, ...]] = ()

# --- Values that are NOT numbers -------------------------------------------
#: BMW's explicit "no measurement". Must never be read as zero.
NO_MEASUREMENT: Final = "-NA-"

#: Tri-state booleans used by the engine descriptors.
ASN_TRUE: Final = "ASN_isTrue"
ASN_FALSE: Final = "ASN_isFalse"
ASN_UNKNOWN: Final = "ASN_isUnknown"

#: `serviceDemand.replace` is a coded health value, not a percentage.
BATTERY_REPLACE_CODES: Final[dict[str, str]] = {
    "200": "adecuada",
    "140": "limitada",
    "110": "inadecuada",
    "80": "degradada",
}
