# Fase 2: diseño del daemon de streaming

**Estado: diseño. No hay código, y no se escribe hasta cerrar las decisiones
abiertas del final.**

Fecha: 2026-09-07. Verificado contra `bmw-cardata==0.1.0a3` (código leído, no
README) y contra `spec/telematic_catalogue.json`.

---

## 1. Qué problema resuelve

La REST está limitada a **50 peticiones/24 h por cuenta**. Con el contenedor de
mantenimiento y un TTL de 12 h, el Bloque A gasta 2 peticiones al día para
obtener **2 fotos** del vehículo. Eso basta para "¿cuánto le queda al
mantenimiento?", pero no para la pregunta que de verdad importa:

> ¿La batería de 12 V se está degradando, o el coche simplemente lleva semanas
> en sueño profundo?

Esa pregunta se responde con una **serie**, y una serie de 2 puntos al día
tarda meses en decir algo. El streaming MQTT entrega los valores cuando el
vehículo los emite, **sin consumir cuota REST**, y los escribe en la misma
SQLite que ya lee el servidor MCP.

El objetivo no es sustituir la REST. Es alimentar `readings` con densidad
suficiente para que `diagnose_software_update` deje de decir "no tengo datos
suficientes para pronunciarme".

---

## 2. Lo que el streaming NO resuelve

Esto es lo primero porque acota todo lo demás.

### 2.1. Seis descriptores del contenedor no son streamable

De los 32 descriptores de `pitwall-maintenance`, **26 son streamable y 6 no**.
Los que no lo son seguirán dependiendo de la REST para siempre:

| Descriptor | Por qué duele |
|---|---|
| `vehicle.status.conditionBasedServices` | Es el desglose CBS completo. El riesgo nº 1 del proyecto sigue siendo REST. |
| `vehicle.status.conditionBasedServicesAverageDistancePerDay` | Contexto CBS. |
| `vehicle.status.checkControlMessages` | Avisos Check Control. |
| `vehicle.electricalSystem.battery.stateOfChargePlausibility` | Dice si la medida de carga es fiable. |
| **`vehicle.electricalSystem.battery.serviceDemand.recharge`** | **1 = el coche pide recarga.** |
| **`vehicle.electricalSystem.battery.serviceDemand.replace`** | **Salud de la batería: 200/140/110/80.** |

Las dos últimas están en negrita a propósito: **son las dos señales de salud de
la batería de 12 V, y son justo las que `diagnose_software_update` necesita.**

Consecuencia de diseño, y hay que decirlo sin adornos: **el streaming NO libera
al diagnóstico de la REST.** Aporta la serie continua de `stateOfCharge`,
`voltage` y `deepSleepModeActive` — que es mucho, porque ahí está la tendencia —
pero el veredicto de salud (`serviceDemand.replace`) seguirá llegando 2 veces al
día como máximo, vía contenedor.

El daemon **no elimina** las 2 peticiones diarias. Las mantiene, y añade
densidad encima.

### 2.2. La selección de datos solo se hace desde el portal

Qué descriptores emite el vehículo por MQTT se configura en **Data Selection**,
en el portal de BMW CarData. **No hay API para eso.** El daemon no puede pedir
un descriptor que no esté seleccionado a mano, y no puede comprobar
programáticamente qué hay seleccionado.

Implicación operativa: si el stream no trae algo, la primera hipótesis no es un
bug del daemon, es que falta en Data Selection. El mensaje de error tiene que
decirlo, con la URL del portal.

### 2.3. Una sola conexión de streaming por gcid

BMW permite **una única conexión simultánea por gcid**. Esto no es un detalle de
implementación: es la restricción que dicta toda la arquitectura de la sección 4.

Y el conflicto no es solo interno. Cualquier otro consumidor del mismo stream
—Home Assistant, evcc, un script propio— compite por la misma conexión, y
**nuestra base de datos no puede verlos**. El cerrojo de la sección 5 es
cooperativo entre nuestros procesos; frente a terceros solo podemos detectar el
síntoma (desconexiones), no prevenirlo.

---

## 3. Lo verificado en la librería

