# Arquitectura de referencia · Sonantia Network 1.0

## 1. Propósito

El proyecto implementa un nodo autónomo que genera mensajes propios, consulta
contexto local, replica mensajes remotos mediante pull HTTPS, conserva estado en
JSON y publica una interfaz estática. La misma base debe poder desplegarse en
GitLab, GitHub, Framagit o Cloudflare sin bifurcar el núcleo del protocolo.

El repositorio actual representa a N02 — Nodo Nueva York y utiliza una semilla
de estado propia, sin mensajes ni secuencias heredadas.

## 2. Estado de las fases

| Fase | Resultado |
|---|---|
| 1 | Identidad y ubicación centralizadas en `config/node.json` |
| 2 | Registro configurable de proveedores y validación del flujo |
| 3 | Meteorología de fuente única o compuesta |
| 4 | Generación Sonantia nativa, retiro de 0.1 y adaptadores nacionales |
| 5 | Perfiles de prueba N01–N04 y pruebas parametrizadas |
| 6 | N02 implementado para GitHub Actions y GitHub Pages |

## 3. Modelo por capas

```text
Configuración del nodo y la red
              ↓
Registro de proveedores contextuales
              ↓
NodeContext normalizado
              ↓
Generador de texto determinista
              ↓
Mensaje canónico Sonantia + SHA-256
              ↓
Archivo diario propio / relays remotos
              ↓
Feed, inventario, estado e interacciones
              ↓
HTML estático y JSON público
              ↓
CI/CD y hosting de la plataforma
```

### 3.1 Configuración

- `config/node.json`: identidad, ubicación, infraestructura y proveedores del
  nodo local.
- `config/sonantia-network.json`: topología, endpoints de pares y políticas de
  almacenamiento.
- `config/message-catalog.json`: gramática y selección de frases.
- `config/operator-message.json`: aviso local no replicable.

La configuración se valida con Pydantic y JSON Schema. Los documentos
estructurales no admiten campos arbitrarios fuera del contrato.

### 3.2 Registro de proveedores

`ProviderRegistry` resuelve implementaciones por dominio e identificador:

```text
weather
weather-condition
astronomy
economy
geology
```

El ciclo no contiene condicionales por `node_id`. Cada adaptador recibe
`NodeConfig`, el instante de consulta y, cuando corresponde, un cliente HTTP
inyectable para pruebas.

Adaptadores activos de N02:

```text
weather.py                                      # Open-Meteo
astronomy.py                                    # NASA/JPL Horizons
providers/economy/us_economic_data.py          # FRED
providers/geology/usgs_earthquakes.py          # USGS
```

### 3.3 Contexto normalizado

`NodeContext` agrupa:

```text
weather
weather_sources
astronomy
economy
geology
```

Los fallos se representan como `status = unavailable`; nunca se inventan datos.
Una fuente opcional puede degradar el ciclo sin invalidar un mensaje base.

### 3.4 Generación de mensajes

El generador ya no está limitado a meteorología. `messages.py` construye un `MessageContext` con weather, astronomy, geology y economy; deriva características contextuales y crea `SourceContribution` por cada dominio disponible. `SourceSelector` puntúa las fuentes, selecciona una primaria y opcionalmente una secundaria, con weather como fallback factual.

El catálogo actual contiene 1.024 aperturas, 512 declaraciones, 128 perfiles meteorológicos, 256 plantillas por dominio y 8.192 afirmaciones. Las cantidades describen el estado editorial actual, no invariantes rígidos del software.

La selección permanece determinística: el catálogo compilado aporta `catalog_hash`; la secuencia y el instante alimentan el PRNG; el cursor de afirmaciones evita repetir una frase hasta completar la permutación de la colección activa.

### 3.5 Persistencia

```text
data/sonantia/
├── core.json
├── own/N##/YYYY/MM/DD.json
├── own/N##/YYYY/MM/index.json
├── relay/N##.json
└── interactions/current.json
```

