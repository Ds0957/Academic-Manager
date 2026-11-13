# 43 — Conector de Claude Desktop

**Estado:** primera fase (solo lectura) implementada y probada localmente (WSL, con un cliente MCP de prueba), con el botón para conectarlo a Claude Desktop y las instrucciones para Claude. Rama `feat/conector-claude`.

## Para qué sirve

Permite que **Claude Desktop**, con la suscripción de Claude (sin clave de API ni pagos aparte), consulte los datos de Academic Manager durante una conversación: qué hay que hacer hoy, cómo va cada materia, notas guardadas, tareas, material de estudio y lo nuevo de Canvas.

Es un servidor **MCP** (Model Context Protocol) local: Claude Desktop lo abre en el mismo PC y le habla por la entrada y salida estándar (stdio). No abre puertos de red. Convive con otros conectores, como Desktop Commander; no los reemplaza.

## Herramientas

Todas son **de solo lectura** salvo `procesar_clase`, `importar_transcripciones`, `completar_clase`, `guardar_mapa` y `guardar_indice_clase`. Estas dos últimas guardan el mapa de una materia y el índice de una clase con vista previa y aprobación (ver más abajo). Las de lectura no escriben en la base, en Drive ni en archivos. Usan los mismos lectores que el chat y el panel ([27](27-contrato-consulta-academica.md), [34](34-today.md), [35](35-summary.md), [36](36-anomalias.md), [41](41-panel.md)).

| Herramienta | Qué devuelve |
|---|---|
| `ponerme_al_dia` | Contexto para **empezar cada conversación**. Sin materia: lo de hoy, avisos, materias, notas y tareas recientes y notas nuevas de Canvas. Con `materia`: pendientes, entregas, tareas, decisiones, resúmenes de clase y notas recientes de esa materia, más su nota, mensajes sin leer y comentarios del profesor en Canvas. Listas recortadas a 20 elementos. |
| `hoy` | Qué hacer primero (`dias`: 1 a 60, por defecto 14). |
| `resumen` | Resumen del semestre y avisos del sistema. |
| `materias` | Materias del semestre activo con su identificador `MAT-…`. |
| `materia` | Detalle y cobertura del material de una materia. |
| `actividades`, `clases` | Actividades o clases de una materia, por páginas (`limite` 1 a 100, `offset`). |
| `notas` | Notas guardadas (opcional `materia` y `tipo`), por páginas. |
| `tareas` | Tareas personales (opcional `estado` y `materia`), por páginas. |
| `buscar` | Busca palabras en el material de una materia (`consulta`, `materia`, `limite` 1 a 30). |
| `leer_fuente` | Lee un fragmento de una fuente (`fuente`, opcional `materia`, `offset`, `length` hasta 6000) para citarla. |
| `anomalias` | Problemas detectados en los datos (opcional `materia`). |
| `mapa` | Mapa de estudio guardado de una materia (`materia`): temas con explicación y citas, relaciones y diagramas Mermaid. Sin mapa, lo dice y sugiere crearlo. |
| `guardar_mapa` | **Escribe.** Guarda el mapa de una materia (`materia`, `mapa`; para guardar, además `plan_id` y `aprobar: true`). |
| `indice_clase` | Índice guardado de una clase (`clase`): un resumen muy corto y los minutos de cada tema. Sin índice, lo dice y sugiere crearlo. |
| `guardar_indice_clase` | **Escribe.** Guarda el índice de una clase (`clase`, `indice`; para guardar, además `plan_id` y `aprobar: true`). |
| `capturas` | Proceso en curso y últimos trabajos de Capture: fase, avance, publicación y registro (`limite`). |
| `leer_transcripcion` | Transcripción de un trabajo, por partes, aunque la clase aún no esté registrada (`trabajo`, `offset`, `length`). |
| `procesar_clase` | **Escribe.** Lanza Capture sobre una grabación de Drive (`enlace`, `materia`, `fecha`; para empezar, además `plan_id` y `aprobar: true`). |
| `transcripciones_descargadas` | Transcripciones `.vtt` en Descargas, bajadas con el botón «Transcripción de Drive»: archivo, grabación, fecha, enlace y si ya está procesada. |
| `importar_transcripciones` | **Escribe.** Crea clases desde esas transcripciones, sin descargar el video (`archivos`, `materia`; para empezar, además `plan_id` y `aprobar: true`). |
| `completar_clase` | **Escribe.** Guarda el índice de la clase, la publica y la registra (`trabajo`, `indice`; para empezar, además `plan_id` y `aprobar: true`). |

