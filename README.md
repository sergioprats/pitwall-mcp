# pitwall-mcp

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
| `diagnose_software_update()` | sí (compuesta) | esqueleto |

Las dos herramientas marcadas con una reserva **funcionan y declaran la
ausencia**: no rellenan el hueco con ceros ni con un valor plausible. `puStep`
no llega en `/basicData` para este coche, y el diagnóstico de neumáticos vuelve
con etiquetas y ceros de relleno que no son medidas.

`diagnose_software_update()` sigue siendo un esqueleto, y no por falta de código:
de las tres condiciones que BMW documenta para no ofrecer una actualización,
CarData sólo permite observar una, y en la primera lectura real
`stateOfCharge` y `deepSleepModeActive` llegaron vacíos. Además necesita una
**serie**, no una foto. El esqueleto dice exactamente eso y no inventa un
veredicto.

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

Lo primero que enseña no es un número, es cuántos de los 32 descriptores del
contenedor traen valor de verdad, y en cuál de los tres estados vacíos está cada
uno de los demás.

---

## Instalación

Requiere **Python 3.12+**.

```bash
git clone https://github.com/panesoft/pitwall-mcp
cd pitwall-mcp
python -m venv .venv
pip install -e ".[dev]"
```

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
- [`docs/paso-0-descriptores.md`](docs/paso-0-descriptores.md) — los descriptores
  confirmados, lo que no existe, y las rarezas del catálogo y del swagger.

---

## Estado

Todas las herramientas están implementadas contra respuestas reales grabadas
como fixtures, salvo `diagnose_software_update()`, que espera a tener serie
suficiente. La Fase 2 (daemon MQTT de streaming) es **diseño, no código**; el
esquema SQLite ya reserva la columna `source` y la tabla `stream_state` para no
necesitar migración.

Queda abierto si los 11 descriptores que vuelven vacíos se rellenan con el coche
despierto: sólo se sabrá leyendo con el contacto dado.

---

## Licencia

MIT, © 2026 Panesoft. Ver [LICENSE](LICENSE).

`spec/telematic_catalogue.json` procede del proyecto
[zweckj/bmw-cardata](https://github.com/zweckj/bmw-cardata). Antes de publicar
este repositorio hay que revisar las condiciones de uso B2C de BMW CarData, en
concreto si permiten redistribuir el catálogo telemático; si no lo permiten, el
fichero sale del repo y se descarga en tiempo de instalación con
`scripts/refresh_catalogue.py`.
