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

El generador utiliza:

- secuencia Sonantia siguiente;
- cursor persistido en `core.json`;
- hora y ubicación local;
- hechos meteorológicos disponibles;
- catálogo y colecciones de frases.

No existe un mensaje intermedio 0.1. El resultado se entrega directamente a la
construcción canónica Sonantia.

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
observed_at / window_hours
events[]
status / error
```

Cada evento normaliza fecha, magnitud, profundidad, ubicación y coordenadas
cuando están disponibles.

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