Leído en `bmw_cardata/streaming.py` y `models/streaming.py`, no supuesto:

| Elemento | Valor real |
|---|---|
| Clase | `StreamingClient(gcid, id_token, *, host, port, client_id, tls_context, keepalive=60)` |
| Broker | `customer.streaming-cardata.bmwgroup.com`, puerto `9000`, MQTT sobre TLS |
| Usuario MQTT | el `gcid` |
| Contraseña MQTT | **el `id_token`**, no el access token |
| `client_id` MQTT | por defecto, el propio `gcid` |
| Topic | `f"{gcid}/{vin}"`, o `f"{gcid}/+"` con `vin=None` |
| API | `async for message in client.stream(vin)`, más `connect()` / `close()` |
| Errores | `MqttError` se envuelve en `StreamingError` **y se propaga**: no hay reconexión interna |

Envolvente del mensaje, tal y como la parsea la librería:

```json
{
  "data": { "vehicle.x.y": { "value": "...", "unit": "...", "timestamp": "..." } },
  "entityId": "<uuid>",
  "timestamp": "...",
  "topic": "<vin>",
  "vin": "<vin>"
}
```

Se parsea a `StreamingMessage(vin, gcid, topic, entity_id, payload_timestamp,
data: dict[str, StreamingEntry], vehicle, raw_data)`. Y **`StreamingEntry` tiene
exactamente la misma forma que el `TelematicDataEntryDto` de la REST**: `value`,
`unit`, `timestamp`.

Eso último es la razón de que la tabla `readings` sirva para ambos sin
migración: la columna `source` (`rest` | `mqtt`) existe desde el primer commit
precisamente para esto.

**Dos avisos sobre la librería:**

- **No reconecta.** Un `MqttError` sale como `StreamingError` y mata el
  `async for`. El bucle de reconexión con backoff es **nuestro**, no suyo
  (sección 7).
- **No refresca el token.** `id_token` puede ser un `str` o un callable async,
  pero solo se consulta **al conectar**. Si el token caduca con la conexión
  abierta, la librería no se entera.

---

## 4. Arquitectura

Dos procesos, una base de datos, y una regla: **el que escribe nunca es el que
lee**.

```
       ┌─────────────────────────┐
       │  BMW CarData            │
       │  MQTT :9000  ·  REST    │
       └───────┬─────────┬───────┘
     stream    │         │   2 peticiones/día
    (sin cuota)│         │   (contenedor, TTL 12 h)
               ▼         ▼
    ┌──────────────┐   ┌──────────────────┐
    │ pitwall-     │   │ servidor MCP     │
    │ streamd      │   │ (stdio)          │
    │              │   │                  │
    │ escribe      │   │ lee readings     │
    │ readings     │   │ escribe cache,   │
    │ (source=mqtt)│   │ quota_log        │
    └──────┬───────┘   └────────┬─────────┘
           │                    │
           └────────┬───────────┘
                    ▼
            ┌───────────────┐
            │  pitwall.db   │
            │  (SQLite WAL) │
            └───────────────┘
```

**El daemon es opcional.** Sin él, el servidor MCP funciona exactamente como en
el Bloque A. Con él, las mismas herramientas ven más puntos en `readings` sin
cambiar una línea de su lógica: leen la tabla, no la fuente.

**El servidor MCP nunca abre el stream.** Es la forma más simple de garantizar
que no compite por la única conexión disponible. Si el usuario quiere streaming,
arranca el daemon; si no, no hay stream y punto. Esto elimina de raíz media
docena de carreras entre procesos.

### Reparto de escrituras

| Tabla | Daemon | Servidor MCP |
|---|---|---|
| `readings` | **escribe** (`source='mqtt'`) | lee |
| `stream_state` | **escribe** (cerrojo) | lee (para informar) |
| `api_cache` | no toca | escribe |
| `quota_log` | **no escribe nunca** | escribe |

`quota_log` en negrita por lo contrario: **el streaming no consume cuota REST**,
así que el daemon no debe registrar nada ahí. Si lo hiciera, el contador
mentiría y el servidor MCP se autobloquearía sin motivo.

### SQLite entre dos procesos

