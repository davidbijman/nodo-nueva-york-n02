# N02 — Nodo Nueva York · Sonantia Network 1.0

Implementación limpia de **N02 — Nodo Nueva York** para Sonantia Network 1.0.
El repositorio utiliza el núcleo configurable en Python y se publica mediante
**GitHub Actions + GitHub Pages**.

## Identidad del nodo

- **ID:** `N02`
- **Nombre:** Nodo Nueva York
- **Ciudad:** Nueva York, Estados Unidos
- **Zona:** `north-america`
- **Zona horaria:** `America/New_York`
- **Coordenadas:** `40.7789, -73.9692`
- **Elevación:** `43 m`
- **Sitio público:** `https://davidbijman.github.io/nodo-nueva-york-n02/`
- **Protocolo:** Sonantia `1.0`

La identidad y la ubicación se declaran exclusivamente en `config/node.json`.
Esos datos se propagan al clima, al observador astronómico, a los mensajes, al
estado, al feed y a las vistas HTML.

## Proveedores activos

| Dominio | Proveedor | Alcance |
|---|---|---|
| Meteorología | `open-meteo-current` | Condiciones actuales en las coordenadas de N02 |
| Astronomía | `nasa-jpl-horizons` | Observador situado en `40.7789, -73.9692`, elevación `43 m` |
| Economía | `us-economic-data` | Indicadores oficiales de Estados Unidos mediante FRED |
| Geología | `usgs-earthquakes` | Sismos de los últimos 7 días para el estado de Nueva York; si no existen, se consulta la región cercana |

El bloque económico conserva la estructura ya implementada: cuatro indicadores
generales y tres indicadores de inflación/tasa. No publica datos de empleo ni de
remuneración horaria.

## Estado limpio

Este repositorio no reutiliza mensajes, secuencias, interacciones ni archivos de
estado de otro nodo. La semilla inicial se crea bajo:

```text
data/sonantia/own/N02/
public/archive/N02/
```

El primer mensaje propio deberá utilizar secuencia `1` y un identificador que
comience con `SN1-N02-`.

## Configuración

Los documentos configurables son:

```text
config/node.json
config/sonantia-network.json
config/message-catalog.json
config/operator-message.json
```

La topología conserva los demás nodos como integrantes conocidos de Sonantia.
N01 puede consultarse como peer activo; N03 y N04 permanecen deshabilitados
hasta que publiquen una superficie Sonantia 1.0 válida.

## Recursos públicos

### HTML

```text
/index.html
/sonantia.html
```

### JSON

```text
/feed.json
/inventory.json
/network.json
/sonantia-status.json
/node.json
/operator-message.json
/archive/index.json
/archive/N02/...
/interactions/current.json
```

## Operación local

```powershell
python -m pip install -e ".[dev]"
python -m distributed_node.cli validate-config
python -m pytest -q
python -m ruff check src tests
python -m distributed_node.cli initialize-sonantia --force
python -m distributed_node.cli run-cycle
python -m distributed_node.cli validate-message-flow
python -m distributed_node.cli validate-public
```

## GitHub Actions

- `.github/workflows/test.yml`: valida configuración, pruebas y estilo.
- `.github/workflows/node-cycle.yml`: ejecuta el ciclo horario, persiste
  `data/sonantia` en `node-state` y despliega GitHub Pages.
- `.github/workflows/pages.yml`: regenera y despliega la superficie pública al
  cambiar `main`, restaurando previamente el estado de `node-state`.

GitHub Pages debe configurarse con **GitHub Actions** como origen. Los workflows
de despliegue utilizan permisos de escritura para contenido, Pages e identidad
OIDC.
