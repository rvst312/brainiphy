# cerebro-empresarial

CLI (`cerebro`) para inicializar y mantener "cerebros" (grafos de conocimiento vía [graphify](https://pypi.org/project/graphifyy/)) para un negocio o cliente: desde cero fuentes de datos hasta un grafo sincronizado y conectado a Claude Code / Claude Desktop.

Este repo es también el código fuente del Skill `cerebro-empresarial` (ver `SKILL.md`) — está enlazado como symlink en `~/.claude/skills/cerebro-empresarial`.

## Instalación

Requiere Python 3.9+. En esta máquina, usar explícitamente el intérprete donde vive `graphify` (hay varios Python 3 instalados):

```bash
/usr/local/opt/python@3.11/bin/python3.11 -m pip install --user -e ~/.claude/skills/cerebro-empresarial
```

Instalación editable: los cambios en `src/cerebro_cli/*.py` se aplican de inmediato, sin reinstalar. El binario `cerebro` queda junto a `graphify` (mismo bin dir de `--user`). Si no aparece en el PATH:

```bash
python3.11 -c "import site,pathlib; print(pathlib.Path(site.getuserbase())/'bin')"
```

y agregar esa ruta al `PATH`, o invocar `cerebro` por ruta completa.

Verificar que `cerebro` y `graphify` resuelven al mismo intérprete:

```bash
head -1 "$(which cerebro)"
head -1 "$(which graphify)"
```

## Comandos

### `cerebro init [project]`

Prepara un proyecto para recibir conectores:

- Crea `connectors/registry.yaml` (vacío) y `connectors/state/`.
- Agrega entradas a `.gitignore` (`connectors/state/`, `connectors/logs/`, `mirrors/`, `raw/`, `graphify-out/`).
- Agrega `connectors/` a `.graphifyignore` — necesario para que graphify no indexe los scripts de los conectores como código fuente (esto **no** lo cubre `.gitignore`, son mecanismos independientes).
- Avisa si `graphify` no está instalado.

`project` es opcional, por defecto `.`. Es seguro correrlo sobre un proyecto que ya tiene `registry.yaml` a mano — no lo sobreescribe.

```bash
cerebro init ~/clientes/acme
```

### `cerebro new-connector <project> <name> [--interval-minutes N]`

Crea un conector nuevo copiando la plantilla (`connector_template.py`) a `connectors/<name>/sync.py` y lo registra en `registry.yaml` con el intervalo dado (default 60 min).

```bash
cerebro new-connector ~/clientes/acme hubspot --interval-minutes 30
```

Después hay que:
1. Editar `connectors/hubspot/sync.py` e implementar `fetch_records()`.
2. Si el conector necesita credenciales:
   ```bash
   cerebro secret set graphify-acme-hubspot
   ```

No sobreescribe un `sync.py` existente (falla en su lugar).

### `cerebro sync [project] [--dry-run]`

Corre los conectores que estén "due" según su `interval_minutes` (comparado contra `connectors/state/<name>.json`), y si al menos uno corrió, reconstruye el grafo con `graphify update <project>`.

- `--dry-run`: solo muestra qué conectores están due/al día y si su script existe, sin ejecutar nada ni tocar el grafo.
- Seguro de correr con un registro vacío (no-op).
- Salida: `ran=[...] skipped=[...] errors=[...] graph_rebuilt=<bool>`. Código de salida 1 si hubo errores.

```bash
cerebro sync ~/clientes/acme
cerebro sync ~/clientes/acme --dry-run
```

Este es el comando que también dispara el LaunchAgent programado (ver `cerebro schedule`), y el que se puede pedir correr manualmente ("sincronizá el CRM ahora").

### `cerebro connect-claude [project] [--desktop] [--trust-desktop]`

Conecta el proyecto a Claude:

- Siempre corre `graphify claude install` (conecta Claude Code vía `CLAUDE.md` + hooks). Sin flags, esto es todo lo que hace — es el default de menor riesgo.
- `--desktop`: además registra un servidor MCP (`graphify-mcp`) en `claude_desktop_config.json`, apuntando a `<project>/graphify-out/graph.json`.
- `--trust-desktop`: agrega el proyecto a `localAgentModeTrustedFolders` en la config de Desktop. **Aditivo** — nunca reemplaza las entradas existentes.
- Antes de tocar `claude_desktop_config.json` siempre hace un backup (`.bak-<timestamp>`).

```bash
cerebro connect-claude ~/clientes/acme --desktop --trust-desktop
```

### `cerebro schedule [project] --interval-minutes N [--load]`

Genera un LaunchAgent (`~/Library/LaunchAgents/com.graphify.sync.<slug>.plist`) que corre `cerebro sync <project>` cada N minutos.

- Se niega a generarlo si el proyecto no tiene ningún conector registrado.
- Sin `--load`, solo escribe el `.plist` y muestra el comando `launchctl bootstrap` para cargarlo a mano.
- Con `--load`, lo carga inmediatamente.
- `--slug` opcional para el label del LaunchAgent (por defecto, se deriva del nombre del proyecto).

```bash
cerebro schedule ~/clientes/acme --interval-minutes 15 --load
```

### `cerebro secret set <item>` / `cerebro secret get <item>`

Credenciales de conectores, guardadas en el Keychain de macOS (nunca en `registry.yaml`, nunca por chat/argumentos de CLI).

- `secret set`: pide el valor con input oculto (`getpass`) y lo guarda/actualiza en el Keychain.
- `secret get`: lee un secreto guardado — solo para debugging manual, los conectores deben llamar `keychain.get_secret()` directamente, no este subcomando.

```bash
cerebro secret set graphify-acme-hubspot
cerebro secret get graphify-acme-hubspot   # debugging
```

### `cerebro status [project]`

Muestra:
- Conectores registrados: nombre, intervalo, si están due o al día, si el script existe.
- Tamaño del grafo actual (`graphify-out/graph.json`): nodos y aristas, o si todavía no se construyó.

```bash
cerebro status ~/clientes/acme
```

## Flujo típico (bootstrap de un cerebro nuevo)

```bash
cerebro init ~/clientes/acme
cerebro new-connector ~/clientes/acme hubspot --interval-minutes 30
cerebro secret set graphify-acme-hubspot
# ... editar connectors/hubspot/sync.py: fetch_records() ...
cerebro sync ~/clientes/acme
cerebro connect-claude ~/clientes/acme --desktop --trust-desktop
cerebro schedule ~/clientes/acme --interval-minutes 15 --load
```

Ver `SKILL.md` para el playbook completo (incluye cómo decidir la estrategia de conexión según el tipo de fuente: carpeta local, URL pública, conector MCP existente, o API/CRM a medida) y las notas de seguridad.
