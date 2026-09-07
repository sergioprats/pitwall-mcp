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
| `list_vehicles()` | sí (1/mes con caché) | esqueleto |
| `get_vehicle_basic_data()` | sí (1/mes con caché) | esqueleto |
| `report_product_update_step()` | sí (comparte caché con `basicData`) | esqueleto |
| `get_telematic_data()` | sí (2/día con caché) | esqueleto |
| `get_vehicle_status()` | sí (comparte caché) | esqueleto |
| `get_tyre_diagnosis()` | sí (1/semana con caché) | esqueleto |
| `get_maintenance_summary()` | sí (compuesta) | esqueleto |
| `diagnose_software_update()` | sí (compuesta) | esqueleto |

Los esqueletos están registrados y devuelven un mensaje claro que dice qué falta
por configurar y qué queda por implementar. **No inventan datos.** Su lógica se
escribirá cuando se haya visto llegar una respuesta real y se haya grabado como
fixture: la estructura de `vehicle.status.conditionBasedServices` no está
documentada por BMW y es el riesgo número uno del proyecto.

---

## Instalación

Requiere **Python 3.12+**.

```bash
git clone https://github.com/panesoft/pitwall-mcp
cd pitwall-mcp
python -m venv .venv
.venv/Scripts/activate        # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
```

### 1. Configuración

```bash
cp .env.example .env
```

Rellena `PITWALL_CLIENT_ID` con el client id de tu aplicación CarData, creada en
el [portal BMW CarData](https://bmw-cardata.bmwgroup.com). Necesitas los scopes
`cardata:api:read` y, si algún día usas streaming, `cardata:streaming:read`.

### 2. Login

```bash
python scripts/login.py
```

Device flow: el script imprime una URL y un código, tú lo autorizas en el
navegador. Los tokens se guardan en `~/.config/pitwall-mcp/tokens.json` con
permisos `600`, **fuera del repositorio**.

El refresh token dura **14 días**. El servidor avisa de forma visible cuando
quedan menos de 3; si caduca, hay que repetir este paso a mano.

### 3. Contenedor

```bash
python scripts/bootstrap_containers.py --dry-run   # muestra qué enviaría
python scripts/bootstrap_containers.py --create    # gasta 1 petición
```

Copia el `containerId` resultante a `PITWALL_CONTAINER_ID` en el `.env`. El
servidor MCP solo consume ese id: nunca crea ni borra nada.

### 4. Arrancar

```bash
python -m pitwall_mcp
```

Transporte **stdio**. Para Claude Desktop o cualquier cliente MCP:

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
python -m pytest                              # ningún test toca la red
python scripts/refresh_catalogue.py --check   # ¿ha cambiado el catálogo?
```

**Ningún test hace llamadas reales.** Todos van contra fixtures grabados a mano
a partir de los esquemas del swagger. Una suite que gaste cuota es un bug.

Documentación relevante:

- [`CLAUDE.md`](CLAUDE.md) — documento de gobierno del repositorio.
- [`docs/paso-0-descriptores.md`](docs/paso-0-descriptores.md) — los descriptores
  confirmados, lo que no existe, y las rarezas del catálogo y del swagger.

---

## Estado

Bloque A completo: todo lo que funciona sin credenciales de BMW. La Fase 2
(daemon MQTT de streaming) es **diseño, no código**; el esquema SQLite ya reserva
la columna `source` y la tabla `stream_state` para no necesitar migración.

---

## Licencia

MIT, © 2026 Panesoft. Ver [LICENSE](LICENSE).

`spec/telematic_catalogue.json` procede del proyecto
[zweckj/bmw-cardata](https://github.com/zweckj/bmw-cardata). Antes de publicar
este repositorio hay que revisar las condiciones de uso B2C de BMW CarData, en
concreto si permiten redistribuir el catálogo telemático; si no lo permiten, el
fichero sale del repo y se descarga en tiempo de instalación con
`scripts/refresh_catalogue.py`.