El archivo diario propio es la evidencia principal. El feed, inventario, estado
e índices son vistas derivadas.

### 3.6 Publicación

La superficie pública se reconstruye en `public/`:

```text
index.html
sonantia.html
node.json
network.json
feed.json
inventory.json
sonantia-status.json
relay/
archive/
interactions/
operator-message.json
```

Los archivos generados no deben editarse manualmente ni conservarse como una
implementación histórica paralela.

## 4. Ciclo de ejecución

```text
run_cycle
  ├─ cargar configuración
  ├─ asegurar consistencia de core
  ├─ registrar cycle_started
  ├─ consultar pares habilitados
  ├─ recolectar contexto local
  ├─ generar texto determinista
  ├─ crear mensaje canónico y hash
  ├─ escribir archivo diario
  ├─ actualizar cursor y secuencia
  ├─ construir feed, inventario y estado
  ├─ renderizar HTML
  ├─ publicar JSON
  └─ validar que el mensaje nuevo sea servible
```

`run-cycle-if-due` consulta la antigüedad del último mensaje. Si supera el umbral
configurado, ejecuta un ciclo; de lo contrario solo renderiza.

## 5. Identidad y ubicación

Todo componente local deriva de `NodeConfig`:

```text
node_id
display_name
country_code
city
timezone
latitude
longitude
elevation_m
platform
public_url
```

NASA/JPL Horizons utiliza esas coordenadas para el observador. Las plantillas
usan la misma ciudad y zona horaria. El feed, el estado y las rutas propias usan
el mismo `node_id`.

## 6. Meteorología

### 6.1 Fuente única

N02 declara `open-meteo-current` en modo `single`. El mismo objeto aporta
mediciones y condición y publica `measurement_source_count = 1`.

La interfaz crea tantas tarjetas como fuentes reales existan y la gráfica acepta
ambos modos sin ramas específicas por ciudad.

## 7. Economía y geología

Los adaptadores nacionales están aislados del núcleo. La capa común conoce
campos normalizados.

### Economía

```text
provider / provider_label
region_label / country_code
observed_at
indicators
inflation
status / error
```

### Geología

```text
provider / provider_label
region_label / country_code
observed_at / window_hours / search_stage
events[]
status / error
```

Cada evento normaliza fecha, magnitud, profundidad, ubicación y coordenadas
cuando están disponibles.

`usgs_earthquakes.py` ejecuta una búsqueda escalonada para evitar que una ventana local
vacía se confunda con un proveedor inoperante:

```text
1. bounding box del estado de Nueva York · 168 h
        ↓ si no hay eventos
2. radio 550 km desde N02 · 168 h
        ↓ si no hay eventos
3. radio 550 km desde N02 · 720 h (30 días)
```

La primera búsqueda confía en los límites geográficos enviados al servicio FDSN de USGS;
no aplica un segundo filtro sobre el texto `place`. Esto evita descartar eventos válidos
por diferencias en la descripción administrativa de la ubicación. `search_stage` identifica
`state-7d`, `nearby-7d` o `nearby-30d`, y `window_hours` siempre refleja la ventana que
produjo el snapshot. Si USGS responde correctamente pero las tres búsquedas están vacías,
el dominio continúa `available` con `count = 0`; `unavailable` queda reservado para fallos
de transporte, HTTP o normalización.

## 8. Replicación

`sonantia_peers.py` consulta feeds Sonantia 1.0 mediante pull HTTPS.

Reglas principales:

- cada peer se habilita explícitamente;
- el feed debe coincidir con red, protocolo, época y origen;
- cada mensaje debe validar identidad, secuencia y hash;
- un duplicado con el mismo hash se ignora;
- un identificador conocido con hash diferente se rechaza;
- los mensajes remotos se escriben únicamente en el relay del origen;
- un mensaje remoto nunca se archiva como propio.

