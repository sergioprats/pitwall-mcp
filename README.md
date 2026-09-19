# pitwall-mcp

<!-- mcp-name: io.github.sergioprats/pitwall-mcp -->

Servidor MCP local, **de solo lectura**, sobre la API BMW CarData de un
BMW X1 sDrive18i (U11, gasolina, España).

> **Proyecto no afiliado a BMW AG.** "BMW" y "CarData" son marcas de sus
> respectivos propietarios. Este proyecto no está respaldado, patrocinado ni
> revisado por BMW.

Le da a un asistente acceso a los datos de mantenimiento del coche —
kilometraje, avisos CBS, presiones de neumáticos, batería de 12 V — sin que
pueda tocar nada del vehículo, y sin agotar la cuota diaria de la API.

---

## Qué hace y qué no hace

**Sí:**

- Lee datos telemáticos, datos básicos del vehículo y el diagnóstico de
  neumáticos, siempre a través de caché.
- Guarda un histórico local en SQLite, para poder razonar sobre **series** y no
  sobre fotos puntuales.
- Dice de dónde viene cada dato: timestamp de BMW, timestamp de la lectura, y si
  es API, caché o histórico.
- Cuando un dato no existe, explica **por qué** no existe.

**No, y no lo hará:**

- Ninguna herramienta escribe, manda, activa ni insinúa que puede actuar sobre
  el coche. CarData es una API de lectura.
- Ninguna herramienta crea ni borra contenedores. Eso vive en un script que se
  lanza a mano.
- No hay `get_software_version`: **ese descriptor no existe** en el catálogo de
  BMW. Lo único disponible es `puStep`, el paso de actualización de producto.

---

## La cuota manda

La API REST de CarData está limitada a **50 peticiones cada 24 h y por cuenta**.
Pasarse devuelve `403 CU-429` hasta el día siguiente, y el contador es de BMW:
incluye cualquier otra aplicación que use la misma cuenta.

Por eso:

- Toda petición pasa obligatoriamente por caché y por un contador local.
- El tope local por defecto es de **20 peticiones/día**, para dejar margen.
- Con un TTL de 12 h en el contenedor de mantenimiento, el uso normal son
  **2 peticiones al día**.
- `search_descriptors` y `get_api_quota` **no gastan cuota nunca**.

Consulta `get_api_quota` antes de encadenar llamadas.

| Dato | TTL |
|---|---|
| `/telematicData` (contenedor de mantenimiento) | 12 h |
| `/smartMaintenanceTyreDiagnosis` | 7 días |
| `/basicData` | 30 días |
| `/mappings` | 30 días |

---

## Herramientas

| Herramienta | Gasta cuota | Estado |
|---|---|---|
| `search_descriptors(query, limit, include_electric)` | no | **funciona** |
| `get_api_quota()` | no | **funciona** |
| `list_vehicles()` | sí (1/mes con caché) | **funciona** |
| `get_vehicle_basic_data()` | sí (1/mes con caché) | **funciona** |
| `report_product_update_step()` | sí (comparte caché con `basicData`) | funciona, pero este vehículo **no devuelve `puStep`** |
| `get_telematic_data()` | sí (2/día con caché) | **funciona** |
| `get_vehicle_status()` | sí (comparte caché) | **funciona** |
| `get_tyre_diagnosis()` | sí (1/semana con caché) | funciona, pero este vehículo **devuelve la estructura vacía** |
| `get_maintenance_summary()` | sí (compuesta) | **funciona** |
| `diagnose_software_update()` | sí (compuesta, comparte caché) | **funciona**, con veredicto acotado |
| `get_fuel_status()` | sí (comparte caché) | **funciona** |
| `get_fault_memory()` | sí (comparte caché) | **funciona**; no traduce los códigos |

Las dos herramientas marcadas con una reserva **funcionan y declaran la
ausencia**: no rellenan el hueco con ceros ni con un valor plausible. `puStep`
no llega en `/basicData` para este coche, y el diagnóstico de neumáticos vuelve
con etiquetas y ceros de relleno que no son medidas.

`get_vehicle_status()` y `get_maintenance_summary()` muestran también los avisos
**Check Control**, y añaden una línea `OJO` cuando alguna partida CBS deja de
estar en `OK`. La cifra global de próximo servicio no basta: con los frenos
delanteros en `PENDING` a 1.900 km, marcaba 13.560.