### `procesar_clase` y `completar_clase`: una clase de principio a fin

Claude procesa una clase sin que la persona use comandos:

1. `procesar_clase` sin `aprobar` consulta Drive y la base, muestra la grabación, la materia y la fecha, y devuelve un `plan_id`. No empieza nada.
2. Con la aprobación, llama otra vez con ese `plan_id` y `aprobar: true`. Capture corre en un proceso aparte, sin ventana, y sigue aunque se cierre la conversación.
3. `capturas` muestra el avance. Se procesa una clase a la vez; un trabajo sin terminar de la misma grabación se reanuda.
4. Con `leer_transcripcion`, Claude lee toda la transcripción y arma el índice por minutos. Es lo único que escribe: no hay un análisis aparte.
5. `completar_clase` sin `aprobar` comprueba los minutos y las citas contra la transcripción del trabajo, y comprueba la clase. Con la aprobación, corre en segundo plano el `complete` de `academic_operations` ([31](31-operaciones-coordinador.md)) y guarda el índice.

Capture exige un `analysis.md` para publicar, así que ese archivo se genera del índice (`class_index.render`). Las clases registradas antes conservan su análisis; a ellas se les agrega el índice con `guardar_indice_clase`.

### Grabaciones que no se pueden descargar: `importar_transcripciones`

Algunos profesores desactivan la descarga del video en Drive (por ejemplo, en inglés), pero el reproductor sigue mostrando su transcripción automática. El botón «Transcripción de Drive» (en Academic Manager → «Configuración» → «Claude») la guarda en Descargas como `<grabación> [<código de Drive>].vtt`. Después:

1. `transcripciones_descargadas` lista esos archivos con la fecha que dice su nombre y si la grabación ya está procesada.
2. `importar_transcripciones` sin `aprobar` muestra cada clase (fecha, fragmentos, minutos y enlace) y devuelve un `plan_id`. Acepta de 1 a 15 archivos de la misma materia.
3. Con la aprobación, guarda una copia de cada archivo en `procesamiento/transcripciones-importadas/` y Capture la importa con `--transcript`, `--recording-name` y `--recording-url` en un proceso aparte. Tarda segundos por clase.
4. Cada clase queda transcrita y sin índice: se termina igual que las demás, con `leer_transcripcion` y `completar_clase`.

Capture limpia los subtítulos de Drive: cada frase se toma una sola vez, sin las marcas de tiempo por palabra, y se unen en fragmentos de unos 30 segundos con sus tiempos originales (`capture/transcription/formatter.import_vtt`). La clase queda registrada con el enlace de su grabación en Drive, así que la misma grabación no se procesa dos veces, ni por video ni por transcripción. Solo entran nombres de archivos `.vtt` que estén directamente en Descargas, nunca rutas; un archivo sin el código de la grabación o sin fecha en el nombre se rechaza.

Controles (`academic_operations/class_capture.py`):

- Solo entra un enlace HTTPS de Google Drive, nunca una ruta ni un ID suelto. El proceso recibe únicamente la carpeta de datos y lee su pedido de `procesamiento/claude-capture/estado.json`, así que ningún texto de la conversación llega a una línea de comandos.
- Si el nombre de la grabación trae una fecha distinta de la indicada, o la grabación ya está transcrita, no empieza.
- Si el archivo de Drive cambia entre la vista previa y la aprobación, el `plan_id` deja de valer.
- Si el proceso muere, su bloqueo del sistema se libera y `capturas` lo muestra como interrumpido.
- Capture usa su propio inicio de sesión de Drive, no el conector de Google Drive de Claude.

### `guardar_mapa`: el mapa de la materia

Claude arma el mapa leyendo el material real (`buscar`, `leer_fuente`, `clases`) y lo guarda con el mismo contrato de dos pasos que las escrituras del chat ([33](33-escritura-desde-chat.md), acción `map`):

