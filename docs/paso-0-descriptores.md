# Paso 0: descriptores confirmados

**Fecha de verificacion:** 2026-09-07  
**Catalogo:** `spec/telematic_catalogue.json`, descargado de  
`https://raw.githubusercontent.com/zweckj/bmw-cardata/main/spec/telematic_catalogue.json`  
**SHA-256 del fichero usado:** `74f364c44ff04d60d2d3c44a259068c5c1f9d906a9331c17274b6ecc1b5761d4`  
**Contenido:** 294 descriptores en 8 categorias.

| Categoria | Descriptores |
|---|---|
| BASIC VEHICLE DATA | 6 |
| DATA ON THE VEHICLE STATUS | 130 |
| USAGE-BASED VEHICLE DATA | 16 |
| DATA REGARDING THE EVENTS OF A VEHICLE | 2 |
| ELECTRIC VEHICLE DATA | 114 |
| METADATA | 13 |
| TYRE DATA | 12 |
| INFORMATION ABOUT THE CONNECTEDDRIVE CONTRACT DETAILS | 1 |
| **TOTAL** | **294** |

Los **114 descriptores de `ELECTRIC VEHICLE DATA` quedan descartados**: el
vehiculo es un X1 sDrive18i de gasolina.

> **Que significa aqui "confirmado".** Confirmado = el descriptor existe en el
> catalogo y este proyecto lo usa. El catalogo es generico para toda la gama BMW,
> asi que **no garantiza que este U11 lo emita**. Eso solo se sabe con la primera
> llamada real. Lo que no llegue se documentara como *no disponible para este
> vehiculo*, nunca como inexistente.

---

## Descriptores confirmados

Los 33 descriptores estan verificados uno a uno contra el catalogo. La verificacion es reproducible: `tests/test_descriptors.py`
falla si alguno deja de existir.

### Kilometraje

| Descriptor | Nombre en el catalogo | Tipo | Unidad | Rango | Streamable |
|---|---|---|---|---|---|
| `vehicle.vehicle.travelledDistance` | Mileage | float | - | 0 km to 500000 km or 0 mi to 310686 mi | si |
| `vehicle.vehicle.averageWeeklyDistanceShortTerm` | Average distance per week | float | - | 1 km to 3000 km or 1 mi to 1864 mi | si |
| `vehicle.vehicle.averageWeeklyDistanceLongTerm` | Average distance per week (long-life) | float | - | 1 km to 3000 km or 1 mi to 1864 mi | si |

### CBS / mantenimiento

| Descriptor | Nombre en el catalogo | Tipo | Unidad | Rango | Streamable |
|---|---|---|---|---|---|
| `vehicle.status.serviceDistance.next` | Distance to the next service | uint16 | - | 0 km to 100,000 km or 0 mi to 62,137 mi | si |
| `vehicle.status.serviceTime.inspectionDateLegal` | Date of next inspection | string | - | dd.mm.yyyy hh:mm UTC or mm/dd/yyyy hh:mm UTC | si |
| `vehicle.status.conditionBasedServices` | Condition Based Service | - | - | - | no |
| `vehicle.status.conditionBasedServicesCount` | Number of CBS reports | uint16 | - | 0 to 60 Messages | si |
| `vehicle.status.conditionBasedServicesAverageDistancePerDay` | Average distance per day | int32 | km | - | no |
| `vehicle.status.serviceDistance.yellow` | Distance threshold for service information | uint16 | - | 2000 km or 1243 mi | si |
| `vehicle.status.serviceTime.yellow` | Time threshold for service information | uint16 | weeks | 4 weeks | si |
| `vehicle.status.serviceTime.hUandAuServiceYellow` | Time threshold for main and exhaust gas inspection | uint16 | months | 0 to 10 months | si |
| `vehicle.status.checkControlMessages` | Check control messages | - | - | - | no |

### Neumaticos: presiones, objetivos y temperaturas

