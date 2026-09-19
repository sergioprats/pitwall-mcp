# Publicar pitwall-mcp

Guía para quien publica el paquete, no para quien lo usa. Si solo quieres
instalarlo, el [README](../README.md) es todo lo que necesitas.

El orden de los cuatro pasos **no es opcional**: el registro MCP comprueba el
paquete de PyPI, y PyPI enlaza al repositorio. Publicar al revés obliga a
rehacer cosas que no siempre se pueden rehacer.

---

## Antes de nada: lo que no debe salir

Este repositorio tiene dos copias de seguridad de historiales antiguos y una
rama de respaldo. **Ninguna sube con un `git push` normal ni con `--all`, pero
todas suben con `--mirror`.**

| Referencia | Qué contiene |
|---|---|
| `refs/original/` | el historial de antes de purgar `spec/*.json`, con los documentos de BMW |
| `refs/original-dtc/` | el historial de antes de anonimizar, con los códigos de avería reales |
| `backup-antes-de-purgar` | rama local, 24 commits que no están en `main` |

**Nunca uses `git push --mirror` en este repositorio.** Comprueba qué hay antes
de subir nada:

```bash
git for-each-ref refs/original refs/original-dtc --format='%(refname)'
git branch --no-merged main
```

Si decides borrarlas, no se deshace. Sacar antes la rama a un fichero fuera del
repositorio cuesta poco:

```bash
git bundle create ../backup-antes-de-purgar.bundle backup-antes-de-purgar
git branch -D backup-antes-de-purgar
git update-ref -d refs/original/refs/heads/master
git update-ref -d refs/original-dtc/refs/heads/main
```

Y una última comprobación de que el árbol que vas a publicar no lleva nada que
no deba: el catálogo y el swagger de BMW están en `.gitignore`, así que no
deberían aparecer.

```bash
git ls-files spec/          # solo spec/README.md
```

---

## 1. Subir y hacer público el repositorio

```bash
git push origin main        # nunca --all ni --mirror
gh repo edit sergioprats/pitwall-mcp --visibility public
```

Antes de hacerlo público conviene mirar el historial una vez más, porque un
repositorio público no se "despublica" del todo: lo que estuvo visible puede
quedar cacheado.

```bash
git log --oneline origin/main..main       # lo que vas a subir
git log -p main | grep -iE "client_id|WBA[0-9A-Z]{14}" | head
```

Los VIN que aparecen en el historial son todos de prueba (`...FAKE01`,
`WBAOTROVIN1234567`). El VIN real, el client id, el `containerId` y el gcid no
están.

---

## 2. Publicar en PyPI

El nombre `pitwall-mcp` estaba libre. **Un número de versión publicado no se
puede reutilizar ni borrar**, así que la 0.1.0 se gasta una sola vez: si algo
sale mal, el arreglo es 0.1.1, no volver a subir la 0.1.0.

```bash
.venv/Scripts/python.exe -m pip install build twine
.venv/Scripts/python.exe -m build
.venv/Scripts/python.exe -m twine check dist/*
```

Comprueba que el wheel no lleva documentos de BMW ni nada de `captures/`:

```bash
.venv/Scripts/python.exe -m zipfile -l dist/pitwall_mcp-0.1.0-py3-none-any.whl
```

Tiene que aparecer `schema.sql` (lo necesita la base de datos) y **no** debe
aparecer ningún `spec/*.json`.

Para subirlo hace falta una cuenta de PyPI con token, o configurar *trusted
publishing* desde GitHub, que evita guardar el token en ninguna parte:

```bash
.venv/Scripts/python.exe -m twine upload dist/*
```

---

## 3. Registrar en el registro MCP

El registro exige demostrar que el paquete de PyPI es tuyo, y lo hace leyendo un
comentario del README publicado en PyPI:

```html
<!-- mcp-name: io.github.sergioprats/pitwall-mcp -->
```

**Ese nombre y el `name` de `server.json` tienen que coincidir.** Están los dos
puestos; si cambias uno, cambia el otro, más el `Homepage` de `pyproject.toml`.

El esquema del registro **ya cambió una vez** (de `2025-07-09` a `2025-12-11`,
que además pasó de snake_case a camelCase y limitó la descripción a 100
caracteres). Antes de publicar, valida contra el esquema que declare tu
`server.json` en ese momento:

```bash
curl -sSo /tmp/mcp-schema.json "$(python -c "import json;print(json.load(open('server.json'))['\$schema'])")"
.venv/Scripts/python.exe -c "
import json, jsonschema
schema = json.load(open('/tmp/mcp-schema.json', encoding='utf-8'))
server = json.load(open('server.json', encoding='utf-8'))
errors = list(jsonschema.Draft7Validator(schema).iter_errors(server))
print(errors or 'server.json valido')"
```

Después, con la cuenta de GitHub `sergioprats`:

```bash
mcp-publisher login github
mcp-publisher publish
```

La cuenta importa: el espacio de nombres es `io.github.sergioprats`, no
`panesoft`. La organización `panesoft` existe en GitHub, pero la cuenta de
trabajo no es miembro y el registro no dejaría demostrar la propiedad.

---

## 4. Después de publicar

- **El catálogo telemático no viaja en el paquete.** Quien lo instale tiene que
  ejecutar `pitwall-mcp --fetch-catalogue` una vez. Está en el README, pero es
  el primer sitio donde alguien se atasca.
- La versión vive en tres ficheros: `pyproject.toml`, `server.json` (dos veces,
  la del servidor y la del paquete) y el tag de git. Que no se queden dispares.
- `bmw-cardata` está pinneada a `0.1.0a3`, una alfa. Si publica una versión
  nueva, hay que probarla contra los fixtures antes de mover el pin: toda la
  librería entra por `cardata/client.py` justamente para que ese cambio se toque
  en un solo sitio.