## 9. Validación y recuperación

### 9.1 Fuentes externas

Una fuente no disponible devuelve un snapshot explícito y puede dejar el ciclo
`degraded`.

### 9.2 Estado persistente

Si `core.json` queda atrasado respecto del archivo diario,
`ensure_core_consistency()` reconstruye sus contadores. La secuencia solo se
confirma después de escribir el mensaje propio.

### 9.3 Flujo publicado

`validate-message-flow` comprueba coherencia entre:

```text
core.json
archivo diario
feed.json
archive/index.json
sonantia-status.json
```

Puede además exigir una antigüedad máxima del último mensaje.

## 10. Renderizado

El layout utiliza el ancho disponible del viewport. La gráfica meteorológica:

- combina historial canónico y observación actual;
- coloca lo más reciente a la izquierda;
- adapta ejes y leyendas a la cantidad de puntos;
- utiliza tipografía compacta;
- conserva una tabla accesible;
- funciona con una o varias fuentes.

Las vistas deben obtener identidad y ubicación de los documentos locales, sin
respaldos rígidos a una identidad, ciudad o plataforma concreta.

## 11. Pruebas

La suite cubre:

- contratos de protocolo y hash;
- almacenamiento, índices y reconstrucción;
- generación nativa;
- registro y contratos de proveedores;
- replicación pull;
- ciclo completo con fuente compuesta y única;
- propagación de identidad y ubicación;
- renderizado y representación meteorológica.

Los perfiles en `tests/profiles/` materializan N01–N04. Las pruebas generales
usan nodos neutrales y evitan depender de textos visuales exactos.

## 12. Automatización y estado

Cada plataforma debe mantener dos responsabilidades separadas:

```text
ciclo programado
  → genera mensaje
  → valida flujo
  → persiste data/sonantia
  → despliega

render por cambio de código
  → restaura data/sonantia
  → renderiza o recupera si está atrasado
  → valida
  → despliega
```

La rama `node-state` se utiliza en GitHub para separar estado operativo
del código fuente. El repositorio principal no necesita versionar cada mensaje.

## 13. Política de reemplazo de infraestructura

La red no mantendrá implementaciones antiguas en paralelo.

### N01

Permanece en:

```text
https://dbijman.gitlab.io/nodo-santiago-n01/
```

### N02

Se construirá en:

```text
https://github.com/davidbijman/nodo-nueva-york-n02
```

con URL pública objetivo:

```text
https://davidbijman.github.io/nodo-nueva-york-n02/
```

### N03

La nueva implementación reemplazará directamente lo publicado en:

```text
https://nodo-falkenstein-n03-5222a9.frama.io/
```

### N04

La nueva implementación reemplazará directamente lo publicado en:

```text
https://nodo-tokio-n04.nodo-tokio-n04-dbijman.workers.dev/
```

No se requieren rutas de compatibilidad, páginas antiguas ni conservación de
artefactos públicos previos. Después del corte, cada URL debe exponer únicamente
la superficie Sonantia 1.0 vigente.

## 14. Regla de continuidad de estado

La sustitución de archivos de implementación y la continuidad del protocolo son
conceptos distintos:

- si el nodo reemplazado no operaba Sonantia 1.0, puede inicializarse desde cero;
- si ya emitía mensajes Sonantia dentro de la época vigente, debe preservarse su
  `data/sonantia` para no reutilizar secuencias;
- si no se conservará ese estado, debe comenzar una nueva época de red o una
  transición explícita acordada por los nodos.

## 15. Implementación de N02

N02 utiliza GitHub Actions, GitHub Pages y una rama `node-state` propia. El
estado inicial comienza con secuencia cero. El clima, el observador astronómico,
la geología y la identidad local reciben las coordenadas de `config/node.json`.
La economía publica únicamente los indicadores ya contemplados por la interfaz,
sin datos de empleo ni remuneración horaria.