| Descriptor | Nombre en el catalogo | Tipo | Unidad | Rango | Streamable |
|---|---|---|---|---|---|
| `vehicle.chassis.axle.row1.wheel.left.tire.pressure` | Measured tyre pressure, front left | uint16 | kPa | 0-1000 kPa or -NA- | si |
| `vehicle.chassis.axle.row1.wheel.right.tire.pressure` | Measured tyre pressure, front right | uint16 | kPa | 0-1000 kPa or -NA- | si |
| `vehicle.chassis.axle.row2.wheel.left.tire.pressure` | Measured tyre pressure, rear left | uint16 | kPa | 0-1000 kPa or -NA- | si |
| `vehicle.chassis.axle.row2.wheel.right.tire.pressure` | Measured tyre pressure, rear right | uint16 | kPa | 0-1000 kPa or -NA- | si |
| `vehicle.chassis.axle.row1.wheel.left.tire.pressureTarget` | Target tyre pressure, front left | uint16 | kPa | 0-1000 kPa or -NA- | si |
| `vehicle.chassis.axle.row1.wheel.right.tire.pressureTarget` | Target tyre pressure, front right | uint16 | kPa | 0-1000 kPa or -NA- | si |
| `vehicle.chassis.axle.row2.wheel.left.tire.pressureTarget` | Target tyre pressure, rear left | uint16 | kPa | 0-1000 kPa or -NA- | si |
| `vehicle.chassis.axle.row2.wheel.right.tire.pressureTarget` | Target tyre pressure, rear right | uint16 | kPa | 0-1000 kPa or -NA- | si |
| `vehicle.chassis.axle.row1.wheel.left.tire.temperature` | Tire temperature in Celsius, front left | float | Celsius | 0 °C to 50 °C | si |
| `vehicle.chassis.axle.row1.wheel.right.tire.temperature` | Tire temperature in Celsius, front right | float | Celsius | 0 °C to 50 °C | si |
| `vehicle.chassis.axle.row2.wheel.left.tire.temperature` | Tire temperature in Celsius, rear left | float | Celsius | 0 °C to 50 °C | si |
| `vehicle.chassis.axle.row2.wheel.right.tire.temperature` | Tire temperature in Celsius, rear right | float | Celsius | 0 °C to 50 °C | si |

Unidad **kPa** (0-1000). El valor `-NA-` es una respuesta legitima y significa
**"sin medida"**: no se convierte a 0 bar bajo ningun concepto. Al usuario se le
presenta en bar, junto al diferencial contra `pressureTarget`.

### Diagnostico de neumaticos (endpoint dedicado)

| Descriptor | Nombre en el catalogo | Tipo | Unidad | Rango | Streamable |
|---|---|---|---|---|---|
| `vehicle.chassis.axle.wheel.tire.diagnosis` | Smart Maintenance: Digital tyre diagnostics | - | - | Details can be found in the corresponding Swagger documen... | no |

No es streamable y su rango en el catalogo remite al swagger de
`/smartMaintenanceTyreDiagnosis`. **Ese endpoint no devuelve presiones**: da
desgaste (`tyreWear.dueMileage`), defectos, dimensiones, fecha de montaje y de
fabricacion, temporada, runflat, fabricante y dibujo. Presiones y diagnostico son
dos fuentes distintas. Por eso este descriptor queda **fuera** del contenedor.

### Bateria de 12V

| Descriptor | Nombre en el catalogo | Tipo | Unidad | Rango | Streamable |
|---|---|---|---|---|---|
| `vehicle.electricalSystem.battery.stateOfCharge` | Low-voltage battery | - | % | - | si |
| `vehicle.electricalSystem.battery.stateOfChargePlausibility` | Low-voltage battery plausibility | - | - | - | no |
| `vehicle.electricalSystem.battery.voltage` | Battery voltage | float | V | 5 V to 20 V | si |
| `vehicle.electricalSystem.battery.serviceDemand.recharge` | Necessity for recharging of the battery | - | - | 1 0 | no |
| `vehicle.electricalSystem.battery.serviceDemand.replace` | Health status of the battery | - | - | 200 140 110 80 | no |

