# 33 — Escritura desde el chat

**Estado:** desplegado en el PC el 2026-10-23, tras el corte a SQLite ([32-almacen-sqlite.md](32-almacen-sqlite.md)). Las instrucciones del Coordinador (`scripts\project-instructions.cmd coordinador`) indican cuándo y cómo usarlo; hay que pegarlas de nuevo en el Project de ChatGPT. Seguimiento en `odd/tasks/sqlite-core.md`.

## Para qué sirve

Antes, lo que se decidía en un chat de ChatGPT no se guardaba en ningún sitio. Ahora el chat normal puede guardar cuatro tipos de información con Remote Desktop Commander, usando `scripts\academic-act.cmd` (equivale a `python -m academic_operations`):

| Acción | Qué guarda | Tabla |
|---|---|---|
| `track` | Estado de una actividad: pendiente, en progreso, entregada o calificada, con nota y puntos opcionales | `seguimiento` |
| `note` | Nota, decisión o aclaración del profesor sobre una materia | `notas` |
| `task` | Tarea personal que no viene de Canvas, por ejemplo «estudiar tema X» | `tareas` |
| `task-status` | Cambio de estado de una tarea personal | `tareas` |
| `class` | Clase sin grabación ni Capture, con un resumen opcional | `clases` (V2) y `notas` |
| `batch` | Varias de `track`, `note`, `task` y `task-status` con una sola vista previa y una aprobación | las de cada elemento |
| `map` | Mapa de estudio de una materia: temas, relaciones, citas de las fuentes y diagramas | `mapas` |
| `class-index` | Índice de una clase: resumen muy corto y minutos de cada tema, comprobados con la transcripción | `indices_clase` |

## Contrato de dos pasos

Es el mismo contrato que `preview`/`complete` ([31-operaciones-coordinador.md](31-operaciones-coordinador.md)).

1. **Vista previa.** Sin `--plan-id` ni `--approve`, la acción valida la petición contra la base local y devuelve el plan. No escribe nada.
2. **Aprobación.** El chat muestra el plan al estudiante y le pide aprobación explícita.
3. **Publicación.** Solo con esa aprobación, repite la misma orden y añade `--plan-id <plan_id de la vista previa> --approve`.

Reglas:

- `plan_id` es el sha256 del JSON canónico de la operación planificada (claves ordenadas, sin espacios).
- Si la petición o la base cambiaron, el `plan_id` no coincide: error `PLAN_CHANGED` y no se escribe nada. Hay que pedir una vista previa nueva.
- `--plan-id` sin `--approve`: error `APPROVAL_REQUIRED`. `--approve` sin un `plan_id` de 64 caracteres hexadecimales: error `INVALID_ARGUMENT`.

## Entrada JSON (`--input`)

`cmd.exe` altera los caracteres `%`, `^`, `&` y `"` de los argumentos. Por eso **se recomienda `--input` para cualquier texto libre** (`--texto`, `--titulo`, `--detalle`, `--comentario`, `--resumen`). El chat escribe un archivo JSON y pasa su ruta:

```powershell
scripts\academic-act.cmd note --input C:\AcademicManagerData\temporales\peticion.json
scripts\academic-act.cmd note --input C:\AcademicManagerData\temporales\peticion.json --plan-id <PLAN_ID> --approve
```

Reglas:

- El archivo contiene los mismos campos que las opciones. Cada clave es el nombre de la opción sin `--` y con `-` cambiado por `_`: `--materia` → `materia`, `--fecha-limite` → `fecha_limite`.
- `--plan-id`, `--approve` y `--runtime` siguen siendo opciones; no van en el JSON.
- La vista previa y la aprobación con el mismo archivo dan el mismo `plan_id` que las opciones equivalentes.
- El archivo debe ser un objeto JSON en UTF-8, de 64 KB como máximo. Se admite la marca BOM inicial.
- `nota` y `puntos` son números JSON (`4.5`), no texto. `null` equivale a omitir el campo.
- Devuelven `INVALID_ARGUMENT`, sin escribir nada:
  - combinar `--input` con opciones de contenido (`--materia`, `--texto`, etc.);
  - una clave desconocida, o una que no pertenece a la acción;
  - un archivo inexistente, ilegible, mayor de 64 KB, que no es UTF-8, que no es JSON válido o que no es un objeto.
