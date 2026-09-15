# pitwall-mcp

Servidor MCP local de **solo lectura** sobre la API BMW CarData, para un
BMW X1 sDrive18i (U11, gasolina, España). Proyecto personal de Panesoft,
destinado a publicarse como open source bajo licencia MIT.

**No afiliado a BMW AG.** "BMW" y "CarData" son marcas de sus respectivos
propietarios. El nombre del proyecto no invoca la marca BMW y no debe
hacerlo en ningún fichero, paquete ni descripción.

---

## Reglas no negociables

1. **Solo lectura sobre el vehículo.** El servidor MCP no expone ninguna
   herramienta que escriba, mande, active o insinúe que puede actuar
   sobre el coche. CarData es una API de lectura; el servidor no puede
   lanzar, descargar ni forzar una actualización de software.
2. **La creación de contenedores vive fuera del servidor.**
   `POST/DELETE /customers/containers` solo se ejecuta desde
   `scripts/bootstrap_containers.py`, lanzado a mano por el usuario. El
   servidor MCP consume el `container_id` que queda en `.env` y nunca
   crea ni borra nada.
3. **Ninguna herramienta dispara una petición REST sin pasar por caché.**
   La REST está limitada a **50 peticiones / 24 h por cuenta**; superarla
   devuelve HTTP 403 `CU-429` hasta el día siguiente. Toda llamada, sin
   excepción (incluidas las de containers), incrementa el contador.
4. **Toda respuesta indica su procedencia**: timestamp del dato, timestamp
   de la lectura, y si viene de caché, de la API o del histórico local.
5. **El catálogo telemático es la única fuente de verdad para
   descriptores.** No se inventan, no se deducen por analogía, no se
   copian de documentación de terceros. Si un descriptor no está en
   `spec/telematic_catalogue.json`, no existe.
6. **Si un dato no existe, la herramienta lo dice explícitamente.** Nunca
   devuelve `null` a secas ni un valor simulado. El mensaje debe decir
   *por qué* no existe: no está en el catálogo, no lo emite este
   vehículo, no está en el contenedor, o no hay lectura reciente.
7. **Cero credenciales en código o en git.** Todo por `.env` y ficheros de
   token fuera del repo. `.gitignore` desde el primer commit.
8. **Ningún test hace llamadas reales.** Todos contra fixtures grabados.
   Una suite que gaste cuota es un bug.
9. **`bimmer_connected` está muerta y archivada.** Ignorar cualquier
   documentación, ejemplo o patrón basado en ella.
10. **Documentación en español, código en inglés** (identificadores,
    comentarios, docstrings, mensajes de log). Los mensajes de error
    dirigidos al usuario final van en español.

---

## Stack

- Python 3.12+, async
- MCP SDK oficial, transporte **stdio**
- `bmw-cardata==0.1.0a3` (PyPI, de `zweckj`) — **versión alfa, pinneada
  exacta**. Se envuelve siempre en `cardata/client.py`; ningún módulo de
  `tools/` importa la librería directamente. Si su API cambia, solo se
  toca el adaptador.
- SQLite (stdlib) para caché, cuota e histórico
- `pytest` + `pytest-asyncio`

---

## API real (verificada contra el swagger oficial)

`https://api-cardata.bmwgroup.com`, header `x-version: v1` obligatorio en
todas las rutas, `bearerAuth` con access token GCDM.

| Ruta | Uso |
|---|---|
| `GET /customers/vehicles/mappings` | lista de VINs de la cuenta (`vin`, `mappedSince`, `mappingType: PRIMARY\|SECONDARY`) |
| `GET /customers/vehicles/{vin}/basicData` | datos del vehículo, incluye `puStep`, `modelRange`, `series`, `engine`, `constructionDate`, `fullSAList` |
| `GET /customers/vehicles/{vin}/telematicData?containerId=` | datos telemáticos; **`containerId` es obligatorio** |
| `GET /customers/vehicles/{vin}/smartMaintenanceTyreDiagnosis` | diagnóstico de neumáticos (desgaste, defectos, dimensiones, montaje) |
| `GET /customers/vehicles/{vin}/image` | imagen del vehículo |
| `GET/POST /customers/containers`, `GET/DELETE /customers/containers/{id}` | gestión de contenedores — **solo desde el script de bootstrap** |

No se usan `chargingHistory` ni `locationBasedChargingSettings`: son de
vehículo eléctrico.

**Aviso del propio swagger:** algunas claves están ligadas a endpoints
dedicados; se pueden añadir a un contenedor pero `/telematicData` no las
devuelve. Confirmado para `vehicle.chassis.axle.wheel.tire.diagnosis`.
Sospechoso para `vehicle.status.conditionBasedServices` — ver riesgos.

---

## Paso 0: descriptores confirmados

Fuente: `spec/telematic_catalogue.json` (294 descriptores, 8 categorías),
descargado de
`https://raw.githubusercontent.com/zweckj/bmw-cardata/main/spec/telematic_catalogue.json`.

El catálogo es genérico para toda la gama BMW. Que un descriptor exista
aquí **no garantiza** que este U11 lo emita; eso solo se confirma con la
primera llamada real. Los 114 descriptores de la categoría
`ELECTRIC VEHICLE DATA` quedan descartados por ser un vehículo de
gasolina.

### Kilometraje

| Descriptor | Valor | Streamable |
|---|---|---|
| `vehicle.vehicle.travelledDistance` | kilometraje total, float, 0–500.000 km | sí |
| `vehicle.vehicle.averageWeeklyDistanceShortTerm` | media km/semana | sí |
| `vehicle.vehicle.averageWeeklyDistanceLongTerm` | media km/semana (larga duración) | sí |

### CBS / mantenimiento

| Descriptor | Valor | Streamable |
|---|---|---|
| `vehicle.status.serviceDistance.next` | km hasta el próximo servicio, uint16 | sí |
| `vehicle.status.serviceTime.inspectionDateLegal` | fecha de la próxima inspección legal | sí |
| `vehicle.status.conditionBasedServices` | objeto CBS completo, **estructura no documentada** | no |
| `vehicle.status.conditionBasedServicesCount` | nº de avisos CBS, 0–60 | sí |
| `vehicle.status.conditionBasedServicesAverageDistancePerDay` | media km/día en contexto CBS | no |
| `vehicle.status.serviceDistance.yellow` | umbral de preaviso, 2.000 km | sí |
| `vehicle.status.serviceTime.yellow` | umbral de preaviso, 4 semanas | sí |
| `vehicle.status.serviceTime.hUandAuServiceYellow` | umbral de preaviso ITV, meses | sí |
| `vehicle.status.checkControlMessages` | mensajes Check Control | no |