`serviceDemand.replace` es un **codigo de salud**, no un porcentaje:
200 adecuada / 140 limitada / 110 inadecuada / 80 degradada.
`serviceDemand.recharge`: 1 = recarga necesaria, 0 = no.

### Contexto de uso

| Descriptor | Nombre en el catalogo | Tipo | Unidad | Rango | Streamable |
|---|---|---|---|---|---|
| `vehicle.vehicle.deepSleepModeActive` | Deep Sleep Mode | boolean | - | true, false | si |
| `vehicle.drivetrain.engine.isIgnitionOn` | Status of engine (on/off) | boolean | - | ASN_isFalse, ASN_isTrue, ASN_isUnknown | si |
| `vehicle.drivetrain.engine.isActive` | State of ignition | boolean | - | ASN_isFalse, ASN_isTrue, ASN_isUnknown | si |

`deepSleepModeActive` es especialmente relevante para este vehiculo: pasa semanas
parado en cochera, y el sueno profundo apaga la mayoria de funciones telematicas.
Es la hipotesis alternativa a la de la bateria para la ausencia de actualizaciones,
y explica lecturas con timestamps antiguos.

`isIgnitionOn` e `isActive` usan los valores tri-estado `ASN_isTrue`,
`ASN_isFalse` y `ASN_isUnknown`. `ASN_isUnknown` no es `false`.

---

## Lo que NO existe

Verificado por busqueda exhaustiva en el catalogo. **Esta prohibido implementar
nada sobre estos supuestos.**

| Supuesto | Realidad |
|---|---|
| Version de software del vehiculo | **No hay ningun descriptor.** Ni iStep, ni version, ni nada relacionado con Remote Software Upgrade. |
| Estado / disponibilidad / historial de actualizaciones de software | **No existe ningun descriptor.** |
| Estado de las luces de emergencia | **No existe.** |
| Inclinacion o pendiente de aparcamiento | **No existe.** |

Lo unico disponible en esa direccion es **`puStep`**, en `/basicData`: es el
**paso de actualizacion de producto**, y probablemente no cambia con cada RSU. Lo
expone `report_product_update_step`, etiquetado explicitamente como lo que es. No
existe `get_software_version` y no se va a escribir.

Consecuencia directa para `diagnose_software_update`: de las tres condiciones que
BMW documenta para no ofrecer una RSU (carga baja de la bateria de 12V, luces de
emergencia puestas al apagar, aparcar con mas de un 12% de inclinacion),
**CarData solo permite observar una**. La herramienta debe decirlo literalmente.

---

## El contenedor `pitwall-maintenance`

Un solo contenedor con **32 descriptores**: los
33 confirmados menos `vehicle.chassis.axle.wheel.tire.diagnosis`,
que tiene endpoint propio y que `/telematicData` no devuelve.

Con un TTL de 12 h, una sola peticion trae kilometraje, CBS, bateria, presiones y
estado de sueno: **2 peticiones al dia** de las 20 del tope local.

**RESUELTO el 2026-09-07 con un `POST` real:** BMW acepto los 32 descriptores
de una vez, sin limite de tamano. No hay que partir el contenedor, y cada
lectura del contenedor cuesta **1 peticion**, como estaba presupuestado.
El `containerId` que devuelve no es un UUID: son 13 caracteres alfanumericos.

Para verlo exactamente como se enviaria, sin enviar nada:

```
python scripts/bootstrap_containers.py --dry-run
```

---

## Rarezas del catalogo

- `vehicle.vehicle.travelledDistance` trae la **cadena literal `"null"`** como
  unidad, en vez de un `null` de JSON. El cargador lo normaliza para que ninguna
  salida imprima "unidad null".
- El swagger oficial tipa `GET /customers/vehicles/mappings` como un **objeto
  unico**, pero el endpoint real devuelve un **array**. La libreria lo documenta
  y acepta ambas formas; los fixtures cubren las dos.
