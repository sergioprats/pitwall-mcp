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

### Neumáticos

Presiones y temperaturas, las ocho + cuatro, todas streamable:

- `vehicle.chassis.axle.row1.wheel.left.tire.pressure` (+ `.right`, `row2`)
- `vehicle.chassis.axle.row1.wheel.left.tire.pressureTarget` (+ ídem)
- `vehicle.chassis.axle.row1.wheel.left.tire.temperature` (+ ídem)

Unidad **kPa** (0–1000), valor posible `-NA-` que debe tratarse
explícitamente como "sin medida", nunca como cero. Se presenta al usuario
en bar, junto al diferencial contra `pressureTarget`.

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
| `get_vehicle_status(vin)` | `/telematicData` | **pendiente**: km sí; desglose CBS por partida depende de la estructura de `conditionBasedServices` |
| `get_tyre_diagnosis(vin)` | `/smartMaintenanceTyreDiagnosis` | confirmada, sin presiones |
| `get_telematic_data(vin, container_id)` | `/telematicData` | confirmada |
| `search_descriptors(query)` | catálogo local | confirmada, **no gasta cuota** |
| `get_api_quota()` | SQLite | confirmada, no gasta cuota |
| `get_maintenance_summary(vin)` | compuesta | confirmada, con las reservas de CBS |
| `get_software_version(vin)` | — | **no implementable como tal** |
| `report_product_update_step(vin)` | `/basicData` | sustituye a la anterior: expone `puStep` etiquetado explícitamente como paso de actualización de producto, **no** versión de software, y lo guarda en el histórico |
| `diagnose_software_update(vin)` | compuesta, sobre histórico | confirmada, con veredicto acotado |

### `get_maintenance_summary(vin)`

Devuelve un bloque de texto listo para pegar en un prompt: kilometraje,
cada partida CBS disponible con km y fecha restantes, presiones de las
cuatro ruedas contra su objetivo, y la fecha del dato más antiguo
utilizado. Si el desglose CBS no llega, lo dice y da el valor global de
`serviceDistance.next`.

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

No se sabe si BMW limita el número de descriptores por contenedor. Si el
`POST` falla por tamaño, el script lo parte en dos y cada lectura pasa a
costar 2 peticiones; hay que reflejarlo en el presupuesto de cuota.

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
- **Antes de publicar nada**: revisar las condiciones de uso B2C de BMW
  CarData, en especial si permiten redistribuir el catálogo telemático
  dentro del repositorio. Si no lo permiten, `spec/telematic_catalogue.json`
  sale del repo y se descarga en tiempo de instalación.

---

## Riesgos abiertos

1. **Estructura de `vehicle.status.conditionBasedServices`**: no
   documentada, y puede estar ligada a un endpoint dedicado y no llegar
   por `/telematicData`. Es el riesgo número uno para el desglose CBS.
   Se verifica con una única llamada real y se graba como fixture.
2. **`bmw-cardata` está en alfa** (0.1.0a3, junio 2026). Versión pinneada
   y adaptador propio obligatorio.
3. **Cobertura real del U11**: qué descriptores emite de verdad este
   coche solo se sabe llamando. Los que no lleguen se documentan como no
   disponibles para este vehículo, no como inexistentes.
4. **`puStep` puede no moverse nunca** con las RSU. Si tras meses de
   histórico no cambia, se documenta como inútil para el diagnóstico en
   lugar de mantener la ficción.
5. **Reset de cuota**: huso horario desconocido.
