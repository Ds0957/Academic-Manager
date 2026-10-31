# 27 — Contrato de consulta académica para ChatGPT Projects (solo lectura)

**Fecha:** 2026-10-08. **Estado:** implementado y probado localmente en PC Diego; integración con los Projects de ChatGPT **planificada, aún no configurada ni validada**. Este documento especifica una interfaz técnica del núcleo, no instrucciones finales del Project Coordinador.

## Propósito y frontera de responsabilidades

El Project de **Desarrollo** mantiene el repositorio y el contrato de consulta. El futuro **Coordinador** consultará estados y fuentes, organizará la vida universitaria conversacionalmente y abrirá GitHub Issues ante necesidades reales del sistema. Los futuros Projects de **materia** usarán consultas acotadas para estudiar. Ninguno necesita interpretar directamente rangos de Excel ni leer archivos internos del runtime a ciegas.

La interfaz está implementada en `academic_access/` y se ejecuta como Python local en Windows, con su wrapper `scripts/academic-read.cmd`. Es **exclusivamente de lectura**: no publica, crea clases, programa tareas, abre issues, modifica Canvas, escribe en Excel ni crea Projects. La lógica conversacional y la creación de issues son responsabilidades de los futuros Projects y sus conectores autorizados (ver `26-roles-coordinador-desarrollo-materias.md`).

## Uso

Desde la raíz de `C:\AcademicManagerDev\Academic-Manager`, en el equipo autorizado:

```powershell
scripts\academic-read.cmd capabilities
scripts\academic-read.cmd health
scripts\academic-read.cmd subjects
scripts\academic-read.cmd subject --materia MAT-ING3
scripts\academic-read.cmd activities --materia MAT-ING3 --limit 20
scripts\academic-read.cmd classes --materia MAT-SER
scripts\academic-read.cmd activity --actividad ACT-SO2-004 --materia MAT-SO2
scripts\academic-read.cmd search --materia MAT-SER --query "felicidad consumismo" --limit 3
scripts\academic-read.cmd source --fuente "clase:CLS-SER-20261001-01:analisis" --materia MAT-SER --offset 0 --length 3000
scripts\academic-read.cmd notes --materia MAT-SO2 --tipo decision
scripts\academic-read.cmd tasks --estado pendiente
scripts\academic-read.cmd tracking --materia MAT-SO2 --estado entregada
scripts\academic-read.cmd today --dias 7
scripts\academic-read.cmd summary
scripts\academic-read.cmd subject-brief --materia MAT-SO2
scripts\academic-read.cmd system
scripts\academic-read.cmd anomalies --materia MAT-SER
```

Para automatización local equivalente: `C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe -m academic_access ...`. Las llamadas requieren acceso autorizado a este PC. **No existe servidor HTTP público**, ni acceso de ChatGPT sin un conector/herramienta previamente autorizado. La nueva interfaz no exige ninguna función exclusiva de un plan concreto de ChatGPT.

La salida para cualquier petición válida sigue este contrato JSON:

```json
{
  "ok": true,
  "schema_version": 1,
  "action": "subjects",
  "data": {}
}
```

Los errores del contrato se entregan en JSON con `ok=false`, `error.code`, `error.message` y ocasionalmente `error.suggested_action`; código de salida del proceso `2`. Una operación exitosa devuelve salida `0`. **No se vuelcan tokens, contraseñas ni contenido íntegro de logs**. Los errores no equivalen automáticamente a un problema de código que justifique issue.

### Operaciones disponibles