**Estructura de `conditionBasedServices`, verificada el 2026-09-07 con una
llamada real.** Sí llega por `/telematicData`; la sospecha de que estuviera
ligada a un endpoint dedicado era falsa.

Su `value` es **una cadena que contiene JSON**: hace falta un **segundo
`json.loads`** para llegar a un array de partidas con estas claves:

```json
{"date": "2027-07", "description": "Next service due when...", "id": 1,
 "messageType": "CBS", "status": "OK", "title": "Engine oil",
 "text": "-", "unitOfLengthRemaining": "14000"}
```

`id` es entero; **todo lo demás son cadenas**, incluidos los kilómetros. Tres
valores centinela que no se pueden tratar como datos:

- **`"date": "null"`** — la cadena literal de cuatro letras, no un `null` de
  JSON. Parsearla como fecha produce basura.
- **`"unitOfLengthRemaining": "-"`** cuando la partida solo tiene fecha.
- **`"text": "-"`** cuando no hay texto.

Partidas observadas en este U11: `Front Brake` (id 2), `Statutory vehicle
inspection` (id 32), `Engine oil` (id 1), `Brake fluid` (id 3), `Vehicle check`
(id 100).

**La API recorta la fecha al mes. Verificado el 2026-09-13 contra la pantalla del
coche:** el iDrive muestra "Aceite del motor 11.07.2027, en 14000 km" y la API da
`"date": "2027-07"`. El día existe en el coche pero CarData no lo manda; las
herramientas no pueden dar más precisión que el mes. Kilómetros y estado sí
coinciden con la pantalla ("Pastillas freno del. en 1900 km", ITV 17.01.2027), y
el Check Control del coche ("Es preciso sustituir las pastillas de freno") es el
mismo aviso que la API da en inglés.

**`conditionBasedServices` también puede llegar vacío. Verificado el
2026-09-14:** en la lectura de las 14:20 UTC, con el coche recién aparcado tras
el viaje, llegó con `value: null` y `timestamp: null`. En la de 35 minutos antes
había llegado con valor. Ese día los vacíos fueron 11. Las herramientas lo
declaran ("Desglose CBS: no disponible") y se quedan con la cifra global. En esa
lectura no hay previsión, porque no hay partidas de las que calcularla.

**Discrepancia sin explicar:** `conditionBasedServicesCount` devolvió **9**
mientras el array traía **5** partidas. Se desconoce el motivo. Las herramientas
dan los dos números y **no fingen que cuadran**. El 2026-09-13 seguía igual: 9
contra 5.

**`serviceDistance.next` no es la partida más urgente. Verificado el
2026-09-13.** El 7 de septiembre valía 2140 con los frenos delanteros a 2100 km
en `OK`. Tras 157 km, los frenos pasaron a **1900 km en `PENDING`** y
`serviceDistance.next` saltó a **13560**, una cifra que no coincide con ninguna
partida. Dar la cifra global como "próximo servicio" habría escondido lo único
urgente. Las herramientas añaden una línea `OJO` con toda partida que BMW no
marque `OK`. No se inventa de dónde sale el 13560.

**Estructura de `checkControlMessages`, verificada el 2026-09-13**, la primera
vez que llegó con valor. Mismo truco que CBS (JSON dentro de una cadena, segundo
`json.loads`) y mismas claves, pero **ni los mismos significados ni los mismos
centinelas**:

```json
{"date": null, "description": null, "id": 907, "messageType": "CCM",
 "status": "NULL", "title": null, "text": "The brake pads need to be replaced.",
 "unitOfLengthRemaining": "48376"}
```

- `date`, `description` y `title` llegan como `null` **de JSON**, no como la
  cadena `"null"` de CBS. `status` sí llega como cadena: `"NULL"`, en mayúsculas.
- **`unitOfLengthRemaining` no es una distancia restante.** Valía 48376 mientras
  el cuentakilómetros pasaba de 48.283 (8-sep) a 48.440 (13-sep) y CBS daba a
  esos mismos frenos 1.900 km. Se trata como el kilometraje al que se refiere el
  aviso. BMW no documenta el campo; es una lectura, no una definición.
- Encaja con CBS: con 2.100 km restantes a los 48.283, los frenos cruzan el
  umbral de preaviso de 2.000 km hacia los 48.383, y el aviso se registró a los
  48.376.
- **Corregido el 2026-09-14: tampoco es el kilómetro en que saltó el aviso.** El
  mismo aviso (id 907, mismo texto) volvió con **48712**, el cuentakilómetros de
  ese momento. Es el kilometraje de la última vez que el coche envió el aviso,
  así que el ajuste con CBS del punto anterior fue casualidad. La herramienta
  dice ahora "último envío del coche a los X km".
- **Corregido otra vez el 2026-09-14 a las 19:57 UTC: tampoco es eso.** Con el
  coche a 48.713 km, el mismo aviso volvió con **48376**. La serie es 48376,
  48712, 48376: no crece, y no se sabe qué es. Van dos interpretaciones y las dos
  han caído. **No se interpreta más.** La herramienta enseña el campo pelado
  ("campo de km del aviso"), dice que BMW no lo documenta y que ha cambiado
  entre lecturas.

### Neumáticos

Presiones y temperaturas, las ocho + cuatro, todas streamable:

- `vehicle.chassis.axle.row1.wheel.left.tire.pressure` (+ `.right`, `row2`)
- `vehicle.chassis.axle.row1.wheel.left.tire.pressureTarget` (+ ídem)
- `vehicle.chassis.axle.row1.wheel.left.tire.temperature` (+ ídem)

Unidad **kPa** (0–1000), valor posible `-NA-` que debe tratarse
explícitamente como "sin medida", nunca como cero. Se presenta al usuario
en bar, junto al diferencial contra `pressureTarget`.

