# Red Sonantia Network · Protocolo público 1.0

## 1. Alcance

El protocolo define documentos JSON públicos para identidad de red, mensajes,
feeds, relays, inventario, archivo, interacciones y estado. El transporte es
pull sobre HTTPS.

No define consenso, ejecución remota, mensajería privada, autenticación mutua ni
confianza entre operadores.

La versión 0.1 no forma parte de la operación vigente ni de la conformidad entre
nodos nuevos.

## 2. Convenciones

- Codificación: UTF-8.
- Fechas canónicas: UTC, `YYYY-MM-DDTHH:MM:SSZ`.
- Identificadores de nodo: `N##`.
- Época: `SN1-YYYY-MM-DD`.
- Visibilidad: `public`.
- Integridad: SHA-256 de JSON canónico.
- Esquemas: JSON Schema 2020-12.
- Transporte público: HTTPS.

## 3. Identidad de nodo

Cada nodo posee un `node_id` estable dentro de una época:

```text
N02
N02
N03
N04
```

La plataforma, repositorio o proveedor de hosting pueden cambiar sin cambiar el
`node_id`, siempre que el nodo mantenga continuidad de secuencias y estado.

## 4. Identidad de mensaje

```text
SN1-N##-YYYY-MM-DDTHH-MM-SSZ-######
```

Ejemplo:

```text
SN1-N02-2026-08-03T21-36-17Z-000020
```

La secuencia es local al origen y debe ser estrictamente creciente dentro de la
época de red.

## 5. Mensaje canónico

Campos incluidos en el hash:

```json
{
  "network_id": "sonantia-network",
  "protocol_version": "1.0",
  "network_epoch": "SN1-2026-08-02",
  "message_id": "SN1-N02-2026-08-03T21-36-17Z-000020",
  "origin_node_id": "N02",
  "sequence": 20,
  "created_at": "2026-08-03T21:36:17Z",
  "visibility": "public",
  "language": "es",
  "text": "...",
  "context": {},
  "generator": {},
  "content_hash": "sha256:..."
}
```

`message_id`, `origin_node_id`, `sequence` y `created_at` deben describir la
misma identidad.

## 6. JSON canónico y hash

Para calcular el hash:

1. seleccionar los campos canónicos del contrato;
2. excluir `content_hash` del material de entrada;
3. serializar objetos con claves ordenadas;
4. usar UTF-8;
5. no escapar caracteres Unicode salvo lo requerido por JSON;
6. usar separadores compactos;
7. rechazar NaN e infinitos;
8. calcular SHA-256;
9. anteponer `sha256:`.

Metadatos locales de recepción no forman parte del hash.

El hash prueba integridad del contenido, no identidad criptográfica del
operador.

## 7. Contexto

`context` puede incluir:

```text
location
weather
astronomy
geology
economy
```

Un dominio ausente o con `status = unavailable` no invalida el mensaje. Los
valores no deben inventarse.

Los proveedores se identifican mediante cadenas estables. Los datos nacionales
se normalizan antes de entrar al mensaje.

### 7.1 Ubicación

La ubicación representa el perfil lógico del nodo originador en el momento de
creación. Debe derivarse de su configuración local y ser coherente con las
consultas astronómicas y meteorológicas.

### 7.2 Meteorología

El contrato admite:

- una fuente completa;
- una composición de varias fuentes;
- una fuente separada para la condición meteorológica.

`measurement_source_count` y `measurement_source_codes` describen las fuentes
reales utilizadas.

## 8. Feed propio

Recurso:

```text
/feed.json
```

Contiene exclusivamente mensajes originados por `node_id`, ordenados del más
reciente al más antiguo y limitados por `feed_limit`.

Un receptor debe validar el documento y luego cada mensaje.

## 9. Relay

Recurso:

```text
/relay/N##.json
```

Un relay contiene mensajes de un único origen remoto. No demuestra custodia
histórica completa. Su retención puede estar limitada por cantidad y tiempo.

Un nodo nunca debe cambiar los campos canónicos de un mensaje recibido.

## 10. Inventario

Recurso:

```text
/inventory.json
```

Por origen declara:

```text
role
available_from_sequence
available_through_sequence
gaps
archive_through
```

Los rangos describen solo aquello que el nodo puede servir actualmente.

## 11. Archivo propio

```text
/archive/N##/YYYY/MM/DD.json
/archive/N##/YYYY/MM/index.json
/archive/index.json
```