1. **Vista previa.** Sin `aprobar`: valida el mapa y **comprueba que cada cita aparezca en su fuente** de la materia; devuelve `plan_id` y `detalle` (temas, relaciones, diagramas, si reemplaza uno guardado). No guarda nada.
2. **Aprobación.** Claude muestra el resumen y pregunta. Solo si la persona aprueba, llama otra vez con el mismo `mapa`, ese `plan_id` y `aprobar: true`, y el mapa se publica en la base de Drive.

`aprobar` sin `plan_id`, o `plan_id` sin `aprobar: true`, es un error y no escribe nada. Guardar **reemplaza** el mapa anterior de la materia. Si la publicación se corta a mitad, el mensaje pide consultar `mapa` antes de reintentar. En Claude Desktop es la única herramienta marcada como de escritura (`readOnlyHint: false`, `destructiveHint: true`), así que Claude Desktop también puede pedir permiso antes de usarla.

### `guardar_indice_clase`: el índice de la clase

Claude lee toda la transcripción con `leer_fuente` (`clase:<clase_id>:transcripcion`), escribe un resumen de 3 a 5 líneas y agrupa por tema los minutos en que se habló de cada cosa. Usa el mismo contrato de dos pasos ([33](33-escritura-desde-chat.md), acción `class-index`): sin `aprobar` valida y **comprueba que cada minuto abra un fragmento de la transcripción y que su cita esté en él**; solo con el `plan_id` y `aprobar: true` publica. Guardar **reemplaza** el índice anterior de la clase y no toca su análisis, así que también sirve para clases ya publicadas. La clase debe estar registrada y su transcripción, indexada.

Los parámetros se revisan antes de leer nada (identificadores `MAT-…`, límites como en la línea de comandos). Si algo falla, Claude recibe un mensaje sencillo en español (por ejemplo, «La base de datos se está actualizando en este momento…»), nunca el error técnico ni rutas del PC.

## Cómo lo abre Claude Desktop

Claude Desktop ejecuta, con el Python que instala Academic Manager:

```
python -m academic_mcp
```

La carpeta de datos se busca como en el resto de Academic Manager: la opción `--runtime`, si no `ACADEMIC_MANAGER_DATA`, si no el archivo de configuración del usuario, si no `C:\AcademicManagerData`.

Al conectarlo desde Configuración (abajo), la carpeta de datos elegida en el asistente se le pasa a Claude Desktop en `ACADEMIC_MANAGER_DATA`, así el conector lee siempre la misma carpeta que el panel.

## Conectar Claude Desktop

En **Configuración**, debajo de «Acceso desde el celular», la fila **Claude Desktop** abre la página `/claude` (solo en el PC; no está disponible desde el celular). Necesita la carpeta de datos elegida (paso 1).

1. Instale Claude Desktop, ábralo una vez con su cuenta y ciérrelo.
2. En la página, pulse **Conectar con Claude Desktop**.
3. Cierre Claude Desktop **por completo** (también desde la bandeja del sistema, junto al reloj) y vuelva a abrirlo. El conector `academic-manager` aparece entre sus conectores.

Qué hace el botón (`academic_app/claude_desktop.py`):