**`pressureTarget` NO es una constante. Verificado el 2026-09-08:** el objetivo
del eje delantero pasó de 250 a 260 kPa entre dos lecturas separadas diez
minutos, mientras el trasero se quedó en 250. Los ejes pueden tener objetivos
distintos, y el objetivo cambia con el uso. Comparar una presión fresca contra
un objetivo cacheado de otro momento da un diferencial falso: el objetivo se lee
siempre de la misma respuesta que la presión.

El 2026-09-13 volvió a moverse: **280 kPa delante y 270 detrás** (antes 260 y
250), con presiones de 270/270/250/260. No se sabe qué lo mueve.

**2026-09-14, al terminar un tramo de 272 km:** presiones 290/290/270/270 y
objetivo 290/290/280/280. El objetivo subió a la vez que las presiones, que
suben con el neumático caliente. **Es compatible con un objetivo compensado por
temperatura**, pero no está demostrado: las cuatro `tire.temperature` siguen
llegando vacías, incluso tras horas de autovía.

**2026-09-14 a las 19:56 UTC, en frío tras seis horas aparcado:** el objetivo
bajó a **250 kPa en las cuatro ruedas** (a las 13:47 estaba en 290/290/280/280),
con presiones de 260/260/250/250. Es el mismo 250 de la lectura en frío del 7
de septiembre. **El objetivo compensado por temperatura queda casi confirmado**:
250 en frío, hasta 290 en caliente. No llega a confirmarse del todo porque la
temperatura sigue sin llegar.

### Tercer estado: presente y vacío

Verificado el 2026-09-07: el contenedor devolvió **las 32 claves pedidas, pero
solo 21 con valor**. Las otras 11 llegan con `"value": null` y
`"timestamp": null`, conservando su `unit`.

Son **tres estados distintos**, y confundirlos es mentir:

1. **Ausente**: la clave no está en la respuesta.
2. **Presente y vacío**: `value: null`. El vehículo conoce el campo pero no
   tiene lectura.
3. **`-NA-`**: el vehículo dice explícitamente "sin medida".

En esta lectura llegaron vacíos: las cuatro `tire.temperature`,
`battery.stateOfCharge`, `battery.stateOfChargePlausibility`,
`deepSleepModeActive`, `isIgnitionOn`, `isActive`, `checkControlMessages` y
`conditionBasedServicesAverageDistancePerDay`.

**Corrección del 2026-09-13:** `checkControlMessages` llegó con valor en cuanto
hubo un aviso (pastillas de freno). Vacío coincidía con no tener avisos, pero
BMW no lo documenta, así que las herramientas dicen "llega vacío" y no "sin
avisos". Los vacíos de esa lectura fueron **10**, no 11.

**Consecuencia para `diagnose_software_update`:** `deepSleepModeActive` y
`stateOfCharge` vinieron vacíos, así que ni la hipótesis del sueño profundo ni
el estado de carga son observables en esa lectura. De la batería solo queda
`voltage`, más `serviceDemand.recharge` y `.replace`.

Diagnóstico: `vehicle.chassis.axle.wheel.tire.diagnosis` (no streamable)
remite al endpoint `/smartMaintenanceTyreDiagnosis`. **Ese endpoint no
devuelve presiones**: da desgaste (`tyreWear.dueMileage`), defectos,
dimensiones, fecha de montaje y fabricación, temporada, runflat,
fabricante y dibujo. Presiones y diagnóstico son dos fuentes distintas.

### Batería de 12V

| Descriptor | Valor | Streamable |
|---|---|---|
| `vehicle.electricalSystem.battery.stateOfCharge` | estado de carga en % | sí |
| `vehicle.electricalSystem.battery.stateOfChargePlausibility` | si la medida es plausible | no |
| `vehicle.electricalSystem.battery.voltage` | voltaje actual, 5–20 V | sí |
| `vehicle.electricalSystem.battery.serviceDemand.recharge` | 1 = recarga necesaria, 0 = no | no |
| `vehicle.electricalSystem.battery.serviceDemand.replace` | salud: 200 adecuada / 140 limitada / 110 inadecuada / 80 degradada | no |

### Contexto de uso

| Descriptor | Valor | Streamable |
|---|---|---|
| `vehicle.vehicle.deepSleepModeActive` | modo de sueño profundo activo | sí |
| `vehicle.drivetrain.engine.isIgnitionOn` | motor encendido/apagado | sí |
| `vehicle.drivetrain.engine.isActive` | estado del contacto | sí |

`deepSleepModeActive` es relevante para este vehículo: pasa semanas
parado en cochera, y el sueño profundo apaga la mayoría de funciones
telemáticas. Es hipótesis alternativa a la de la batería para la ausencia
de actualizaciones, y explica lecturas con timestamps antiguos.

---

### En prueba: diez descriptores que la app oficial no enseña

Añadidos el 2026-09-14 en `TRIAL_DESCRIPTORS`. Existen en el catálogo, pero
**ninguno se ha visto llegar de este coche**:

| Descriptor | Para qué |
|---|---|
| `vehicle.drivetrain.fuelSystem.consumptionOverLifeTime.overall.fuel` + `.referenceDistance` | consumo real homologado (OBFCM): l/100 km, y por periodos con el histórico. No streamable |
| `vehicle.drivetrain.fuelSystem.level`, `.remainingFuel` | depósito en % y litros (±6 L según el catálogo): detectar repostajes |
| `vehicle.cabin.infotainment.navigation.remainingRange`, `vehicle.drivetrain.lastRemainingRange` | autonomía |
| `vehicle.electronicControlUnit.diagnosticTroubleCodes.raw` | memoria de averías, pensada para el taller |
| `vehicle.drivetrain.internalCombustionEngine.engine.ect` | temperatura del refrigerante: trayectos con el motor frío |
| `vehicle.serviceDemand.defect.id` | diagnóstico en vivo. No streamable, **sospechoso de endpoint dedicado** como el de neumáticos |
| `vehicle.isMoving` | si llega, distingue un voltaje de alternador de uno en reposo, que `isIgnitionOn` no puede |

