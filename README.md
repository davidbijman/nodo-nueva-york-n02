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
| Geología | `usgs-earthquakes` | Región de 550 km alrededor de N02: 7 días → 30 días si la ventana corta está vacía |

El bloque económico conserva la estructura ya implementada: cuatro indicadores
generales y tres indicadores de inflación/tasa. No publica datos de empleo ni de
remuneración horaria.

La consulta geológica se define como una **región física alrededor del nodo**, no como una frontera administrativa. N02 consulta primero un radio de 550 km durante 7 días (`regional-7d`) y, si no hay eventos, repite el mismo radio durante 30 días (`regional-30d`). El snapshot conserva `search_stage`, `window_hours`, `region_label` y `source_url` para trazabilidad. Un evento disponible no implica que deba aparecer en Sonantia: el motor calcula después su relevancia por magnitud, recencia y distancia.

Para comprobar el proveedor sin ejecutar un ciclo completo:

```powershell
$env:PYTHONPATH = "src"
python scripts/check_usgs_geology.py
```

La salida muestra la etapa de búsqueda utilizada, la ventana efectiva, la URL consultada y
hasta diez eventos normalizados.


## Motor de mensajes homologado

N02 utiliza el mismo núcleo de composición contextual de N01, conservando sus proveedores estadounidenses. El estado editorial actual es:

- **8.192 afirmaciones** en `config/phrases/frases_consolidadas.txt`;
- **1.024 aperturas** con familias temporales y meteorológicas;
- **512 declaraciones** con familias semánticas;
- **128 perfiles meteorológicos** para combinar dos hechos;
- **256 plantillas factuales por dominio**: weather, astronomy, geology y economy;
- máximo de **dos fuentes factuales** por mensaje.

El generador construye primero un `MessageContext` a partir de Open-Meteo, NASA/JPL Horizons, USGS y FRED. Después calcula relevancia por dominio, selecciona una fuente primaria y opcionalmente una secundaria, elige hechos compatibles, una apertura contextual, una declaración semántica y una afirmación del cursor persistente. Weather actúa como fallback factual.

La geología tiene una regla especial: magnitudes inferiores a 4 son opcionales; los eventos M4.x aumentan progresivamente su relevancia; **M ≥ 5 es obligatorio** y geology debe entrar al mensaje. Si hay varios sismos, magnitud domina el ranking y recencia/distancia actúan como moduladores.

La selección sigue siendo determinística y auditable mediante `catalog_hash`, secuencia, cursor de afirmaciones y `selected_values`.

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
- `.github/workflows/node-cycle.yml`: ejecuta el ciclo programado, persiste
  `data/sonantia` en `node-state` y despliega GitHub Pages. No repite la suite de tests en cada cron; esa responsabilidad pertenece a `test.yml`.
- `.github/workflows/pages.yml`: regenera y despliega la superficie pública al
  cambiar `main`, restaurando previamente el estado de `node-state`.

GitHub Pages debe configurarse con **GitHub Actions** como origen. Los workflows
de despliegue utilizan permisos de escritura para contenido, Pages e identidad
OIDC.