## 16. Motor contextual multi-fuente de N02

### 16.1 Fuentes y adaptadores

| Dominio | Adaptador | Fuente externa |
|---|---|---|
| weather | `open-meteo-current` | Open-Meteo |
| astronomy | `nasa-jpl-horizons` | NASA/JPL Horizons |
| geology | `usgs-earthquakes` | USGS FDSN Event API |
| economy | `us-economic-data` | Federal Reserve Economic Data (FRED) |

`context_providers.py` resuelve estos adaptadores desde `config/node.json`; el compositor no contiene condicionales por `node_id`.

### 16.2 Geología regional

USGS se consulta alrededor de las coordenadas de Central Park con un radio de 550 km. La primera ventana cubre 168 horas (`regional-7d`); si no contiene eventos se amplía a 720 horas (`regional-30d`). El snapshot conserva la etapa y URL efectiva.

El motor vuelve a calcular distancia desde N02 y ordena los eventos con magnitud como factor dominante. Conceptualmente:

```text
priority = magnitude * 100 + recency_bonus + distance_bonus
```

M < 4 es opcional; M4.x incrementa relevancia de forma continua; M >= 5 activa `mandatory=true` y geology debe participar en el mensaje.

### 16.3 Construcción secuencial del mensaje

```text
run_cycle()
  -> collect_node_context()
  -> Open-Meteo / Horizons / USGS / FRED
  -> generate_and_store_sonantia_message()
  -> generate_sonantia_text()
  -> build_cycle_context()
  -> SourceContribution[]
  -> SourceSelector
  -> primary + secondary?
  -> fact selector / weather profile
  -> source template
  -> contextual opening
  -> semantic declaration
  -> phrase cursor
  -> canonical Sonantia message
  -> content_hash
  -> archive/feed/public
```

Weather utiliza los 128 perfiles para seleccionar dos hechos compatibles y su conector/orden. Astronomy puede elegir Sol/Luna visibles; geology conserva magnitud/localización y puede añadir antigüedad o distancia; economy selecciona entre indicadores FRED disponibles.

### 16.4 Aperturas y declaraciones

La familia `general` siempre es válida. Las aperturas pueden reforzarse con `morning`, `afternoon`, `evening`, `night`, `sunrise`, `sunset`, `rain`, `clear`, `cloudy`, bandas térmicas, humedad y radiación. Las declaraciones usan una taxonomía semántica multi-etiqueta: neutral, reflective, change, calm, beginning, data-driven, network, curiosity, resilience y otros matices.

### 16.5 `catalog_hash`, PRNG y afirmaciones

`message_catalog.py` compila la definición y las colecciones habilitadas y calcula `catalog_hash` mediante SHA-256 sobre una serialización JSON canónica. El generador deriva su semilla de catálogo, nodo, instante y secuencia.

Las 8.192 afirmaciones se recorren mediante una permutación modular. Para cada ronda se deriva offset y paso desde SHA-256 de `catalog_hash:round`; el paso se ajusta hasta ser coprimo con el tamaño de la colección, garantizando recorrer todos los índices antes de repetir. El cursor se persiste en `data/sonantia/core.json`.

### 16.6 Mensaje canónico y `content_hash`

`sonantia_protocol.py` construye el documento canónico. El hash de contenido usa SHA-256 sobre los campos canónicos serializados en UTF-8 con claves ordenadas y separadores compactos. El valor se publica como `sha256:<hex>`. Este hash prueba integridad/reproducibilidad del contenido, no identidad criptográfica del operador.

### 16.7 Automatización

`test.yml` ejecuta validación, pytest y Ruff en push/pull request. `node-cycle.yml` ejecuta sólo responsabilidades operativas del ciclo: restaurar `node-state`, instalar dependencias runtime, generar, validar, persistir y desplegar. `pages.yml` renderiza y publica sin repetir la suite de desarrollo.