Solo los pide `scripts/bootstrap_containers.py`. Las herramientas no los usan, y
un contenedor sin ellos **no los da por ausentes**: eso diría "este coche no los
emite" sin haberlo comprobado. `get_telematic_data` los enseña aparte, en una
sección `EN PRUEBA`. Un descriptor sale de la prueba solo después de verlo llegar
en una lectura real y grabarla como fixture.

La posición (`navigation.currentLocation.*`) queda fuera a propósito, por
privacidad. Si algún día entra, será opcional y desactivada por defecto.

**Resultado de la prueba, 2026-09-14 a las 14:30 UTC.** BMW aceptó los 42
descriptores en un solo `POST` (código 200, 1 petición). El contenedor nuevo
tiene otro id de 13 caracteres. Primera lectura, grabada en
`tests/fixtures/telematic_extended.json`. **Llegan 7 de los 10:**

| Descriptor | Valor | Sello | Observación |
|---|---|---|---|
| `fuelSystem.level` | `58` % | 13:51:32 | grupo de la medida real |
| `fuelSystem.remainingFuel` | `24`, **unidad `null`** | 13:49:14 | el catálogo dice litros, ±6 L |
| `lastRemainingRange` | `395` km | 13:51:32 | |
| `consumptionOverLifeTime.overall.fuel` | `226.32` l | **2024-10-30** | congelado casi dos años |
| `consumptionOverLifeTime.overall.referenceDistance` | `2826.3` km | **2024-10-30** | ídem |
| `diagnosticTroubleCodes.raw` | XML, ver abajo | 13:49:14 | |
| `engine.ect` | `85` °C | 13:49:14 | recién aparcado tras el viaje |

Vacíos: `navigation.remainingRange`, `serviceDemand.defect.id` e `isMoving`. El
de `isMoving` duele: era la esperanza de distinguir un voltaje de alternador de
uno en reposo.

**Consumo OBFCM: no es el consumo de hoy.** 226,32 l en 2.826,3 km son 8,0 l/100
km, pero sellados el 2024-10-30, cuando el coche tenía unos 2.800 km. El
catálogo describe el OBFCM como valores que se transfieren en el taller, por
cable. Probablemente solo se actualiza en una visita al taller: la cita de las
pastillas es la ocasión de comprobarlo. Hasta entonces **no sirve para una
serie**, y ninguna herramienta puede presentarlo como consumo actual.

**Memoria de averías: XML dentro de la cadena.**

```xml
<dtcData dtcCount="72">
  <dtc ecuAddress="29">X00001</dtc>
  ...
</dtcData>
```

- Solo lleva `ecuAddress` (dirección de la centralita, decimal) y el código.
  **Sin estado, sin fecha y sin descripción**: no se sabe si un código está
  activo o solo almacenado, ni cuándo apareció.
- **Otra discrepancia de contador: `dtcCount="72"` con 44 entradas `<dtc>`.** El
  XML está completo (1.615 caracteres, cerrado). Sin explicación, como el 9
  contra 5 de CBS: se dan los dos números.
- 44 códigos distintos repartidos en 13 centralitas. La que más tiene es la 16,
  con 11.
- **El significado de cada código no está en el catálogo y no se inventa.** Los
  códigos de BMW son del fabricante. Una herramienta puede contarlos, agruparlos
  y, sobre todo, **compararlos entre lecturas**: códigos nuevos, códigos
  borrados tras el taller. Traducirlos no.

`conditionBasedServices` siguió vacío también en esta lectura.

**Promovidos el mismo 2026-09-14.** Los diez pasan al contenedor confirmado
(`FUEL_DESCRIPTORS`, `DIAGNOSTIC_DESCRIPTORS` e `IS_MOVING` en el contexto), que
queda en 42. Los tres vacíos se tratan como cualquier otro campo vacío.
`TRIAL_DESCRIPTORS` queda vacío para futuras pruebas.

Dos consecuencias que hubo que corregir en el código:

- **El sello de 2024 del OBFCM se colaba como "dato más antiguo utilizado"** en
  el estado, el resumen y el diagnóstico, que no usan ese dato.
  `FROZEN_LIFETIME_DESCRIPTORS` lo excluye de esas tres. `get_telematic_data`
  sí enseña el OBFCM, así que allí 2024 es el dato más antiguo de verdad.
- **La unidad de la temperatura del refrigerante llega como `Â°C`**: es `°C` con
  la codificación rota, UTF-8 leído como Latin-1. No se sabe si pasa en el
  servidor de BMW o en la librería. Ninguna herramienta la muestra salvo
  `get_telematic_data`, que enseña las unidades tal como llegan.

## Paso 0: lo que NO existe

Verificado por búsqueda exhaustiva en el catálogo. Está prohibido
implementar nada sobre estos supuestos.

- **Versión de software del vehículo: no hay ningún descriptor.** Ni
  iStep, ni versión, ni nada relacionado con Remote Software Upgrade.
  Lo único disponible es `puStep` en `/basicData`, que es el paso de
  actualización de producto y probablemente no cambia con cada RSU.
- **Estado de las luces de emergencia: no existe.**
- **Inclinación / pendiente de aparcamiento: no existe.**
- No existe ningún descriptor de estado, disponibilidad o historial de
  actualizaciones de software.

---

## Herramientas

Estado obligatorio de cada una. Nada marcado como *no implementable* se
escribe; nada marcado como *pendiente* se da por bueno sin verificación
real.