Los archivos diarios son la fuente persistente de mensajes propios. Los índices
son vistas derivadas y reconstruibles.

Un mensaje remoto no puede archivarse como propio.

## 12. Interacciones

```text
/interactions/current.json
```

Los eventos son compactos y pueden referenciar `message_id` o `peer_node_id`.
No deben duplicar texto, contexto, feed ni payloads externos completos.

## 13. Topología

```text
/network.json
```

Declara:

```text
network_id
protocol_version
network_epoch
reference_node_id
nodes[]
```

Cada entrada puede incluir nombre, plataforma y URL de:

```text
public_url
feed_url
status_url
```

La presencia en la topología no garantiza disponibilidad. Un nodo solo debe
consultarse cuando esté explícitamente habilitado.

## 14. Estado

```text
/sonantia-status.json
```

Incluye:

```text
node_id
result
feed_message_count
archive_message_count
publication_file_count
origins
components
```

`result` puede ser:

- `success`;
- `degraded`;
- `rendered`.

## 15. Replicación

Para cada par habilitado:

1. descargar `feed_url` con timeout y límite de tamaño;
2. comprobar red, versión, época, tipo e identidad;
3. validar identidad y hash de cada mensaje;
4. almacenar los nuevos en el relay del origen;
5. ignorar duplicados con mismo ID y hash;
6. rechazar mismo ID con hash diferente;
7. registrar el resultado como interacción.

## 16. Conflictos

```text
ID nuevo + hash válido       → aceptar
ID conocido + mismo hash     → duplicado
ID conocido + hash distinto  → rechazar
secuencia duplicada          → rechazar
origen incompatible          → rechazar
época incompatible           → rechazar
```

## 17. Persistencia y recuperación

Las escrituras deben ser atómicas por archivo. Si el estado compacto no coincide
con el último archivo diario, el nodo puede reconstruir contadores desde el
archivo propio.

La secuencia no se confirma hasta que el mensaje quede escrito en el archivo
diario.

## 18. Actualización de implementación

El protocolo no exige conservar código, plantillas ni recursos públicos de una
implementación anterior. Un operador puede sustituir directamente la
infraestructura publicada en la misma URL.

Después del corte, la URL debe exponer únicamente recursos Sonantia 1.0 válidos.
No es necesario mantener rutas, páginas o documentos de versiones antiguas.

### 18.1 Sustitución de un nodo anterior a Sonantia 1.0

Puede inicializarse un estado Sonantia nuevo bajo la época vigente, siempre que
el nodo no haya emitido previamente secuencias Sonantia dentro de esa época.

### 18.2 Sustitución de un nodo Sonantia 1.0 activo

Debe cumplirse una de estas condiciones:

- preservar `data/sonantia` y continuar la secuencia;
- comenzar una nueva época coordinada;
- publicar una transición explícita que impida interpretar mensajes nuevos como
  continuación de secuencias antiguas.

Reutilizar un `node_id` y una secuencia ya emitida con contenido diferente es un
conflicto.

## 19. Cambio de URL o plataforma

Un nodo puede cambiar de repositorio, plataforma o URL sin cambiar su identidad.
La topología debe actualizarse para que todos los nodos consulten los endpoints
vigentes.

Durante la migración no se requiere mantener dos implementaciones en paralelo.
Sin embargo, un nodo no debe habilitar el nuevo peer hasta que sus documentos
públicos y flujo de mensajes hayan sido validados.

## 20. Seguridad

- Solo HTTPS para pares públicos.
- No ejecutar contenido recibido.
- Tratar texto remoto como datos y escaparlo en HTML.
- Limitar tamaño, tiempo y número de documentos.
- No publicar secretos.
- Validar esquemas e identidad antes de persistir.
- Mantener permisos de CI mínimos para código, estado y Pages.

## 21. Conformidad mínima

Un nodo conforme debe:

- tener identidad `N##`;
- publicar `network.json`, `feed.json`, `inventory.json` y
  `sonantia-status.json` válidos;
- generar mensajes con hash verificable;
- conservar secuencia propia sin reutilización;
- separar archivo propio y relays;
- no modificar mensajes remotos;
- representar fallos de fuentes como no disponibles;
- permitir reconstruir sus índices desde el archivo propio;
- publicar únicamente la superficie Sonantia 1.0 vigente;
- validar su flujo antes de ser habilitado como peer.