| Acción | Qué ofrece | Control |
|---|---|---|
| `capabilities` | Versión, operaciones, límites y notas de uso | No accede a los datos |
| `health` | Coincidencia entre hash del índice y copia local publicada, semestre/bloque y último resultado conocido de Canvas Sync | No fuerza sincronización ni afirma disponibilidad remota en vivo |
| `subjects` | Materias, IDs, semestre, bloques y pertenencia al bloque activo | Lee configuración canónica del Excel, no crea registro local |
| `subject` | Cobertura de fuentes/actividades de una materia y advertencia de ausencia de material | ID exacto `MAT-*` |
| `activities` | Campos conservados de la hoja canónica, estados, fechas y puntuaciones | ID de materia obligatorio, paginación de 1–100 |
| `activity` | Actividad exacta con fuentes de eje y guías explícitas | Puede limitarse con `--materia`; relación contextual no se presenta como consigna |
| `classes` | Clases verificadas, rutas a transcripción/análisis | ID de materia obligatorio, paginación |
| `search` | Fragmentos literales de fuentes verificadas, con procedencia y ranking | Materia obligatoria, máximo 30 resultados, valida SHA de textos recuperados |
| `source` | Texto parcial de una fuente verificada y estado de paginación por caracteres | ID exacto, `--offset`, `--length` máximo 6000, SHA antes de lectura; admite `--materia` |
| `notes` | Notas, decisiones, aclaraciones y resúmenes de clase guardados desde el chat, de la más reciente a la más antigua | `--materia` y `--tipo` (`nota`, `decision`, `aclaracion`, `resumen_clase`) opcionales, paginación |
| `tasks` | Tareas personales, ordenadas por fecha límite (sin fecha al final) | `--materia` y `--estado` (`pendiente`, `en_progreso`, `hecha`, `descartada`) opcionales, paginación |
| `tracking` | Seguimiento de entregas con `materia_id` y `titulo` de la actividad | `--materia` y `--estado` (`pendiente`, `en_progreso`, `entregada`, `calificada`) opcionales, paginación |
| `today` | Qué hacer primero: entregas vencidas y próximas, tareas personales, clases sin procesar y materias sin movimiento, en orden determinista ([34-today.md](34-today.md)) | `--fecha YYYY-MM-DD` (por defecto hoy en `America/Bogota`) y `--dias` 1–60 (por defecto 14) opcionales; sin paginación |
| `summary` | Resumen corto para empezar un chat: semestre y materias activas, los 5 primeros elementos de `today`, notas recientes, tareas abiertas y estado del sistema (Canvas Sync, índice, token, publicación) ([35-summary.md](35-summary.md)) | `--fecha YYYY-MM-DD` opcional (por defecto hoy en `America/Bogota`); las fuentes opcionales que falten salen como `null`, no como error |
| `subject-brief` | Resumen de **una sola materia** para empezar el chat de su Project: `materia` (`materia_id`, `nombre`, `semestre_id`, `semestre_activo`, `ultima_clase`); `pendientes` (`items` de `today` de esa materia con horizonte de 60 días y su `resumen` por tipo); `entregas` (filas de `tracking`); `tareas` abiertas (`pendiente`, `en_progreso`, como en `tasks`); `decisiones` (todas las notas `decision`, completas, de la más reciente a la más antigua, como `notes --tipo decision`) y `resumenes_clase` (las 5 notas `resumen_clase` más recientes) | `--materia MAT-...` obligatorio; `--fecha YYYY-MM-DD` opcional (por defecto hoy en `America/Bogota`); sin paginación. Materia inexistente: `NOT_FOUND`; una materia sin nada guardado devuelve listas vacías. Una materia de un semestre cerrado no tiene `pendientes`, pero conserva sus decisiones |
| `anomalies` | Inconsistencias de datos del semestre activo: archivos de Canvas con el mismo nombre y distinto contenido, texto de plantilla («lorem ipsum», «texto de prueba») en recursos PDF y actividades sin fecha de entrega ([36-anomalias.md](36-anomalias.md)); solo informa, nunca corrige | `--materia MAT-...` opcional; sin paginación; si falta la evidencia local de PDF, `fuentes.recursos_pdf` es `no_disponible` y esos controles no devuelven elementos |
| `system` | Comprobación en vivo del sistema real, solo lectura: `estado` global (`ok`, `aviso` o `error`) y `comprobaciones` `{nombre, estado, detalle}` para `base_local`, `drive` (identidad, permisos de lectura y edición, lock compartido con su antigüedad, versión publicada frente a la local), `canvas_sync` (racha de la tarea diaria), `token_canvas`, `indice` y `tarea_diaria` (tarea programada de Windows). `no_disponible` no empeora el estado global. No repara nada: los locks retenidos se recuperan con `scripts\academic-store.cmd unlock` ([32-almacen-sqlite.md](32-almacen-sqlite.md)) | sin opciones |
| `capture-jobs` | Trabajos Capture y fase observada; porcentaje de fragmentos WhisperX, ETA de transcripción orientativa y señales de publicación/registro | `--job latest` opcional o lista acotada; no inicia ni modifica procesos. Un job `completed` local puede no estar publicado |

`notes`, `tasks`, `tracking`, `today`, `summary`, `subject-brief` y `anomalies` leen la base publicada; las tres primeras, las tablas que escribe el chat ([33-escritura-desde-chat.md](33-escritura-desde-chat.md)):

- Exigen que la base local coincida con el hash publicado (`workbook_version`), **no el índice**, así que no devuelven `INDEX_STALE`. Si no coinciden, devuelven `SOURCE_UNAVAILABLE`.
- Su respuesta incluye `workbook_sha256`.
- `--estado` y `--tipo` solo valen en `notes`, `tasks` y `tracking`; `--fecha`, solo en `today`, `summary` y `subject-brief`; `--dias`, solo en `today`. En las demás consultas son `INVALID_ARGUMENT`. `subject-brief` solo acepta `--materia` y `--fecha`: `--limit`, `--offset`, `--query` y los demás también son `INVALID_ARGUMENT`.