| Herramienta | Fuente | Estado |
|---|---|---|
| `list_vehicles()` | `/mappings` | confirmada |
| `get_vehicle_basic_data(vin)` | `/basicData` | confirmada |
| `get_vehicle_status(vin)` | `/telematicData` | confirmada: km y desglose CBS por partida. La estructura de `conditionBasedServices` está verificada contra una respuesta real y grabada como fixture |
| `get_tyre_diagnosis(vin)` | `/smartMaintenanceTyreDiagnosis` | implementada, pero **este vehículo devuelve la estructura vacía**: solo etiquetas y ceros de relleno. La herramienta lo declara y no presenta los ceros como medidas |
| `get_telematic_data(vin, container_id)` | `/telematicData` | confirmada |
| `search_descriptors(query)` | catálogo local | confirmada, **no gasta cuota** |
| `get_api_quota()` | SQLite | confirmada, no gasta cuota |
| `get_maintenance_summary(vin)` | compuesta | confirmada, con las reservas de CBS. Desde el 2026-09-14 añade una **previsión** (semanas y fecha de cada partida CBS, al ritmo de uso) y la **tendencia de neumáticos por eje** (posible fuga lenta). Ninguna de las dos gasta peticiones |
| `get_software_version(vin)` | — | **no implementable como tal** |
| `report_product_update_step(vin)` | `/basicData` | implementada, pero **este vehículo no devuelve `puStep`**. Verificado el 2026-09-07. La herramienta explica la ausencia y sigue aclarando que `puStep` nunca fue la versión de software |
| `diagnose_software_update(vin)` | compuesta, sobre histórico | confirmada, con veredicto acotado |
| `get_fuel_status(vin)` | `/telematicData` + histórico | confirmada el 2026-09-14. Depósito y autonomía; repostajes detectados en el histórico (subida de 10 puntos o más); consumo desde el último repostaje solo a partir de 300 km, con su margen de +/-6 L en cada extremo. El OBFCM se muestra como cifra de por vida congelada, nunca como consumo actual |
| `get_fault_memory(vin)` | `/telematicData` + histórico | confirmada el 2026-09-14. Códigos agrupados por centralita, la discrepancia de la cabecera y los códigos que aparecen o desaparecen entre lecturas. **No traduce ningún código** |

### `get_maintenance_summary(vin)`

Devuelve un bloque de texto listo para pegar en un prompt: kilometraje,
cada partida CBS disponible con km y fecha restantes, presiones de las
cuatro ruedas contra su objetivo, y la fecha del dato más antiguo
utilizado. Si el desglose CBS no llega, lo dice y da el valor global de
`serviceDistance.next`.

**Previsión (2026-09-14).** Convierte los km de cada partida CBS en semanas y
fecha, contando desde el sello del kilometraje, no desde hoy. Hay dos ritmos
posibles: `averageWeeklyDistanceShortTerm` de BMW y el que mide el histórico
local (sin cifra si hay menos de 2 días de lecturas). Se usa **el mayor**, para
que la previsión nunca llegue tarde. Las fechas CBS vienen solo con mes, así que
se comparan con el día 1 de ese mes. La salida dice que es un cálculo propio y
no un dato de BMW.

**Tendencia por eje (2026-09-14).** No compara una rueda con su propio objetivo
a lo largo del tiempo: el objetivo se mueve con el calor y las temperaturas no
llegan, así que eso confundiría el tiempo que hace con una fuga. Compara cada
rueda con su pareja de eje **en la misma lectura**, que comparte temperatura y
carga. La lectura se reconstruye con `HistoryStore.snapshots()`, que arrastra
los valores deduplicados. El aviso exige 3 lecturas en al menos un día, y una
diferencia que haya crecido dos pasos del sensor (20 kPa). Un paso de 10 kPa es
ruido.

### `diagnose_software_update(vin)`

El vehículo lleva ~1 año sin recibir una Remote Software Upgrade. BMW
documenta que la instalación no se ofrece si el estado de carga de la
batería de 12V es bajo, si las luces de emergencia quedan puestas al
apagar el motor, o si el coche está aparcado con más de un 12% de
inclinación.

**De esas tres condiciones, CarData solo permite observar una.** La
herramienta debe decirlo literalmente en su salida. Su veredicto puede
confirmar o descartar la hipótesis de la batería; sobre las otras dos
solo puede declarar "no observable por CarData". Está prohibido que la
herramienta insinúe una conclusión más fuerte que la evidencia.

El informe incluye: `puStep` actual y fecha del último cambio detectado
en el histórico local, serie de `stateOfCharge` y `voltage` de la batería
de 12V con su tendencia, `serviceDemand.recharge`, `deepSleepModeActive`,
kilometraje, y la fecha del dato más reciente y más antiguo.

**El valor está en la SERIE, no en la foto puntual.** Cada lectura se
guarda en SQLite. Con un solo punto la herramienta no concluye: dice
cuántas lecturas tiene, desde cuándo, y qué le falta para poder
pronunciarse.

---

## Persistencia

### Tokens

Refresh token 14 días, access token 1 hora. Se guardan **fuera del
repo**, en `~/.config/pitwall-mcp/tokens.json` con permisos `600`. El
servidor refresca automáticamente y **avisa de forma visible cuando
quedan menos de 3 días** de vida del refresh token, porque si caduca hay
que repetir el login manual (device flow con navegador).

### SQLite

Un único fichero, ruta configurable, fuera del repo por defecto.

- `api_cache` — respuesta cruda por endpoint + VIN + container, con
  `fetched_at` y `expires_at`.
- `quota_log` — una fila por petición REST realmente enviada: timestamp,
  endpoint, código de respuesta. El contador diario se deriva de aquí.
- `readings` — histórico: `vin`, `descriptor`, `value`, `unit`,
  `source_timestamp` (el que da BMW), `recorded_at` (cuando lo guardamos
  nosotros), y **`source`** (`rest` | `mqtt`). La columna `source` existe
  desde el primer día para que el daemon de la Fase 2 escriba en la misma
  tabla sin migración.
- `stream_state` — reservada para la Fase 2: quién tiene tomada la
  conexión de streaming del gcid y desde cuándo.

### Cuota y TTLs

Tope duro configurable, por defecto **20 peticiones/día** de las 50, para
dejar margen. Al alcanzarlo, las herramientas sirven de caché o fallan
con un mensaje claro; no envían nada.

| Dato | TTL |
|---|---|
| `/telematicData` (contenedor de mantenimiento) | 12 h |
| `/smartMaintenanceTyreDiagnosis` | 7 días |
| `/basicData` | 30 días |
| `/mappings` | 30 días |

Se desconoce en qué huso horario resetea BMW la ventana de 24 h. Se
asume UTC y se documenta como suposición; el contador es conservador.

### Contenedor

Un solo contenedor gordo, `pitwall-maintenance`, con todos los
descriptores confirmados arriba: una sola petición trae kilometraje, CBS,
batería, presiones y estado de sueño. Con TTL de 12 h son 2 peticiones
al día.