- Los mensajes de error no incluyen la ruta del archivo.

Un archivo por acción:

```json
{"actividad": "ACT-C9201-A1", "estado": "calificada", "nota": 4.5, "puntos": 90, "comentario": "Revisado en clase & aprobado"}
```

```json
{"materia": "MAT-SO2", "tipo": "decision", "texto": "El parcial pasa al jueves 5 y vale el 30 %."}
```

```json
{"titulo": "Preparar parcial", "materia": "MAT-SO2", "fecha_limite": "2026-11-05", "detalle": "Capítulos 1 a 3 (\"memoria virtual\")"}
```

```json
{"tarea": "TAR-0123456789abcdef", "estado": "hecha"}
```

```json
{"materia": "MAT-SO2", "fecha": "2026-10-20", "titulo": "Repaso de memoria virtual", "resumen": "Vimos paginación por demanda."}
```

## Órdenes

Todas aceptan `--runtime RUTA`. Por defecto se usa `ACADEMIC_MANAGER_DATA` o `C:\AcademicManagerData`.

### `track`: estado de una entrega

```powershell
scripts\academic-act.cmd track --actividad ACT-C9201-A1 --estado calificada --nota 4.5 --puntos 90 --comentario "Revisado en clase"
scripts\academic-act.cmd track --actividad ACT-C9201-A1 --estado calificada --nota 4.5 --puntos 90 --comentario "Revisado en clase" --plan-id <PLAN_ID> --approve
```

- `--actividad` (obligatorio) debe existir en `actividades`.
- `--estado` (obligatorio) es `pendiente`, `en_progreso`, `entregada` o `calificada`.
- `--nota`, `--puntos` y `--comentario` son opcionales. Las cifras deben ser números finitos y no negativos.
- Hay una fila por actividad, y cada escritura la actualiza.
- Si se omite un campo opcional, se conserva el valor guardado antes.
- **Las columnas que sincroniza Canvas en `actividades` no se tocan.**

### `note`: nota o decisión de una materia

```powershell
scripts\academic-act.cmd note --materia MAT-SO2 --tipo decision --texto "El parcial pasa al jueves 5."
scripts\academic-act.cmd note --materia MAT-SO2 --tipo decision --texto "El parcial pasa al jueves 5." --plan-id <PLAN_ID> --approve
```

- `--materia` (obligatorio) debe existir en `materias`.
- `--tipo` (obligatorio) es `nota`, `decision` o `aclaracion`.
- `--texto` es obligatorio.

### `task`: tarea personal

```powershell
scripts\academic-act.cmd task --titulo "Preparar parcial" --materia MAT-SO2 --fecha-limite 2026-11-05 --detalle "Capítulos 1 a 3"
scripts\academic-act.cmd task --titulo "Preparar parcial" --materia MAT-SO2 --fecha-limite 2026-11-05 --detalle "Capítulos 1 a 3" --plan-id <PLAN_ID> --approve
```

- `--titulo` es obligatorio (máximo 200 caracteres).
- `--materia`, `--fecha-limite` (YYYY-MM-DD) y `--detalle` son opcionales.
- La tarea se crea en estado `pendiente`.

### `task-status`: cambiar el estado de una tarea

```powershell
scripts\academic-act.cmd task-status --tarea TAR-0123456789abcdef --estado hecha
scripts\academic-act.cmd task-status --tarea TAR-0123456789abcdef --estado hecha --plan-id <PLAN_ID> --approve
```

- `--tarea` debe existir en `tareas`.
- `--estado` es `pendiente`, `en_progreso`, `hecha` o `descartada`.

### `class`: clase sin Capture

```powershell
scripts\academic-act.cmd class --materia MAT-SO2 --fecha 2026-10-20 --titulo "Repaso de memoria virtual" --resumen "Vimos paginación por demanda."
scripts\academic-act.cmd class --materia MAT-SO2 --fecha 2026-10-20 --titulo "Repaso de memoria virtual" --resumen "Vimos paginación por demanda." --plan-id <PLAN_ID> --approve
```

- `--materia`, `--fecha` (YYYY-MM-DD) y `--titulo` son obligatorios. `--resumen` es opcional.
- Se añade una fila V2 en `clases`:
  - id `CLS-…-NN`, calculado con `next_class_id`, igual que Capture;
  - `estado_sesion` = `realizada`, `fuente` = `chat`, `procesada` = falso;
  - se inserta en la primera fila libre entre la 2 y la 500.