`get_maintenance_summary()` añade además, **sin gastar peticiones**, una
previsión en semanas y fechas y la tendencia de presiones por eje. Las dos salen
del histórico local, y el porqué de cada una está en
[Por qué esto y no la app oficial](#por-qué-esto-y-no-la-app-oficial). La
previsión usa la media semanal de BMW o la del histórico, la mayor de las dos,
para no quedarse corta, y avisa cuando una partida vence antes por kilómetros
que por fecha.

Dos herramientas más con datos que la app oficial no enseña:

- **`get_fuel_status()`**: depósito y autonomía, los repostajes que detecta en
  el histórico y el consumo real desde el último. Solo da el consumo a partir de
  300 km, y siempre con su margen, porque el aforador puede desviarse hasta 6 L.
  Muestra también el consumo homologado (OBFCM), pero como lo que es: una cifra
  de por vida que en este coche no se mueve desde octubre de 2024, no el consumo
  de hoy.
- **`get_fault_memory()`**: la memoria de averías que el coche guarda para el
  taller, agrupada por centralita, y **qué códigos aparecen o desaparecen entre
  lecturas**. No traduce ningún código: su significado no está en el catálogo
  de BMW, y darlo sería inventarlo.

`diagnose_software_update()` razona sobre la **serie** del histórico, no sobre
una foto. De las tres condiciones que BMW documenta para no ofrecer una
actualización, CarData sólo permite observar una, y la herramienta declara las
otras dos como `NO OBSERVABLE POR CARDATA` en lugar de razonar como si las
hubiera descartado. Deja fuera cualquier voltaje de 13,5 V o más, porque eso es
el alternador cargando y no dice nada de la batería en reposo. Si no le quedan al
menos dos observaciones en reposo, responde **SIN VEREDICTO** y dice qué le
falta. Y como `isIgnitionOn` llega vacío, avisa de que parte de cualquier
pendiente puede ser sólo motor en marcha frente a motor parado, no una batería
descargándose.

---

## Por qué esto y no la app oficial

La app te enseña el estado de hoy. Esto guarda cada lectura, así que puede
comparar. Cuatro cosas concretas que salen de ahí, todas vistas en este coche:

- **Cuándo, no solo cuánto.** La app dice "frenos delanteros, 1.600 km". El
  resumen dice unas 2,8 semanas, hacia el 5 de octubre, al ritmo real de uso, y
  avisa si una partida vence antes por kilómetros que por fecha.
- **La cifra global no basta, y aquí se ve.** Con los frenos en `PENDING` a
  1.900 km, `serviceDistance.next` marcaba 13.560. Quedarse con ese número
  habría escondido lo único urgente, así que toda partida que BMW no marque
  `OK` sale con un aviso propio.
- **Neumáticos sin confundir el calor con una fuga.** El objetivo de presión de
  BMW no es fijo: se ha visto en 250 kPa en frío y en 290 con el neumático
  caliente. Comparar la presión de hoy con un objetivo de otro momento da un
  diferencial falso, así que cada rueda se compara con su pareja de eje **en la
  misma lectura**, que comparte temperatura y carga.
- **Qué cambia en la memoria de averías.** El 18 de septiembre, entre dos
  lecturas, entraron 11 códigos y salieron otros 11: apareció una centralita
  entera y desaparecieron tres. Eso no se ve en ningún sitio si no guardas la
  lectura anterior.

Y lo que **no** aporta, para que no haya malentendidos:

- **No traduce los códigos de avería.** Su significado no está en el catálogo de
  BMW; inventarlo sería peor que no darlo. Para eso está una herramienta de
  diagnóstico por OBD, del estilo de BimmerLink, que además los lee con su
  descripción y puede borrarlos.
- **No escribe nada.** Ni codificación, ni resets, ni una sola orden al coche.
  Eso es terreno de BimmerCode y de un adaptador OBD, con el coche delante.
- **No inventa lo que no llega.** Un dato ausente, uno presente pero vacío y un
  `-NA-` son tres cosas distintas, y las herramientas las distinguen en vez de
  enseñar un cero. De los 42 descriptores del contenedor, en la última lectura
  llegaron 28 con valor y 14 vacíos.

---

## El informe local

El histórico que va guardando el servidor no se ve en ningún sitio: las
herramientas contestan con la foto del momento, no con la serie. Para mirar la
serie hay una página HTML que se genera a mano desde la SQLite:

```bash
.venv/Scripts/python.exe scripts/report.py              # captures/report-*.html
.venv/Scripts/python.exe scripts/report.py --full-vin   # con el VIN entero
```

**No gasta ninguna petición.** No usa el token, no llama a ningún endpoint y no
toca el contador de cuota: sólo lee la base de datos local. La página es un
único fichero sin JavaScript ni dependencias externas, imprime bien, y sale con
el VIN enmascarado a `captures/`, que está en `.gitignore`.

Lo primero que enseña no es un número, es cuántos de los 42 descriptores del
contenedor traen valor de verdad, y en cuál de los tres estados vacíos está cada
uno de los demás.

---

## Instalación

Requiere **Python 3.12+**.

```bash
git clone https://github.com/sergioprats/pitwall-mcp
cd pitwall-mcp
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"   # Linux/macOS: .venv/bin/python
.venv/Scripts/python.exe scripts/refresh_catalogue.py
```

Ese último paso **no es opcional**: el catálogo telemático es un documento de
BMW y este repositorio no lo redistribuye, así que hay que descargarlo una vez.
Viene de [`zweckj/bmw-cardata`](https://github.com/zweckj/bmw-cardata) (MIT), no
de la API de CarData, de modo que no gasta ninguna petición de tu cuota. Los
detalles están en [`spec/README.md`](spec/README.md).

Si instalas el paquete en vez de clonar el repositorio, no tienes `scripts/`, así
que el propio servidor sabe descargarlo:

```bash
pitwall-mcp --fetch-catalogue          # junto a la base de datos
pitwall-mcp --fetch-catalogue --out /otra/ruta.json
```

Se niega a escribir si lo que baja no es un catálogo —un portal cautivo
responde 200 con HTML—, así que un catálogo que ya te funcionaba no se pierde
por una descarga mala.

Los comandos de abajo usan el intérprete del entorno virtual de forma explícita
(`.venv/Scripts/python.exe` en Windows, `.venv/bin/python` en Linux y macOS).
Si activas el entorno (`.venv/Scripts/activate`, o `source .venv/bin/activate`),
te basta con `python`. Sin activar y con el `python` del sistema, fallará con
`ModuleNotFoundError: No module named 'bmw_cardata'`.

### 1. Configuración

```bash
cp .env.example .env
```

Rellena `PITWALL_CLIENT_ID` con el client id de tu aplicación CarData, creada en
el [portal BMW CarData](https://bmw-cardata.bmwgroup.com). Necesitas los scopes
`cardata:api:read` y, si algún día usas streaming, `cardata:streaming:read`.

### 2. Login

```bash
.venv/Scripts/python.exe scripts/login.py
```

Device flow: el script imprime una URL y un código, tú lo autorizas en el
navegador. Los tokens se guardan en `~/.config/pitwall-mcp/tokens.json` con
permisos `600`, **fuera del repositorio**.

El refresh token dura **14 días**. El servidor avisa de forma visible cuando
quedan menos de 3; si caduca, hay que repetir este paso a mano.

### 3. Contenedor

```bash
.venv/Scripts/python.exe scripts/bootstrap_containers.py --dry-run   # muestra qué enviaría
.venv/Scripts/python.exe scripts/bootstrap_containers.py --create    # gasta 1 petición
```

Copia el `containerId` resultante a `PITWALL_CONTAINER_ID` en el `.env`. El
servidor MCP solo consume ese id: nunca crea ni borra nada.

El contenedor pide 42 descriptores. Además del mantenimiento, incluye el
depósito, la autonomía, el consumo homologado, la memoria de averías y la
temperatura del motor. En el coche de referencia llegan todos menos tres. Si tu
contenedor es de antes de esta ampliación, créalo de nuevo con el mismo script.

### 4. Arrancar

```bash
.venv/Scripts/python.exe -m pitwall_mcp
```

Transporte **stdio**. El `.env` se busca en el directorio desde el que se lanza
el servidor y, si no aparece, en la raíz del repositorio — un cliente MCP arranca
el proceso con el directorio de trabajo que le apetece. Si tu `.env` vive en otro
sitio, indícalo con `PITWALL_ENV_FILE`.

Para Claude Desktop o cualquier cliente MCP:

```json
{
  "mcpServers": {
    "pitwall": {
      "command": "/ruta/al/.venv/bin/python",
      "args": ["-m", "pitwall_mcp"],
      "env": { "PITWALL_CLIENT_ID": "...", "PITWALL_VIN": "...", "PITWALL_CONTAINER_ID": "..." }
    }
  }
}
```

---

## Desarrollo

```bash
.venv/Scripts/python.exe -m pytest                              # ningún test toca la red
.venv/Scripts/python.exe scripts/refresh_catalogue.py --check   # ¿ha cambiado el catálogo?
```

**Ningún test hace llamadas reales.** Todos van contra fixtures grabados a mano
a partir de los esquemas del swagger. Una suite que gaste cuota es un bug.

Documentación relevante:

- [`CLAUDE.md`](CLAUDE.md) — documento de gobierno del repositorio.
- [`docs/resumen-del-proyecto.md`](docs/resumen-del-proyecto.md) — qué hace, cómo
  lo hace y para qué sirve, para quien llega de nuevas.
- [`docs/paso-0-descriptores.md`](docs/paso-0-descriptores.md) — los descriptores
  confirmados, lo que no existe, y las rarezas del catálogo y del swagger.
- [`docs/publicacion.md`](docs/publicacion.md) — cómo se publica esto: el orden
  de los pasos, y qué no debe salir del repositorio.
- [`docs/streaming-design.md`](docs/streaming-design.md) — la Fase 2, el daemon
  MQTT. Diseño, no código.

---

## Estado

Todas las herramientas están implementadas contra respuestas reales grabadas
como fixtures. La Fase 2 (daemon MQTT de streaming) es **diseño, no código**; el
esquema SQLite ya reserva la columna `source` y la tabla `stream_state` para no
necesitar migración.

Lo que ya se sabe de este coche, verificado con lecturas reales entre el 7 y el
18 de septiembre de 2026:

- **13 de los 42 descriptores del contenedor llegan siempre vacíos**, incluso
  con el contacto dado y el coche rodando. Entre ellos están el estado de carga
  de la batería, el sueño profundo, el contacto, si el coche se mueve y las
  temperaturas de los neumáticos. No es falta de lectura: este coche no los
  emite. El decimocuarto, el desglose CBS, va y viene: unas lecturas trae las
  partidas y otras llega vacío.
- **El voltaje de la batería de 12 V viaja en un grupo que por REST se refresca
  muy de tarde en tarde.** Se le han visto cuatro refrescos, y los cuatro con el
  motor en marcha. Los cinco voltajes del histórico —14,39 / 14,35 / 14,77 /
  13,92 / 14,80 V— son tensión de alternador, por encima del umbral de 13,5 V.
  **Leer más veces no lo arregla**, y está comprobado: ni una noche entera
  parado ni un trayecto por medio lo refrescan. Por REST no hay forma de
  construir una serie de la batería en reposo, y por eso `diagnose_software_update`
  se queda en `SIN VEREDICTO` en vez de fingir uno. La única vía conocida para
  esa serie es el streaming de la Fase 2.
- Quedan abiertos otros tres puntos:
  - por qué el contador de CBS dice 9 cuando el desglose trae 5 partidas, y por
    qué la memoria de averías anuncia 72 códigos —luego 67— trayendo 44
    entradas en las dos lecturas;
  - en qué huso horario reinicia BMW la cuota diaria;
  - qué es el campo de kilómetros que acompaña a los avisos Check Control. Van
    dos interpretaciones probadas y las dos han caído, así que la herramienta lo
    enseña pelado y dice que BMW no lo documenta.

---

## Licencia

MIT, © 2026 Panesoft. Ver [LICENSE](LICENSE).

**Los documentos de BMW no se redistribuyen desde aquí.** Ni el catálogo
telemático ni la especificación OpenAPI están en el control de versiones: se
descargan en tu máquina, y `spec/` está en `.gitignore`. Ver
[`spec/README.md`](spec/README.md).

Proyecto **no afiliado a BMW AG**. "BMW" y "CarData" son marcas de sus
respectivos propietarios.