**Resuelto el 2026-09-07:** BMW aceptó los 32 descriptores en un solo `POST`,
sin límite de tamaño. No hay que partir el contenedor y cada lectura cuesta
**1 petición**, como estaba presupuestado. El `containerId` que devuelve no es
un UUID: son 13 caracteres alfanuméricos.

**Contenedor ampliado, preparado el 2026-09-14 y aún sin crear.**
`bootstrap_containers.py` pide ahora 42 descriptores: los 32 confirmados y los 10
en prueba. No se sabe si BMW acepta 42 en un solo `POST` (32 sí los aceptó).
Pasos, siempre a mano:
1. `--create`, que gasta 1 petición;
2. copiar el id nuevo al `.env`;
3. hacer una lectura (1 petición) y grabarla como fixture;
4. borrar el contenedor antiguo con `--delete ID` (1 petición).

Hasta el paso 2, todo sigue funcionando con el contenedor de 32.

**Hecho el 2026-09-14.** Contenedor de 42 creado a las 14:28 UTC y antiguo
borrado a las 14:33 UTC. Un `--list` posterior lo confirma. **Un contenedor
borrado sigue apareciendo en el listado**, con estado `DELETED`, junto al activo
(`ACTIVE`). El ciclo completo costó 4 peticiones: crear, leer, borrar y listar.

---

## Errores útiles

Cada error debe decir en español qué tiene que hacer el usuario:

- **Falta un scope** → qué scope falta (`cardata:api:read` /
  `cardata:streaming:read`) y dónde autorizarlo en el portal.
- **No hay campos mapeados / contenedor vacío** → que ejecute
  `scripts/bootstrap_containers.py`, o que revise Data Selection en el
  portal si es streaming.
- **Cuota agotada (403 `CU-429`)** → cuántas peticiones se han gastado,
  desde cuándo, y a qué hora estimada se recupera.
- **Refresh token caducado** → que ejecute `scripts/login.py` y complete
  el device flow en el navegador.
- **VIN no `PRIMARY`** → CarData exige ser usuario primario del vehículo.

---

## Fase 2 (diseñar, NO implementar)

Daemon opcional suscrito al stream MQTT
`customer.streaming-cardata.bmwgroup.com:9000`, topic `<gcid>/<vin>`,
escribiendo en la misma SQLite para que el MCP lea sin gastar cuota.

- **BMW solo permite una conexión de streaming simultánea por gcid.**
  Daemon y servidor MCP nunca conectan a la vez. El mismo conflicto
  aparece con cualquier otro consumidor del stream (Home Assistant,
  evcc). `stream_state` sirve de cerrojo.
- La selección de descriptores a emitir (**Data Selection**) solo se hace
  desde el portal, no por API. Y solo sirven los descriptores con
  `"streamable": true` en el catálogo; varios de los de CBS y batería no
  lo son y seguirán dependiendo de la REST.

Todo esto va a `docs/streaming-design.md`. Sin código.

---

## Publicación

- Licencia **MIT**, titular Panesoft.
- Aviso de proyecto no afiliado a BMW AG en README y en `server.json`.
- `server.json` válido para `registry.modelcontextprotocol.io`.
- **Cuenta y namespace: `sergioprats`.** No `panesoft`: esa organización existe
  en GitHub pero la cuenta de trabajo no es miembro, y el registro MCP exige
  demostrar la propiedad de `io.github.<cuenta>`. Aparece en `server.json`,
  `pyproject.toml` y el README; si algún día cambia, cambia en los tres.
- **Resuelto el 2026-09-08: los documentos de BMW no se redistribuyen.**
  `spec/*.json` está en `.gitignore` y purgado del historial. El catálogo se
  descarga con `scripts/refresh_catalogue.py` (de GitHub, no de BMW: no gasta
  cuota) y el swagger se saca del portal. `spec/README.md` lo explica. Esto
  evita tener que interpretar las condiciones B2C, no las resuelve.
- ~~Consecuencia para PyPI.~~ **RESUELTO el 2026-09-08:** `pitwall-mcp
  --fetch-catalogue` descarga el catálogo desde el propio paquete, sin
  `scripts/`. Se niega a escribir si lo descargado no es un catálogo, para que
  un portal cautivo no destruya una instalación que funcionaba.
  `scripts/refresh_catalogue.py` reutiliza ese mismo descargador: una sola URL
  y una sola definición de qué es un catálogo válido.

### Pendiente para cerrar la publicación

Estado comprobado el 2026-09-13:

- El repositorio de GitHub `sergioprats/pitwall-mcp` es **privado**.
- **El historial está limpio.** Ni el VIN, ni el client id, ni el
  `containerId`, ni el gcid reales aparecen en `git log -p` de `main` ni de la
  rama de backup. Los VIN que salen son todos de prueba: `...FAKE01`,
  `...FAKE02`, `WBAOTROVIN1234567`, `WBA11223344556677`.
- **El wheel está limpio**: 34 ficheros, `schema.sql` incluido, ningún documento
  de BMW.
- **El nombre `pitwall-mcp` está libre en PyPI** (404 en la API JSON).

**1. Retoques locales, sin riesgo:**

- [x] La sección "Estado" del README está desfasada. Aún da por abierto si los
      11 vacíos se rellenan con el contacto dado: se cerró el 2026-09-08, y
      desde el 2026-09-13 son 10. Debe decir también que la REST no da serie de
      voltaje en reposo.
- [x] Añadir al README `mcp-name: io.github.sergioprats/pitwall-mcp`. Lo exige
      el registro MCP para demostrar la propiedad de un paquete de PyPI.
      Verificado el 2026-09-14 en la documentación del registro: puede ir en un
      comentario HTML, que es como está, pero el nombre tiene que coincidir con
      el de `server.json`.
- [ ] **`server.json` usa el esquema `2025-07-09`**, y la documentación del
      registro ya pone de ejemplo el `2025-12-11`. Actualizarlo y validarlo con
      `mcp-publisher` justo antes de publicar.
- [ ] **Documentación final**, pedida por el usuario el 2026-09-14 para cuando el
      proyecto esté listo: pasos para publicar, cómo instalarlo y usarlo, y
      qué ventajas aporta usar este MCP frente a la app o el portal. Sin
      prometer nada que el coche no emita: las ventajas se describen con los
      datos que de verdad llegan.