- Busca el archivo `claude_desktop_config.json` de Claude Desktop. La instalación normal lo guarda en `%APPDATA%\Claude\`; la de **Microsoft Store**, en `%LOCALAPPDATA%\Claude-Data\claude_…\` (las versiones anteriores usaban `%LOCALAPPDATA%\Packages\Claude_…\LocalCache\Roaming\Claude\`). La carpeta `Claude-Data` siempre tiene prioridad. Si existen las dos carpetas, usa la que tiene otros archivos de Claude (en empate, la de Microsoft Store, porque esa carpeta desaparece al desinstalarla). Si no existe ninguna carpeta de Claude, dice «Claude Desktop no está instalado en este PC» y no crea nada.
- Agrega o reemplaza **solo** la entrada `mcpServers["academic-manager"]`:

  ```json
  {
    "mcpServers": {
      "academic-manager": {
        "command": "C:\\…\\Academic Manager\\python\\python.exe",
        "args": ["-m", "academic_mcp"],
        "env": {"ACADEMIC_MANAGER_DATA": "C:\\AcademicManagerData"}
      }
    }
  }
  ```

  `command` es el mismo Python con el que corre Academic Manager. Las demás opciones y los demás conectores (por ejemplo Desktop Commander) quedan igual.
- Antes de cambiar el archivo guarda una **copia de seguridad** a su lado: `claude_desktop_config.json.academic-manager-<fecha y hora UTC>.bak`. Si el archivo no existía, lo crea (sin copia, porque no había nada que guardar).
- Escribe de forma atómica (archivo temporal en la misma carpeta y reemplazo), en UTF-8.
- Si ya estaba conectado igual, no cambia nada ni hace otra copia.
- No toca el archivo si no es JSON válido, si no tiene la forma esperada (`mcpServers` debe ser un objeto) o si es un enlace a otro archivo: en ese caso explica el problema en palabras sencillas y muestra cómo hacerlo a mano.
- Nunca muestra ni registra el contenido del archivo: otros conectores pueden guardar claves en él.

La página muestra el estado: **No instalado**, **Sin conectar**, **Conectado**, **Hay que actualizar** (existe la entrada pero apunta a otro Python o a otra carpeta de datos, por ejemplo tras mover la carpeta: pulse **Actualizar conexión**) o **Hay que revisar** (el archivo no se puede usar). **Quitar conexión** quita solo la entrada `academic-manager`, también con copia de seguridad antes.

### Hacerlo a mano

Si el botón no puede, la página muestra dónde está el archivo y el texto exacto para pegar, con su botón **Copiar**. En Claude Desktop también se abre desde Configuración → Desarrollador → «Editar configuración». Si el archivo está vacío o no existe, se pega todo el texto; si ya tiene `mcpServers`, se agrega solo la parte de `academic-manager` dentro, sin borrar los demás conectores. Después se cierra Claude Desktop por completo y se vuelve a abrir.

## Instrucciones para Claude

La misma página trae la tarjeta **Proyectos de Claude**: un cuadro para el Coordinador y uno por cada materia del semestre activo, para pegar en las instrucciones de su proyecto (engranaje → Memory → Project instructions). Cada proyecto tiene su propia memoria, así que las materias no se mezclan. Son independientes de los Projects de ChatGPT: los dos consultan los mismos datos, pero Claude usa el conector y ChatGPT la línea de comandos. Todos los cuadros comparten estas reglas y terminan con una línea que fija su alcance:

1. Al empezar cada conversación, llamar primero a `ponerme_al_dia` (con la materia, si se va a hablar de una).
2. Para el material de una materia, usar `buscar` y después `leer_fuente`, y citar la fuente de cada dato; si no se encuentra, decirlo.
3. Para registrar entregas, notas, tareas o decisiones, usar Academic Manager: el panel o, cuando existan, las herramientas de escritura del conector, con vista previa y aprobación.
4. Para estudiar una materia, consultar su `mapa`; si no tiene, ofrecer crearlo con `guardar_mapa` desde el material real, mostrar el resumen y los diagramas y guardarlo solo con aprobación.
5. Para saber en qué minuto de una clase se habló de algo, consultar `indice_clase`; si no tiene, ofrecer crearlo con `guardar_indice_clase` desde su transcripción y guardarlo solo con aprobación.
6. Para procesar una clase grabada, usar `procesar_clase`, seguirla con `capturas`, leerla con `leer_transcripcion` y terminar con `completar_clase`, que guarda su índice, sin escribir un análisis aparte, con vista previa y aprobación en cada paso.
7. Aunque Claude tenga Desktop Commander u otro acceso a los archivos del PC, **nunca** editar directamente los datos, la base de datos ni los archivos de Academic Manager.

Desktop Commander sigue conectado y funcionando igual; estas instrucciones solo evitan que se use para cambiar los datos de Academic Manager por fuera de sus controles.

## Protocolo

Usa el SDK oficial de MCP para Python (`mcp`, versión fija en `requirements-app.txt`). El SDK atiende tanto a los clientes que empiezan con el saludo `initialize` (revisiones hasta 2025-11-25) como a los de la revisión sin estado 2026-07-28. Por la salida estándar solo viajan mensajes del protocolo; los avisos internos van a la salida de errores y solo dicen el tipo de fallo, sin datos.

## Privacidad

- Corre **solo en este PC**; no envía nada a internet por su cuenta.
- Lee solo los datos de Academic Manager (la base local, el índice de material y las copias de Canvas de la sincronización).
- Lo que Claude lee con estas herramientas sí llega a la conversación con Claude, como cuando usted le pega un texto.
- Escribir (registrar entregas, notas, tareas) llegará en una fase posterior, siempre con vista previa y aprobación.
