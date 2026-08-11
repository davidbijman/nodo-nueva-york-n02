# N02 — Homologación del motor Sonantia

## Alcance

Esta versión porta a N02 el motor contextual multi-fuente consolidado en N01 sin sustituir los proveedores propios de Nueva York.

## Componentes homologados

- 8.192 afirmaciones auditadas en `config/phrases/frases_consolidadas.txt`.
- 1.024 aperturas contextuales.
- 512 declaraciones con familias semánticas.
- 128 perfiles meteorológicos.
- 256 plantillas por dominio: weather, astronomy, geology y economy.
- `SourceSelector` multi-fuente con máximo de dos fuentes factuales.
- weather como fallback factual.
- astronomy con Sol/Luna y fases sunrise/sunset/night.
- geology con prioridad sísmica y M >= 5 obligatorio.
- economy consumiendo FRED mediante `us-economic-data`.
- cursor persistente y permutación determinística de afirmaciones.
- trazabilidad mediante `selected_values` y `catalog_hash`.

## Geología N02

USGS se consulta como región física alrededor del nodo:

1. `regional-7d`: radio 550 km, 168 horas.
2. `regional-30d`: mismo radio, 720 horas cuando la primera ventana está vacía.

El motor vuelve a calcular distancia desde N02 y prioriza eventos con magnitud como factor dominante. Los eventos M4.x aumentan su relevancia progresivamente; M >= 5 activa una regla obligatoria.

## Automatización

- `test.yml`: validación, tests y Ruff en push/pull request.
- `node-cycle.yml`: ciclo operativo, sin repetir pytest en cada cron.
- `pages.yml`: render/deploy con dependencias runtime.

El cron de N02 se conserva sin cambios en `21 * * * *`.

## Validaciones ejecutadas

- `compileall`: OK.
- `validate-config`: OK.
- `validate-public`: OK.
- `validate-message-flow`: OK.
- motor/configuración/proveedores/USGS/weather/astronomy/perfiles: 37 tests OK.
- protocolo/storage/replicación/activación/renderizado: 8 tests OK.
- ciclo + ciclo Sonantia: 4 tests muestran 100 % de aserciones aprobadas; el proceso de prueba tarda en finalizar en el entorno de construcción.
- Python bajo `src/` y `tests/`: 0 líneas >100 caracteres.
- simulación M5.2: geology seleccionada como `geology-mandatory`, con astronomy secundaria.
- catálogo y compositor: sin referencias a Santiago, Banco Central de Chile, CSN ni UF.

## Consideraciones

Las cantidades editoriales describen esta versión y no se fijan como invariantes rígidos en los tests. La economía todavía no utiliza un delta histórico entre ciclos para determinar relevancia; puede incorporarse más adelante cuando exista una política de baseline definida.