WAL ya está activado en `schema.sql`, que es lo que permite un escritor y varios
lectores concurrentes. Aun así, el daemon debe:

- Escribir en **lotes** (un `executemany` por mensaje MQTT), no fila a fila.
- Abrir con `busy_timeout` (5 s propuestos) para absorber el solape con una
  escritura de `api_cache` del servidor.
- No mantener transacciones largas abiertas. Nunca.

---

## 5. El cerrojo: `stream_state`

La tabla ya existe, vacía y sin uso:

```sql
CREATE TABLE stream_state (
    gcid TEXT PRIMARY KEY, vin TEXT, holder TEXT, holder_pid INTEGER,
    connected_since TEXT, last_message_at TEXT, note TEXT
);
```

### Protocolo

**Adquirir**, antes de tocar la red:

1. `INSERT` de la fila con `holder='daemon'`, `holder_pid`, `connected_since`.
   `gcid` es PRIMARY KEY, así que un segundo daemon choca. Eso es el cerrojo.
2. Si la fila ya existe, mirar `holder_pid`:
   - **Proceso vivo** → salir con un error que diga qué PID tiene la conexión
     y desde cuándo. No se intenta conectar.
   - **Proceso muerto** (cerrojo huérfano de un `kill -9`) → tomar la fila,
     dejando constancia en `note`.
3. Si `last_message_at` lleva más de N minutos sin moverse y el PID sigue vivo,
   **no** se roba el cerrojo: se avisa. Un vehículo en cochera puede estar
   semanas sin emitir, y confundir "silencioso" con "colgado" provocaría
   justo la doble conexión que intentamos evitar.

**Mantener**: `last_message_at` se actualiza con cada mensaje. Es el latido, y
además es lo que permite al servidor MCP decir "el daemon está vivo pero el
coche lleva 9 días callado".

**Liberar**: `DELETE` de la fila en el apagado ordenado (SIGTERM/SIGINT).

### Lo que este cerrojo no puede hacer

No ve a Home Assistant ni a evcc. Si el usuario tiene otro consumidor del
stream, ambos se pisarán y el síntoma será una **desconexión cíclica**: cada
cliente echa al otro y reconecta.

El daemon debe **detectar ese patrón** —N desconexiones no solicitadas en M
minutos, con reconexiones limpias entre medias— y decirlo explícitamente en el
log y en `stream_state.note`:

> Detectadas 6 desconexiones en 10 minutos. Es el síntoma típico de otro cliente
> conectado al mismo gcid (Home Assistant, evcc, otra instancia). BMW solo
> permite una conexión simultánea por cuenta.

Diagnosticar esto mal cuesta horas. Diagnosticarlo bien es una línea de log.

---

## 6. Autenticación, y el problema de verdad

El broker quiere `gcid` + **`id_token`**. Ambos vienen de `TokenResponse`, que
ya persistimos entera en `tokens.json`: `gcid` es obligatorio en el modelo, e
`id_token` opcional.

**VERIFICADO el 2026-09-07 con un login real.** BMW concedió los cuatro scopes
(`cardata:api:read openid cardata:streaming:read authenticate_user`) y devolvió
un `id_token` de 1324 caracteres junto con el `gcid`. Las dos credenciales que
necesita el broker MQTT existen, así que la Fase 2 es viable por este lado.

### El problema: el refresh token rota

Y este es el riesgo serio de la Fase 2.

`TokenManager.refresh()` obtiene un token nuevo y **BMW rota el refresh token**:
el anterior deja de valer. Con dos procesos compartiendo `tokens.json`:

1. El daemon refresca a las 10:00 y escribe el bundle nuevo.
2. El servidor MCP tenía el bundle viejo en memoria (`self._bundle`) y refresca
   a las 10:05 con el refresh token **ya invalidado**.
3. `invalid_grant`. **Y el usuario tiene que rehacer el login manual.**

Se evaluaron tres opciones:

| Opción | Cómo | Veredicto |
|---|---|---|
| **A. Cerrojo de fichero sobre el refresh** | Lock exclusivo alrededor del ciclo leer-refrescar-escribir. Quien no lo tiene, espera y **relee del disco**; si el bundle que aparece ya es válido, no refresca. | **ELEGIDA E IMPLEMENTADA** |
| B. Solo el daemon refresca | El servidor MCP se vuelve lector puro del fichero. | Descartada: acopla el MCP al daemon. Sin daemon, nadie refresca, y el daemon es opcional. |
| C. Ficheros de token separados | Dos device flows, dos credenciales. | Descartada: dos logins manuales cada 14 días, y probablemente dos gcid, con lo que el cerrojo del stream deja de tener sentido. |

### Estado: implementada (2026-09-07)

Ya está en `cardata/auth.py`, antes de que exista el daemon, porque el arreglo
toca código del Bloque A. **Son dos mecanismos, y defienden mitades distintas
del problema:**

1. **Nadie cachea el bundle en memoria.** `TokenStore.load()` va siempre al
   disco y `TokenManager.bundle` lo consulta cada vez. Esto es lo que evita el
   daño grave: usar un refresh token que el otro proceso ya rotó, que es lo que
   provoca el `invalid_grant` y el login manual.
2. **`TokenLock`, cerrojo exclusivo entre procesos**, sobre un fichero
   `tokens.json.lock` aparte. Aparte y no sobre el propio `tokens.json` porque
   este se reemplaza atómicamente en cada escritura, y un cerrojo sobre él se
   iría con el inodo viejo. Dentro del cerrojo se **relee y se comprueba**: si
   el proceso al que esperábamos ya renovó, se devuelve su access token y no se
   manda un segundo refresh. Esto evita la rotación inútil.

Detalles que importan:

- `fcntl.flock` en POSIX, `msvcrt.locking` en Windows. Ambos se liberan solos si
  el proceso muere, así que **no hay cerrojos huérfanos** que limpiar a mano.
- La adquisición **sondea con `await asyncio.sleep`** en vez de bloquear, para
  que esperar no congele el event loop de un servidor MCP que está atendiendo
  otras herramientas.
- Timeout de 30 s, con un error en español que explica que probablemente sea el
  daemon y que se reintente.
- `device_login` bloquea **solo la escritura**, no el sondeo: esperar a un humano
  con un navegador puede tardar minutos, y retener el cerrojo ese rato dejaría
  colgado a cualquier proceso que solo necesitaba renovar.

Cubierto por 16 tests en `tests/test_auth.py`, incluido uno que lanza **un
proceso de verdad** que retiene el cerrojo (no un mock: el daemon será otro
proceso, no una tarea de este event loop), y otro que comprueba que un refresh
fallido ni corrompe el fichero ni se queda el cerrojo.

### Renovación con la conexión abierta

El `id_token` dura ~1 h; la conexión MQTT, indefinidamente. La librería solo
consulta el token al conectar.

Comportamiento propuesto: **no reconectar preventivamente.** Mantener la sesión
mientras el broker la acepte, y refrescar el token únicamente cuando toque
reconectar por otro motivo. Reconectar cada hora "por si acaso" garantiza un
hueco de datos cada hora a cambio de un problema hipotético.

**Pendiente de comprobar empíricamente:** si BMW expulsa la sesión al caducar el
id_token. Se sabe dejando el daemon corriendo 3 h y mirando el log. Hasta
saberlo, no se escribe lógica para ninguna de las dos hipótesis.

---

## 7. Ciclo de vida y reconexión

La reconexión es nuestra, la librería no la trae.

```
arranque
   │
   ├─ leer .env, abrir SQLite, adquirir cerrojo ──── ocupado ──▶ salir con error claro
   │
   ├─ cargar tokens; sin id_token ──▶ salir explicando el device flow
   │
   ▼
 conectar ──▶ suscribir gcid/vin ──▶ async for mensaje
   ▲                                      │
   │                                      ├─ escribir readings (lote, source=mqtt)
   │                                      ├─ actualizar last_message_at
   │                                      └─ contar para la detección de rivales
   │                                      │
   └── StreamingError ◀────────────────────┘
         │
         └─ backoff exponencial 5s → 10 → 20 … tope 5 min, con jitter
```

Notas de diseño:

