# spec/ — documentos que NO viven en este repositorio

Este directorio está vacío en el control de versiones a propósito. Los dos
ficheros que van aquí son documentos de BMW, y este proyecto **no los
redistribuye**: se obtienen una vez, en tu máquina.

## `telematic_catalogue.json` — obligatorio

Es la única fuente de verdad sobre qué descriptores existen. Sin él, el
servidor arranca pero cualquier herramienta que consulte el catálogo falla con
un mensaje que te manda aquí.

```bash
python scripts/refresh_catalogue.py     # desde un clon del repositorio
pitwall-mcp --fetch-catalogue           # desde el paquete instalado
```

Descarga de `github.com/zweckj/bmw-cardata` (licencia MIT), **no de la API de
BMW**: no gasta ninguna petición de tus 50 diarias. Vuelve a ejecutarlo con
`--check` cuando quieras saber si el catálogo ha cambiado río arriba.

Si prefieres tenerlo en otro sitio, apunta `PITWALL_CATALOGUE_PATH` a él. El
servidor lo busca en este orden: la copia empaquetada dentro del wheel, luego
`~/.local/share/pitwall-mcp/telematic_catalogue.json`, y por último `spec/`.

## `swagger-customer-api-v1.json` — opcional

La especificación OpenAPI de la API de clientes de CarData, tal cual la publica
BMW. Se descarga desde el portal de CarData con tu propia cuenta.

No hace falta para nada en tiempo de ejecución: ningún módulo lo lee. Sirve
para verificar a mano lo que dice la documentación cuando una respuesta real no
coincide con ella, que en este vehículo ha pasado ya varias veces.