- Con `--resumen`, se añade también una nota de tipo `resumen_clase` enlazada por `clase_id`.

### `batch`: varias escrituras con una sola aprobación

Solo con `--input`. El archivo contiene una lista `operaciones` de 1 a 20 elementos; cada uno lleva `accion` (`track`, `note`, `task` o `task-status`) y las mismas claves que esa acción por separado:

```json
{"operaciones": [
  {"accion": "note", "materia": "MAT-SO2", "tipo": "decision", "texto": "El parcial pasa al jueves 5."},
  {"accion": "task", "titulo": "Preparar parcial", "materia": "MAT-SO2", "fecha_limite": "2026-11-05"},
  {"accion": "track", "actividad": "ACT-C9201-A1", "estado": "entregada"}
]}
```

```powershell
scripts\academic-act.cmd batch --input C:\AcademicManagerData\temporales\lote.json
scripts\academic-act.cmd batch --input C:\AcademicManagerData\temporales\lote.json --plan-id <PLAN_ID> --approve
```

- La vista previa enumera cada elemento ya validado (`operation.operaciones`) con un único `plan_id`.
- Todo o nada: si un elemento no es válido, el lote entero se rechaza indicando «Operación N». Con la aprobación, todos se escriben en una sola transacción y una sola publicación.
- `class` no se admite en lotes: cada clase reserva su propia fila de `03 - Clases`.
- Dos elementos no pueden tocar el mismo registro (la misma nota, tarea o actividad). Un `task-status` solo puede cambiar tareas que ya existen en la base, no una creada en el mismo lote.

### `class-index`: índice de la clase

Solo con `--input` (hasta 256 KB). Guarda dónde se encuentra cada cosa que se habló en una clase registrada. Guardar de nuevo **reemplaza** el índice anterior de esa clase y nunca toca su análisis, así que también sirve para clases publicadas.

```json
{"clase": "CLS-SO2-20261020-01", "indice": {
  "resumen": "Se explicó la actividad del eje 3 y se repasó la planificación de procesos.",
  "temas": [
    {"titulo": "Instrucciones de la actividad", "categoria": "instrucciones", "momentos": [
      {"inicio": "00:04:12", "texto": "Cómo se entrega", "cita": "se sube a Canvas en parejas"},
      {"inicio": "01:20:18", "texto": "Repite la fecha", "cita": "la fecha de entrega del viernes"}]}
  ]
}}
```

Reglas (cualquier fallo es `INVALID_ARGUMENT` y nombra el tema y el tiempo):

- `resumen`: obligatorio, hasta 900 caracteres.
- `temas`: de 1 a 40; `titulo` hasta 120 caracteres; `categoria` opcional: `instrucciones`, `fechas`, `evaluacion`, `tareas` o `tema` (la predeterminada).
- `momentos`: de 1 a 30 por tema, sin repetir, y se guardan en orden de tiempo. `texto` hasta 200 caracteres.
- **Los minutos son reales:** `inicio` (`HH:MM:SS`) debe ser el tiempo inicial de un fragmento de la transcripción y `cita` (12 a 300 caracteres) debe estar en ese fragmento, sin importar espacios ni saltos de línea. Una transcripción sin tiempos no admite índice.

### `map`: mapa de la materia

Solo con `--input` (hasta 256 KB; las demás acciones siguen en 64 KB). Guarda el mapa de estudio de una materia: sus temas, cómo se relacionan, de qué fuente sale cada uno y diagramas en Mermaid. Guardar de nuevo **reemplaza** el mapa anterior de esa materia. Desde Claude Desktop se usa la herramienta `guardar_mapa` del conector ([43-conector-claude.md](43-conector-claude.md)), que llama a esta misma acción.

```json
{"materia": "MAT-SO2", "mapa": {
  "resumen": "Procesos, hilos y planificación.",
  "temas": [
    {"id": "SO2", "titulo": "Sistemas Operativos II"},
    {"id": "T1", "titulo": "Procesos", "padre": "SO2", "eje": 1,
     "explicacion": "Programa en ejecución con su propio espacio de direcciones.",
     "fuentes": [{"fuente_id": "lectura:91004:1:1", "cita": "Un proceso es un programa en ejecución."}]}
  ],
  "relaciones": [{"de": "T1", "a": "SO2", "texto": "es parte de"}],
  "diagramas": [{"titulo": "Mapa mental", "mermaid": "mindmap\n  root((SO II))\n    Procesos"}]
}}
```