- **El limite de 50 peticiones/24 h y el codigo `CU-429` no aparecen en el
  swagger.** Vienen de la documentacion B2C de BMW, no del contrato de la API.
  Se tratan como restriccion externa no verificada, y por eso el tope local es
  conservador (20).
- El aviso del swagger sobre "claves ligadas a endpoints dedicados" es
  **generico**: aparece en `POST /containers`, `GET /telematicData` y
  `GET /containers/{id}`, remite a la Integration Guide cap. 3.3.2 (que no
  tenemos) y **no nombra ningun descriptor**. Que
  `vehicle.chassis.axle.wheel.tire.diagnosis` sea uno de ellos es deduccion
  razonable; para `vehicle.status.conditionBasedServices` es solo sospecha.

---

## Lo aprendido de la primera lectura real (2026-09-07)

Una sola llamada a `/telematicData`. Fixture: `tests/fixtures/telematic_real.json`.

### Llegan las 32 claves, pero solo 21 con valor

Once descriptores vinieron con `"value": null` y `"timestamp": null`, conservando
su unidad. Son **tres estados distintos**, y confundirlos seria mentir:

| Estado | Significa |
|---|---|
| Con valor | lectura real |
| **Presente y vacio** (`value: null`) | el vehiculo conoce el campo y no tiene lectura |
| `-NA-` | el vehiculo dice explicitamente "sin medida" |
| Ausente | la clave no viene en la respuesta |

Vacios en esta lectura: las cuatro `tire.temperature`, `battery.stateOfCharge`,
`battery.stateOfChargePlausibility`, `deepSleepModeActive`, `isIgnitionOn`,
`isActive`, `checkControlMessages` y
`conditionBasedServicesAverageDistancePerDay`.

**Correccion del 2026-09-13:** `checkControlMessages` llego con valor en cuanto
el coche tuvo un aviso (pastillas de freno). Su vacio no era "no lo emite". La
estructura del aviso esta en `CLAUDE.md`.

**Duele especialmente** que `deepSleepModeActive` y `stateOfCharge` esten vacios:
son dos de las tres senales que `diagnose_software_update` necesita.

### `conditionBasedServices` llega, y es JSON dentro de una cadena

La sospecha de que estuviera ligado a un endpoint dedicado era **falsa**. Pero su
`value` es una **cadena** que hay que decodificar por segunda vez con
`json.loads`. Dentro, un array de objetos con `date`, `description`, `id`,
`messageType`, `status`, `text`, `title`, `unitOfLengthRemaining`.

`id` es entero; todo lo demas son cadenas, kilometros incluidos. Con centinelas:

- `"date": "null"` — la cadena literal, no un `null` de JSON.
- `"unitOfLengthRemaining": "-"` cuando la partida solo tiene fecha.

Partidas de este U11: `Front Brake` (2), `Engine oil` (1), `Brake fluid` (3),
`Statutory vehicle inspection` (32), `Vehicle check` (100).

### El contador de CBS no cuenta avisos

`conditionBasedServicesCount` devolvio **9** con **5** partidas en el array, y
durante dias se trato como una discrepancia sin explicar. **No lo es. Resuelto el
2026-09-22 leyendo el propio catalogo**, que lo define como *"the maximum number
of service notifications transmitted from the vehicle to BMW via telematics"*, y
anade que el numero realmente transmitido varia y que **no todos los avisos CBS
del coche se transfieren**. Los 5 del array son los transmitidos; el 9 es el tope
de ese vehiculo. Las herramientas dan los dos numeros y ahora explican cual es
cual.

La leccion es mas util que el dato: **el catalogo ya traia la respuesta**, y se
tardo en encontrarla por buscarla en el swagger, que de estos campos no dice
nada.

### Desfase de reloj

Un `timestamp` venia un minuto en el futuro. Los calculos de antiguedad toleran
valores negativos y los presentan como "dentro de", no como "hace".

---

## Como reproducir esta verificacion

```
python scripts/refresh_catalogue.py --check   # compara con el catalogo remoto
python -m pytest tests/test_descriptors.py    # falla si algo dejo de existir
```