- **Backoff con jitter y tope de 5 min.** Sin jitter, dos clientes rivales
  reconectan sincronizados y el ciclo no se rompe nunca.
- **Suscripción a `gcid/{vin}`, no al comodín.** Es un solo coche; el comodín
  solo añade ruido.
- **QoS 0.** Es el valor por defecto de la librería y el adecuado: aquí no se
  pierde nada crítico, y un mensaje perdido se recupera solo con el siguiente.
- **El silencio es normal.** Este coche pasa semanas parado, y en sueño profundo
  la mayoría de funciones telemáticas están apagadas. Un keepalive vivo sin
  mensajes es el estado esperado, no un fallo. Nada de "watchdog" que reconecte
  por falta de datos: causaría el problema que pretende resolver.
- **Apagado limpio** con SIGTERM/SIGINT: cerrar MQTT, liberar el cerrojo, cerrar
  la base.

---

## 8. Escritura en `readings`

Sin cambios de esquema. Por cada `StreamingMessage`:

```
para cada (descriptor, entry) en message.data:
    INSERT OR IGNORE INTO readings
        (vin, descriptor, value, unit, source_timestamp, recorded_at, source)
    VALUES (..., entry.timestamp o '', ahora_utc, 'mqtt')
```

Es literalmente `HistoryStore.record(vin, entries, source="mqtt")`. El método ya
existe, ya está probado, y hay un test que comprueba que la misma lectura por
REST y por MQTT convive sin colisionar (el índice único incluye `source`).

**Filtro obligatorio:** descartar los descriptores fuera de
`ALL_CONFIRMED_DESCRIPTORS`. Si Data Selection incluye de más, el daemon no lo
guarda. La regla 5 no distingue entre fuentes: el catálogo manda igual por MQTT
que por REST.

**Valores crudos, como siempre.** `-NA-`, `ASN_isUnknown` y cualquier sorpresa
se guardan tal cual llegan. La interpretación es de `formatting.py`.

### Doble contabilidad REST/MQTT

El mismo descriptor puede llegar por ambas vías con **timestamps de BMW
distintos**, y entonces son dos filas legítimas. Es correcto: son dos
observaciones.

Pero el consumidor —`diagnose_software_update`— debe leer la serie
**consolidada por `source_timestamp`**, no contar filas. Si no, una lectura REST
que confirme un valor ya recibido por MQTT parecerá un punto nuevo, y la
herramienta creerá tener el doble de evidencia de la que tiene. Sería una forma
sutil de mentir sobre la confianza del diagnóstico, y está prohibida por la
regla 6 tanto como devolver un valor inventado.

---

## 9. Qué ve el usuario del MCP

El servidor MCP **no gana herramientas nuevas**. Gana dos cosas:

1. **Más puntos en la serie**, sin cambiar la lógica de ninguna herramienta.
2. **Procedencia más precisa.** `provenance_line` ya distingue `api`, `cache` e
   `history`; la fase 2 solo exige que el histórico diga si un punto vino de
   `rest` o de `mqtt`. La columna ya lo guarda.

Propuesta: **ampliar `get_api_quota`** (que ya informa del estado local y no
gasta cuota) con un bloque de estado del stream leído de `stream_state`:

```
Streaming (Fase 2): daemon activo desde 2026-09-01 08:12 UTC (PID 4711).
Último mensaje: hace 9 días — normal si el coche está en sueño profundo.
Lecturas por MQTT en los últimos 7 días: 0.
```

Y cuando no hay daemon, decirlo sin dramatismo: *"Sin daemon de streaming. Las
series solo crecen 2 veces al día vía REST."*

Esto es una decisión abierta (§12): puede que merezca una herramienta propia
`get_stream_status()` en vez de engordar `get_api_quota`.

---

## 10. Puesta en marcha (manual, como el resto)

1. **Data Selection en el portal.** Seleccionar a mano los 26 descriptores
   streamable del contenedor. No hay API. Es el paso que más se olvida y el que
   más tiempo hace perder.