Reglas (cualquier fallo es `INVALID_ARGUMENT` y nombra el tema, la relación o el diagrama):

- `temas`: de 1 a 80. `id` único (letras, números, `-`, `_`); `titulo` hasta 120 caracteres; `explicacion` opcional hasta 1500; `padre` opcional, debe ser otro tema y sin ciclos; `eje` opcional, entero de 1 a 12.
- `fuentes`: hasta 5 por tema, cada una con `fuente_id` (el que devuelven `search`/`source` o `buscar`/`leer_fuente`) y `cita` (12 a 400 caracteres). Todo tema con `padre` necesita al menos una. **La cita debe aparecer en el texto de esa fuente de la materia** (se ignoran espacios y saltos de línea); si no, no se guarda nada. Comprobarlo necesita el índice de búsqueda al día; si no lo está, el error es `SOURCE_UNAVAILABLE`.
- `relaciones`: hasta 150, entre dos temas distintos del mapa, con `texto` hasta 80 caracteres, sin repetir.
- `diagramas`: hasta 6, con `titulo` y `mermaid` (hasta 12 000 caracteres). La primera línea es el tipo: `mindmap`, `flowchart`, `graph`, `timeline`, `sequenceDiagram`, `stateDiagram`, `stateDiagram-v2`, `classDiagram`, `erDiagram` o `quadrantChart`. No se admiten directivas `%%{...}%%`.
- El mapa completo no puede pasar de 200 KB.

La vista previa añade `detalle`: `materia_id`, número de `temas`, `relaciones` y `diagramas`, si `reemplaza` un mapa guardado y cuántos `temas_anteriores` tenía.

## Una escritura a la vez

Las escrituras del chat de este equipo se turnan: si otra está publicando, la vista previa o la publicación **esperan hasta 2 minutos** y luego vuelven a validar el plan desde cero. Solo se espera ante ocupaciones detectadas antes de escribir nada: el turno local, el estado local en edición o el lock compartido de otro escritor (la tarea diaria de Canvas, Capture u otro equipo). Si la espera se agota, el error es `STORE_BUSY` y basta repetir la misma orden con el mismo `plan_id`. Un lock sin fecha o con más de 10 minutos no se espera: queda de un fallo anterior y la orden falla enseguida con `STORE_UNAVAILABLE`, porque requiere recuperación supervisada.

## Forma del JSON

Vista previa (`note`):

```json
{
  "ok": true, "schema_version": 1, "action": "note",
  "data": {
    "operation": {"accion": "note", "notas": [{"nota_id": "NOTA-1a2b3c4d5e6f7a8b", "materia_id": "MAT-SO2",
                  "tipo": "decision", "texto": "El parcial pasa al jueves 5.", "clase_id": null}]},
    "plan_id": "<64 hex>",
    "already_applied": false,
    "requires_explicit_approval": true
  }
}
```

Publicación:

```json
{
  "ok": true, "schema_version": 1, "action": "note",
  "data": {"plan_id": "<64 hex>", "operation": {"…": "…"}, "published": true, "already_applied": false,
           "sha256": "<hash de la base publicada>", "index_refreshed": true, "warning": null}
}
```

Error (código de salida `2`):

```json
{"ok": false, "schema_version": 1, "action": "note",
 "error": {"code": "PLAN_CHANGED", "message": "…", "stage": "validate_plan", "retryable": false,
           "publicado": "no", "suggested_action": "Generar una vista previa nueva y pedir aprobación otra vez."}}
```

`publicado` vale `no` cuando nada llegó a escribirse y `incierto` cuando el fallo ocurrió con el lock compartido ya tomado: el protocolo lo conserva para recuperación supervisada, así que hay que consultar `academic-read.cmd summary` antes de reintentar.

Códigos de error:

| Código | Significado |
|---|---|
| `INVALID_ARGUMENT` | Parámetro que falta o no es válido, opción que no pertenece a la acción, fecha que no es YYYY-MM-DD, texto que empieza por `=` o que supera el límite |
| `NOT_FOUND` | `materia_id`, `actividad_id` o `tarea_id` no existe en la base |
| `APPROVAL_REQUIRED` | Falta `--approve` |
| `PLAN_CHANGED` | El `plan_id` no coincide, o la base publicada en Drive cambió desde la vista previa. No se escribe nada |
| `CONFLICT` | El identificador planificado ya existe con otro contenido |
| `STORE_BUSY` | Otra escritura seguía publicando tras 2 minutos de espera. No se escribió nada; repetir con el mismo `plan_id` |
| `STORE_UNAVAILABLE` | La base local no coincide con el hash publicado o está ilegible |
| `PUBLISH_FAILED` | El protocolo de publicación se detuvo, por ejemplo con el libro bloqueado por otro equipo o un error de integridad tras subir |

## Tablas

Son tablas limpias: fechas `YYYY-MM-DD`, marcas de tiempo ISO-8601 UTC (`2026-10-22T15:04:05Z`) e ids de texto. **No heredan la forma de Excel** y no aparecen en `_columnas` ni en `sheetview`. Las crea `migrate`, y la escritura las crea si faltan en una base anterior.

| Tabla | Columnas |
|---|---|
| `seguimiento` | `actividad_id` (clave), `estado`, `nota`, `puntos`, `comentario`, `actualizado_utc` |
| `notas` | `nota_id` (clave, `NOTA-` + 16 hex), `materia_id`, `tipo` (`nota`, `decision`, `aclaracion`, `resumen_clase`), `texto`, `clase_id`, `creado_utc` |
| `tareas` | `tarea_id` (clave, `TAR-` + 16 hex), `titulo`, `materia_id`, `fecha_limite`, `detalle`, `estado`, `creado_utc`, `actualizado_utc` |
| `mapas` | `materia_id` (clave), `contenido` (JSON canónico del mapa, versión 1), `actualizado_utc` |
| `indices_clase` | `clase_id` (clave), `materia_id`, `contenido` (JSON canónico del índice, versión 1), `actualizado_utc` |

Los estados y tipos también se comprueban con `CHECK` en SQLite.

## Garantías

- **Publicación.** Toda escritura pasa por `canvas_sync.workbook_blocks.publish`, con el mismo protocolo de un solo escritor:
  1. lock y comprobación de hash;
  2. checkpoint en `temporales/`;
  3. edición de una copia de trabajo y nueva planificación, que debe devolver `[]`;
  4. subida y re-descarga;
  5. nueva planificación sobre la copia re-descargada, que debe devolver `[]`;
  6. liberación del estado y actualización del hash del registro de cursos.
- **Validación doble.** La petición se valida contra la base local en la vista previa, y otra vez contra la base descargada de Drive antes de tomar el lock. Si la operación resultante cambia (por ejemplo, otro equipo registró una clase y el siguiente `CLS-…-NN` es otro), se devuelve `PLAN_CHANGED` sin lock ni escritura.
- **Una transacción.** Todos los cambios de una operación (por ejemplo, la clase y su resumen) se aplican en una sola transacción SQLite, que incrementa `_meta.revision` una vez.
- **Idempotencia.** Los ids se fijan en el plan:
  - `NOTA-` y `TAR-` son un hash del contenido;
  - una clase de chat con la misma materia, fecha y título reutiliza su id.
  
  Repetir la misma petición con el mismo `plan_id` tras un éxito devuelve `already_applied: true` y `published: false`, sin filas duplicadas. Por eso dos notas idénticas de la misma materia y tipo cuentan como una sola.
- **Límites de texto.** Se recortan los espacios de los extremos. Los textos tienen un máximo de 4000 caracteres y los títulos de 200. Se rechazan textos que empiezan por `=` y caracteres nulos.
- **Índice.** Tras publicar, se reconstruye el índice de conocimiento sin que su fallo invalide la escritura. Si falla, la respuesta trae `index_refreshed: false` y un `warning`, y la escritura queda publicada.
- **Sin acceso libre.** Las órdenes no aceptan comandos ni `--job`/`--analysis`, y la única ruta que leen es el archivo de `--input`. Una opción que no pertenece a la acción se rechaza; no se ignora.

## Lectura

Lo guardado se consulta con `scripts\academic-read.cmd notes|tasks|tracking`. Ver [27-contrato-consulta-academica.md](27-contrato-consulta-academica.md). Estas consultas dependen de la base publicada (`workbook_version`), no del índice.
