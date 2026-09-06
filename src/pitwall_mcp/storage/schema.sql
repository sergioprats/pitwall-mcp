-- pitwall-mcp — esquema SQLite.
-- Todas las marcas de tiempo son TEXT en ISO-8601 UTC ('YYYY-MM-DDTHH:MM:SSZ').
-- SQLite no tiene tipo fecha; el orden lexicografico de este formato coincide
-- con el cronologico, asi que las comparaciones y los BETWEEN son correctos.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;          -- version de esquema, para migraciones

-- ---------------------------------------------------------------------------
-- api_cache — respuesta CRUDA por endpoint + VIN + contenedor.
-- Se guarda el JSON tal cual llega, sin normalizar: si manana entendemos mejor
-- una estructura (conditionBasedServices), la reinterpretamos sin volver a
-- gastar cuota.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_cache (
    endpoint      TEXT NOT NULL,        -- 'mappings' | 'basicData' | 'telematicData'
                                        -- | 'tyreDiagnosis' | 'containers'
    vin           TEXT NOT NULL DEFAULT '',   -- '' para endpoints sin VIN
    container_id  TEXT NOT NULL DEFAULT '',   -- '' para endpoints sin contenedor
    response_json TEXT NOT NULL,        -- cuerpo crudo de la respuesta
    http_status   INTEGER NOT NULL,
    fetched_at    TEXT NOT NULL,        -- cuando lo LEIMOS nosotros
    expires_at    TEXT NOT NULL,        -- fetched_at + TTL del endpoint
    PRIMARY KEY (endpoint, vin, container_id)
);

CREATE INDEX IF NOT EXISTS idx_api_cache_expires
    ON api_cache (expires_at);

-- ---------------------------------------------------------------------------
-- quota_log — UNA FILA POR PETICION REST REALMENTE ENVIADA.
-- Los aciertos de cache NO se registran aqui: esta tabla es la unica verdad
-- sobre cuanta cuota se ha gastado. El contador diario se DERIVA de ella,
-- nunca se almacena un contador aparte que pueda desincronizarse.
-- Ventana asumida: dia natural UTC. Suposicion: se desconoce el huso real
-- en el que BMW resetea las 24 h (ver CLAUDE.md, riesgo 5).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quota_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_at TEXT NOT NULL,         -- ISO-8601 UTC del envio
    endpoint     TEXT NOT NULL,
    vin          TEXT,                  -- NULL si el endpoint no lleva VIN
    http_status  INTEGER,               -- NULL si la peticion ni llego a responder
    error_id     TEXT,                  -- exveErrorId, p.ej. 'CU-429'
    note         TEXT
);

CREATE INDEX IF NOT EXISTS idx_quota_log_requested_at
    ON quota_log (requested_at);

-- ---------------------------------------------------------------------------
-- readings — historico de valores telematicos.
-- El VALOR esta en la SERIE: diagnose_software_update no puede concluir nada
-- con una foto puntual.
--
-- 'value' se guarda como TEXT porque la API devuelve TODO como cadena,
-- incluido '-NA-'. No se convierte ni se normaliza al guardar: se guarda
-- lo que BMW dijo. La interpretacion es de la capa de formato.
--
-- 'source' existe desde el primer dia para que el daemon MQTT de la Fase 2
-- escriba en esta misma tabla sin migracion. Hoy solo se escribe 'rest'.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS readings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    vin              TEXT NOT NULL,
    descriptor       TEXT NOT NULL,     -- technical_descriptor del catalogo
    value            TEXT,              -- crudo, tal cual lo dio BMW
    unit             TEXT,
    source_timestamp TEXT,              -- el que da BMW; '' si no lo da
    recorded_at      TEXT NOT NULL,     -- cuando lo guardamos nosotros
    source           TEXT NOT NULL      -- 'rest' | 'mqtt'
        CHECK (source IN ('rest', 'mqtt'))
);

-- Deduplicacion: la misma lectura leida dos veces (dos llamadas dentro del
-- TTL, o REST y MQTT solapados en la Fase 2) no debe inflar la serie.
-- Los NULL son distintos entre si en un indice unico de SQLite, por eso se
-- normaliza source_timestamp a '' cuando BMW no lo da.
CREATE UNIQUE INDEX IF NOT EXISTS idx_readings_unique
    ON readings (vin, descriptor, source_timestamp, source);

CREATE INDEX IF NOT EXISTS idx_readings_series
    ON readings (vin, descriptor, recorded_at);

-- ---------------------------------------------------------------------------
-- stream_state — RESERVADA PARA LA FASE 2. Hoy no se escribe.
-- BMW permite UNA sola conexion de streaming simultanea por gcid. Esta tabla
-- es el cerrojo: quien la tiene tomada y desde cuando. El conflicto es real
-- tambien frente a otros consumidores (Home Assistant, evcc), a los que esta
-- tabla no puede ver: es un cerrojo cooperativo, no una garantia.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stream_state (
    gcid            TEXT PRIMARY KEY,
    vin             TEXT,
    holder          TEXT,               -- 'daemon' | 'mcp-server' | libre
    holder_pid      INTEGER,
    connected_since TEXT,
    last_message_at TEXT,
    note            TEXT
);