- [ ] **Decidir si se anonimizan los códigos de avería de
      `tests/fixtures/telematic_extended.json`** antes de hacer público el
      repositorio. No llevan VIN ni ningún identificador, pero son la memoria de
      averías real de este coche.
- [ ] Commit de lo anterior.

**2. Decisiones del usuario. Salen fuera o borran, y no se deshacen fácilmente:**

- [ ] **Borrar la rama local `backup-antes-de-purgar`**, o sacarla a un bundle
      fuera del repo. Tiene 24 commits que no están en `main` y, por su nombre,
      conserva el historial de antes de purgar `spec/*.json`. No tiene upstream,
      pero un `git push --all` la subiría. **Nunca usar `--all` con este repo
      mientras exista.**
- [ ] Subir `main` y hacer público el repositorio.
- [ ] Publicar la 0.1.0 en PyPI. Un número de versión publicado no se puede
      reutilizar. Hace falta una cuenta de PyPI con token, o publicación
      directa desde GitHub (trusted publishing).
- [ ] Registrar en `registry.modelcontextprotocol.io` con `mcp-publisher`,
      entrando con la cuenta de GitHub `sergioprats`. Validar `server.json`
      contra el esquema vigente en ese momento.

Orden recomendado: borrar la rama de backup, subir y hacer público, PyPI y, por
último, el registro. Ese orden es obligatorio: el registro comprueba el paquete
de PyPI, y PyPI enlaza al repositorio.

**3. Abierto, pero no impide publicar:** la prueba de la noche parado (2
peticiones), la discrepancia 9 contra 5 del contador CBS, el huso horario del
reinicio de cuota y la Fase 2.

---

## Riesgos abiertos

1. ~~Estructura de `vehicle.status.conditionBasedServices`.~~ **CERRADO el
   2026-09-07**: llega por `/telematicData`, su `value` es JSON dentro de una
   cadena, y la estructura está documentada arriba y grabada como fixture.
2. **`bmw-cardata` está en alfa** (0.1.0a3, junio 2026). Versión pinneada
   y adaptador propio obligatorio.
3. **Cobertura real del U11**: parcialmente resuelto. Las 32 claves del
   contenedor llegan, pero **11 vienen vacías** (ver "Tercer estado"). Queda
   por saber si esos 11 se rellenan en otras condiciones —con el coche
   despierto, recién apagado, en movimiento— o si este U11 no los emite nunca.
   Solo se sabe repitiendo la lectura en circunstancias distintas.
4. ~~`puStep` puede no moverse nunca con las RSU.~~ **CERRADO, y peor de lo
   esperado, el 2026-09-07**: `/basicData` **no devuelve `puStep`** para este
   vehículo. No es que no se mueva: no llega. Con esto, no queda **ningún**
   dato en toda la API que informe sobre el software del coche.
5. **Reset de cuota**: huso horario desconocido.
6. **`conditionBasedServicesCount` no cuadra con el array**: devolvió 9 con 5
   partidas. Sin explicación. No se inventa una.
7. ~~Desfase de reloj de BMW.~~ **EXPLICADO el 2026-09-07**: no es desfase. Ver
   "Timestamps de petición" abajo.
8. **`/basicData` no coincide con el swagger.** Este vehículo no devuelve
   `vin`, `puStep`, `isTelematicsCapable` ni `modelRange`, y en cambio devuelve
   `seriesDevt`, `colourDescription` y `countryCode`, que el swagger no
   documenta. El adaptador guarda la respuesta cruda, así que esto no rompe
   nada, pero invalida el esquema como fuente de verdad sobre qué campos llegan.
9. **El diagnóstico de neumáticos viene vacío**: estructura completa, valores a
   cero, `errors: []`. Probablemente requiere que los neumáticos estén
   registrados en el sistema de BMW. Los ceros son relleno, no medidas.
10. ~~Los 11 campos vacíos.~~ **CERRADO el 2026-09-08, en negativo.** Lectura
    con el coche **encendido y rodando**: los mismos 11 siguen vacíos. El caso
    decisivo es `isIgnitionOn`, que llegó vacío **mientras el contacto estaba
    dado**. Eso no es "falta de lectura reciente": este U11 no los emite. Que el
    coche estaba despierto está probado por el propio dato — `travelledDistance`
    pasó de 48.260 a 48.278 km y una presión trasera subió de 220 a 230 kPa, con
    sellos de las 17:59 de ese día.

    **Corregido el 2026-09-13 para uno de los 11:** `checkControlMessages` llegó
    con valor en cuanto el coche tuvo un aviso. Su vacío no era "este U11 no lo
    emite", era probablemente "no hay nada que avisar". Para los otros diez la
    conclusión se mantiene.