Fechas estructuradas de Excel se serializan como ISO local, conservando valores ausentes `null` y puntuación de cero como `0`; no se inventan horas de vencimiento, calificación institucional ni importancia académica. Se etiqueta explícitamente la zona `America/Bogota` para las fechas almacenadas en Excel. Las materias transversales figuran una sola vez, con `bloques=[1,2]`.

### Diagnóstico y escalamiento

Códigos tratados por el consumidor:

- `INVALID_ARGUMENT`: corregir parámetros/IDs; no crear issue.
- `INVALID_CONFIG`: el archivo de configuración del usuario (`%APPDATA%\AcademicManager\config.json` o el indicado en `ACADEMIC_MANAGER_CONFIG`) no es válido; el mensaje nombra el archivo. Corregirlo o borrarlo ([03-almacenamiento-y-seguridad.md](03-almacenamiento-y-seguridad.md)); no crear issue.
- `NOT_FOUND`: ID no registrado o no pertenece a la materia solicitada; verificar antes de crear contenido.
- `NO_TEXT`: recurso catalogado sin contenido de lectura autorizado (por ejemplo, Inglés III restringido); **no inventar contenido** ni intentar saltarse permisos.
- `INDEX_STALE`: el índice no corresponde al SHA del Excel local; reconstruir con `knowledge.cmd build` según el procedimiento ya documentado; no marcar las obligaciones como actualizadas.
- `SOURCE_CHANGED`: el archivo falta o su SHA no coincide; comprobar publicación, acceso y reconstrucción del índice antes de atribuirle contenido.
- `SOURCE_UNAVAILABLE`, `INVALID_SOURCE`: problema de disponibilidad/integridad. Diagnosticar primero; si revela limitación sistémica real no cubierta por documentación, escalar mediante el flujo de GitHub Issues.
- El último código de salida de Canvas Sync en `health` es local, conocido y puede tener estado `unknown`; una ejecución fallida no es prueba de falla permanente del producto.

**Contrato de frescura:** `index.fresh=true` significa únicamente que el SHA del Excel local validado coincide con el SHA referenciado por el índice de conocimiento. **No confirma consulta actual a Canvas o Google Drive** ni que todas las fuentes externas estén disponibles. `search` y `source` vuelven a comprobar SHA de los archivos que devuelven. La tarea de Windows actualiza el índice después de Canvas Sync, pero la consulta en sí nunca inicia tareas de mantenimiento.

### Validación real inicial

Sobre la base actual del semestre `2026-2`: cinco materias, bloque activo 2, Inglés III en ambos bloques, 31 actividades, 133 fuentes, 34 con texto y 63 relaciones. Se consultaron realmente las nueve actividades de Inglés III con paginación, las clases de SER, el contexto de `ACT-SO2-004`, la búsqueda de SER sobre felicidad/consumismo y un fragmento del `analysis.md` de SER con hash comprobado. Un ID inexistente devolvió JSON estructurado `NOT_FOUND`. Las pruebas unitarias cubren lectura por materia, control de tamaños, `null` frente a cero, integridad de fuentes, índices desactualizados y errores sin filtrar secretos de logs.

**Regresión e integridad verificadas:** `scripts/dev-check.ps1` terminó con **126 tests ejecutados, 2 omitidos**. Una comprobación posterior confirmó `EXCEL_HASH_UNCHANGED=True`, `INDEX_MANIFEST_UNCHANGED=True`, estado remoto conocido `free`, hash local igual al publicado y `academic_knowledge build` con `changed=false` (5 materias, 133 fuentes, 34 legibles y 63 relaciones). **No hay cambio en el workbook, Canvas, Drive, índice original ni tareas programadas** como resultado de esta interfaz; solo código, pruebas y documentación. La primera validación desde los Projects reales queda pendiente.

## Siguiente paso coherente

Dejar **el núcleo de acceso como contrato reutilizable**. En la Etapa 3 se preparará el Project Coordinador y los Projects de materias con instrucciones breves para invocar estas acciones mediante acceso al PC permitido; se validará también la conexión GitHub para issues. No se requiere crear ahora un asistente paralelo en Python ni un servicio remoto público.


### Estado de revisión del análisis (2026-10-12)

La consulta existente \`capture-jobs\` añade \`analysis_status\` por job, con valores \`pending\`, \`reviewed\` o \`unknown\` si la metadata no puede comprobarse de forma segura. Se deriva de la metadata local cuyo SHA-256 coincide con \`job.json\`; la consulta sigue siendo **solo lectura**. No representa por sí sola calidad semántica del informe ni garantiza publicación/registro; esos campos siguen separados. La incorporación es un comando de **Capture**, no una operación de escritura en \`academic_access\`. Consultar [05-capture.md](05-capture.md).