2. Verificar que `tokens.json` tiene `gcid` e `id_token`.
3. `python -m pitwall_mcp.streamd` (o script equivalente), en primer plano.
4. Cuando funcione, servicio de usuario: systemd `--user` en Linux, Tarea
   Programada en Windows. **Sin auto-arranque por defecto**: el daemon toma la
   única conexión disponible, y eso tiene que ser una decisión explícita del
   usuario, no un efecto secundario de instalar el paquete.

---

## 11. Lo que NO se va a hacer

- **No hay comandos hacia el vehículo.** El stream es de lectura. La regla 1
  aplica igual aquí.
- **El daemon no llama a la REST.** Ni para refrescar contenedores, ni para
  rellenar huecos, ni para nada. Si necesitara REST, tendría que compartir el
  presupuesto de cuota con el servidor MCP, y eso convierte un contador simple
  en un problema de coordinación entre procesos. No merece la pena.
- **El daemon no borra ni compacta `readings`.** El crecimiento de la tabla es
  una decisión abierta (§12), no algo que un proceso de fondo haga por su cuenta
  con datos que el usuario no puede recuperar.
- **El servidor MCP no arranca el daemon** ni lo supervisa. Informa de su
  estado, nada más.
- **Nada de reconstruir el estado del coche en memoria.** La librería ofrece un
  árbol `Vehicle` tipado; para nosotros la base de datos es el estado. Un caché
  en memoria de un proceso que puede morir en cualquier momento solo añade una
  segunda verdad posible.

---

## 12. Decisiones abiertas

Ninguna se resuelve escribiendo código; todas necesitan un dato o una decisión
tuya.

1. **¿Emite este U11 algo por MQTT estando parado?** Es la pregunta que decide
   si la Fase 2 tiene sentido. Si en sueño profundo no manda nada, el streaming
   solo aporta durante los trayectos, y la serie de la batería en reposo —que es
   la que interesa— seguirá viniendo de la REST. **Se comprueba dejando el
   daemon corriendo una semana y contando filas.** No se escribe el resto hasta
   saberlo.
2. **¿Expulsa BMW la sesión al caducar el `id_token`?** Determina si hace falta
   reconexión programada (§6).
3. **¿`get_stream_status()` propia o bloque dentro de `get_api_quota`?** Me
   inclino por el bloque: una herramienta más es más superficie que mantener
   para información que casi siempre se consulta junto a la cuota.
4. **Crecimiento de `readings`.** 26 descriptores a ritmo de trayecto pueden ser
   muchas filas al año. ¿Retención indefinida, submuestreo por encima de cierta
   antigüedad, o nada hasta que moleste? Mi voto: **nada hasta medirlo**, y
   medirlo es el punto 1.
~~5. Refresh token compartido: ¿opción A confirmada?~~ **CERRADA el 2026-09-07:
   opción A, implementada en `cardata/auth.py`. Ver §6.**

---

## 13. Criterios de aceptación

Cuando se implemente, esto es lo que debe cumplirse antes de darlo por bueno:

- [ ] Con el daemon parado, el servidor MCP se comporta **exactamente** como en
      el Bloque A. Cero regresiones.
- [ ] Dos daemons a la vez: el segundo sale con un error que nombra el PID y la
      hora del primero. Nunca abre una segunda conexión.
- [ ] Un daemon muerto con `kill -9` deja un cerrojo que el siguiente arranque
      reclama, dejándolo anotado.
- [ ] El daemon **no escribe ni una fila en `quota_log`**. Test explícito.
- [ ] Un descriptor fuera del catálogo que llegue por el stream se descarta y se
      registra. Test con un mensaje fabricado.
- [ ] Un `-NA-` recibido por MQTT se guarda como texto, no como cero.
- [ ] Una desconexión provoca reconexión con backoff, sin perder el cerrojo.
- [ ] Seis desconexiones en diez minutos producen el aviso de "otro cliente
      conectado".
- [ ] Ningún test toca la red: broker MQTT falso o mensajes inyectados. La
      regla 8 no tiene excepción para MQTT.
- [x] El refresh concurrente entre daemon y servidor MCP **no invalida** el
      refresh token. *Hecho: `tests/test_auth.py`, con un proceso real para el
      cerrojo. Queda por verificar contra BMW de verdad en el Bloque B.*