11. **Hay un grupo de descriptores congelado, y arruina la serie.** En esa misma
    lectura, nueve descriptores conservaron el sello `2026-09-07T08:55:27.912Z`
    de veinte horas antes: `battery.voltage`, `serviceDistance.next` y
    `.yellow`, `serviceTime.*`, `conditionBasedServicesCount` y las dos medias
    semanales. **No se movieron ni con el coche en marcha.**

    Consecuencia directa: **`diagnose_software_update` puede no conseguir nunca
    una serie por REST**, porque el voltaje no se refresca al leer. La
    herramienta lo dice en su salida en lugar de recomendar repetir la lectura,
    que es un consejo contra el que ahora tenemos evidencia. Si se confirma, la
    única vía de serie real es el streaming MQTT de la Fase 2.

    **Probado el 2026-09-08 a las 18:30, y la hipótesis no se sostiene.** Una
    tercera lectura, media hora después de la anterior y tras otros 3 km, dejó
    los nueve descriptores **con el mismo sello exacto de las 08:55 del día
    anterior**: 34 horas sin moverse, dos trayectos por medio. La idea de que
    el grupo se refresque al apagar el motor no queda descartada del todo —el
    vehículo seguía emitiendo medidas frescas a las 18:29:55, así que puede que
    el apagado no hubiera terminado— pero ya no es la explicación probable.

    Esa misma lectura vuelve a mostrar los cuatro grupos de sellos, ahora
    nítidos: nueve congelados del día anterior, cinco de las 17:54, cinco de la
    medida real a las 18:29:55, y dos con el sello de nuestra propia petición.

    **Cerrado el 2026-09-08 a las 18:41, con el coche ya apagado: los nueve
    siguen exactamente igual.** Cuatro lecturas, 34 horas, dos trayectos y un
    apagado, y ni uno se movió. La hipótesis del apagado está muerta.

    **Conclusión operativa: la REST no sirve para construir series** de esos
    nueve descriptores, `battery.voltage` incluido. `diagnose_software_update`
    puede quedarse en `SIN VEREDICTO` indefinidamente por mucho que se lea, y lo
    dice en su salida. **La Fase 2 deja de ser opcional**: es la única vía para
    tener serie de voltaje.

    Queda una prueba más barata que confirmaría el mecanismo: leer tras una
    noche entera parado. Si el grupo se moviera entonces, el disparador sería el
    ciclo de sueño profundo y no el uso del coche.

    **Reabierto el 2026-09-13: el grupo sí se mueve.** Cinco días y 157 km
    después, los nueve traían un sello nuevo y común, `2026-09-13T17:21:47.172Z`.
    Se movieron juntos, como grupo. Pero la prueba de la noche no quedó hecha:
    entre el 8 y el 13 no se leyó, así que no se sabe cuántas veces se refrescó
    ni con qué disparador. Lo que sí se sabe es que **no fue en reposo**: el
    voltaje nuevo era **14,35 V**, y el del 7 de septiembre, **14,39 V**. Los
    dos son tensión de alternador, con el motor en marcha.

    **Conclusión operativa, corregida:** la REST sí añade puntos a la serie,
    pocos, y hasta ahora ninguno sirve para juzgar la batería en reposo.
    `diagnose_software_update` deja fuera todo voltaje de 13,5 V o más: sin esa
    regla, dos lecturas de alternador bastaban para declarar la hipótesis de la
    batería "debilitada", una conclusión que la evidencia no sostiene. Con las
    dos lecturas reales se queda en `SIN VEREDICTO` y explica por qué. La Fase 2
    sigue siendo la única vía conocida para una serie en reposo.

    **2026-09-14, tras una noche parado y un tramo de 272 km:** el grupo volvió
    a moverse, con sello `2026-09-14T10:06:28.763Z` y **14,77 V**, otra vez con el
    motor en marcha. Son tres refrescos vistos y los tres con el alternador
    cargando. Ninguno lleva sello nocturno, aunque como solo se ve el último
    sello, un refresco de madrugada que después se sobrescribiera sería
    invisible. En la misma lectura, el Check Control 907 llegó re-sellado (12:09)
    sin cambiar nada: mismo texto y mismo kilometraje, 48.376. Un sello nuevo en
    un aviso no significa un aviso nuevo.

    **2026-09-14 a las 19:58 UTC, con el motor recién arrancado y el coche
    parado** (lectura pedida a propósito para esta prueba):

    - `isIgnitionOn`, `isActive` e `isMoving` siguen **vacíos con el motor en
      marcha**. Tercera confirmación: este U11 no los emite.
    - **El grupo lento no se refrescó al arrancar.** El voltaje (13,92 V), la
      temperatura del refrigerante, `serviceDistance.next` y **los litros del
      depósito** conservan el sello de las 13:49. El nivel en % sí es fresco
      (19:56). Arrancar el motor no es el disparador.
    - **Consecuencia para `get_fuel_status`, CORREGIDA el mismo día:**
      `remainingFuel` viaja en el grupo lento, y las lecturas reconstruidas
      arrastraban esos litros antiguos junto a kilómetros frescos. Un test
      reproduce el caso y el código viejo se inventaba un consumo. Ahora el
      consumo usa solo **medidas** de litros, una por sello distinto de BMW,
      cada una emparejada con el kilometraje medido más cerca en el tiempo. Un
      repostaje, a efectos de consumo, es una subida de más de 12 L (el error
      del aforador en los dos extremos). La herramienta enseña además cuándo se
      midieron los litros, que pueden ser más antiguos que el porcentaje.
    - CBS volvió a llegar con valor (frenos, 1.600 km), con el sello del grupo de
      las presiones objetivo.

---

## Timestamps de petición, no de medida

Verificado el 2026-09-07 comparando dos lecturas consecutivas.

**No todos los `timestamp` significan lo mismo.**
`battery.serviceDemand.recharge` y `.replace` vinieron sellados con el momento
de **nuestra propia petición**, al milisegundo (`21:35:06.005Z` para una llamada
hecha a las 21:35:06). `travelledDistance`, en cambio, traía el momento de la
medida real (20:40), idéntico en ambas lecturas.

Esto explica el "timestamp un minuto en el futuro" que veíamos: es el reloj del
servidor de BMW, no un desfase del vehículo.

**Los grupos son eventos de subida, no clases fijas.** En la lectura de las
18:41 las cuatro `pressureTarget` abandonaron el grupo de las 17:54 y pasaron a
uno nuevo de las 18:30:57, dejando a `conditionBasedServices` solo en el suyo.
No se puede clasificar un descriptor por el grupo en el que estuvo una vez.

**Verificado el 2026-09-08: son cuatro grupos, no dos.** Una sola respuesta
traía cuatro sellos distintos: `17:54:22.984` (CBS y las presiones objetivo),
`17:59:09.000` (kilometraje y presiones reales, la medida de verdad),
`18:00:07.428` (los dos `serviceDemand`, o sea el momento de nuestra petición) y
`2026-09-07T08:55:27.912` (el grupo congelado del riesgo 11). Leer un timestamp
sin saber a qué grupo pertenece lleva a conclusiones falsas.

**Consecuencia grave para el histórico.** El índice único de `readings` incluye
`source_timestamp`, así que esos dos descriptores **generan una fila nueva en
cada lectura aunque el valor no se mueva**. Contar filas sobrestimaría la
evidencia: diez llamadas en una tarde parecerían diez observaciones de una
batería medida una sola vez.

Por eso **todo lo que razone sobre tendencias usa `HistoryStore.changes()`**, que
colapsa repeticiones consecutivas, y nunca `series()` a secas.
