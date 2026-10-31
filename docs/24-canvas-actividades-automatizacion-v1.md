# 24 — Etapa 1 V1: actividades Canvas, calificaciones y rutina diaria

**Fecha:** 2026-10-08. **Estado:** sincronización de actividades reales y programación automática implementadas y validadas en PC Diego. La prueba con dos PCs físicos queda **explícitamente aplazada por el estudiante**, no requerida para dar por usable la Etapa 1 en este equipo.

## Contrato funcional confirmado

Academic Manager V1 mantiene la base académica canónica en Excel/Drive. Canvas es una **fuente de lectura** de materias, asignaciones y entregas. La rutina periódica actualiza únicamente campos externos verificados; **nunca modifica Canvas**. Se preservan los datos manuales (descripciones redactadas, decisiones del estudiante, pesos porcentuales, notas institucionales y entregables verificados).

- `canvas_sync/classification.py`: clasifica por evidencia y conserva reglas/decisiones en `C:\AcademicManagerData\control\canvas-sync\assignment-decisions.json`. Clave externa estable `curso Canvas:assignment ID`; un cambio de nombre posterior obliga a revisión antes de reclasificar automáticamente.
- `canvas_sync/workbook_activities.py`: compara la versión canónica bajo el protocolo de hash/bloqueo con snapshots locales recientes verificados; crea nuevas actividades del **bloque activo** y actualiza calificaciones/estados en actividades ya registradas de cualquier bloque previo **si ese curso permanece accesible**. Los cursos de bloques anteriores no visibles no detienen la rutina actual ni se tratan como eliminados. No incorpora automáticamente actividades antiguas desconocidas de semestres inactivos.
- IDs internos nuevos deterministas: `ACT-C<canvas_course_id>-A<assignment_id>`; no se duplican actividades en futuras sincronizaciones. Los existentes conservan sus IDs de V2 (`ACT-GBD-001`, etc.).
- Una actividad con `submission.score` real mantiene sus **puntos obtenidos** y estado `calificada`. Puntos posibles, entrega, cierre y apertura se separan; `due_at=null` no se sustituye por `lock_at`. No se crea cero a partir de null ni se interpreta peso porcentual desde los puntos.
- Fechas API Canvas con zona explícita (`Z`/UTC) se convierten a **America/Bogota** para los valores Excel. Para evitar cambios de cinco horas por COM se escriben directamente como números seriales de Excel, con formato de fecha/hora; los valores UTC originales permanecen en snapshots verificados.
- Las fechas o puntuaciones curadas que entren en conflicto con información de Canvas se marcan para revisión según el campo; se preservan los datos manuales no corroborados en vez de sobreescribirlos a ciegas.
- **Extensión mínima de V2.1 en `02 - Actividades`:** `fecha_envio_real` (columna Z / 26, fecha-hora local de entrega real del alumno) y `referencias_externas` (columna AA / 27, JSON estructurado genérico `sistema/tipo/id` para un quiz o discussion adicional, sin reemplazar el `assignment_id` principal). No se añade una hoja ni se borra la información previa. Se valida el tipo e integridad de cada ID.
- Clasificación conservadora: título explícitamente evaluativo + `published` permite importar una actividad. Elementos `Nota final [P1]` son resúmenes de calificación, no obligaciones individuales; `Class recordings` y `Tutor & class link` son recursos informativos, no actividades. Las **complementarias** solo entran como tipo `complementaria` cuando Canvas acredita puntos y mecanismo real de entrega; **NO se afirma que sean obligatorias** ni que deban priorizarse como tareas ordinarias. El `Learning Agreement` de Inglés III se registra al comprobar una entrega calificada de 4/4, sin inventar nuevas obligaciones.
- Casos que sigan siendo ambiguos o con nombre cambiado se conservan como excepciones; comando interactivo `activities-review` permite tomar una decisión **una sola vez** y guardarla sin tener que repetirla en cada corrida.

## Resultados reales del 2026-10-08

Base anterior: **7 actividades**. Los 38 assignments Canvas de los cinco cursos (GBD, Sistemas Transaccionales, Inglés III, SO2 y SER) se clasificaron en **31 actividades de Canvas**, **cinco resúmenes `Nota final`** y **dos enlaces informativos**.

1. Primera publicación verificada: **17 nuevas actividades**, 289 cambios de celda, hash `117a4b3301f143a9f192d8d85b8bc134b9d9781771fa5163b398ea496578fa89`. La siguiente previsualización regresó **0 cambios**; workbook con 24 actividades y estado remoto `free`.
2. Revisión basada en evidencia real: seis complementarias evaluables tratadas como `tipo=complementaria` y el acta calificada de Inglés III tratada como actividad; 7 nuevas actividades, 114 cambios, hash `af86c3034d3b2150ffead6801784ec5f1d715cca6b3678b1d9876e3186fb37eb`. Total: **31 actividades**. La clasificación dejó cero casos dudosos en las 38 evidencias actuales, sin presuponer obligatoriedad de las complementarias.
3. Migración estructurada `fecha_envio_real` y `referencias_externas`: ensayo sobre copia del Excel encontró **cinco fechas reales de envío** y **cuatro referencias externas**. Tras corregir conversión Excel/UTC, segunda ejecución sobre copia terminó con **0 diferencias y 0 conflictos**. La publicación final en Drive **se completó y verificó**: **11 celdas modificadas** (2 encabezados, 5 fechas y 4 referencias), hash final `ff41a86f40bc355ecf9685c9b86d48e9c993f344d19bb91d696f5a3b6f3cc54f`, estado compartido `free` y una nueva previsualización con **0 diferencias / 0 excepciones**. Se verificó específicamente el envío de Inglés III del 26 de septiembre a las 12:05 (hora Bogotá).
4. No se alteraron grabaciones, materias, clases, recursos ni entregables existentes durante estas importaciones.
5. **Cierre de la cola antigua:** tras completar las importaciones, se archivó localmente el informe previo de `bloque2/latest.json` y se regeneró el estado desde evidencias y workbook publicados; comprobación final de `daily --offline`: **38 registros, 31 actividades vinculadas, 7 no-actividades y 0 pendientes, 0 alertas repetidas**. Una regresión del reconciliador evita que futuras importaciones confirmadas permanezcan como `evidencia_cambio_sin_diferencia_visible`.

## Automatización configurada en este equipo

Windows Task Scheduler: `AcademicManager-CanvasDaily`, usuario `diego`, dos disparadores diarios **08:00 y 18:00**, zona horaria de Windows `SA Pacific Standard Time` (Bogotá). Acción local:

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\AcademicManagerDev\Academic-Manager\scripts\canvas-daily.ps1
```

- La tarea corre `python -m canvas_sync daily --apply`: consultar Canvas por GET, detectar nuevos cursos no asociados, reconciliar, proponer y publicar **solo diffs seguros** al libro canónico usando el lock/SHA/checkpoint/Drive API/re-descarga de `workbook_blocks.py`, luego persistir clasificaciones.
- Se comprobó la ejecución iniciada desde el Programador, con código de salida **0**, sin ChatGPT abierto y sin nuevas escrituras al no haber cambios. `MultipleInstances=IgnoreNew`, `StartWhenAvailable=true`.
- Requiere que **el PC esté encendido y la sesión autorizada de Windows permanezca iniciada**; no depende del plan de ChatGPT. Si Canvas o Drive falla, el estado local conserva el error y la ejecución retorna código de error: no se afirma que sincronizó correctamente.
- Logs: `C:\AcademicManagerData\procesamiento\canvas-sync\logs\canvas-YYYY-MM-DD.log` y `latest-status.json`. **Todavía no hay notificaciones móviles o correos proactivos**; los errores/excepciones pueden revisarse allí o mediante el asistente cuando el equipo está accesible.
- `scripts\install-canvas-daily-task.ps1` reinstala o modifica la tarea. No se solicita credencial de usuario a ChatGPT.

## Comandos de operación y diagnóstico

```powershell
scripts\canvas-sync.cmd daily                  # GET + informe, no publica Excel
python -m canvas_sync daily --apply             # GET + publicación canónica controlada
scripts\canvas-sync.cmd activities-preview     # diff LOCAL, no publica
scripts\canvas-sync.cmd activities-review      # decisión humana excepcional
python -m canvas_sync daily --offline           # solo snapshots ya guardados, sin publicación
```

**Regresión final:** `scripts/dev-check.ps1` terminó con **112 pruebas ejecutadas, 2 omitidas**. Además de la clasificación y la importación reales se probó con datos sintéticos que un curso del bloque anterior puede actualizar una nota existente sin crear obligaciones nuevas, y que timestamps y referencias externas son idempotentes.

## Criterio de cierre de Etapa 1

**Cumplido en PC Diego:** alta de semestres/bloques, clasificación de assignments, persistencia/idempotencia de decisiones, inscripción controlada de cursos, actualización segura de actividades/calificaciones reales y tarea recurrente probada. La primera matrícula de un semestre futuro solo se validó con datos sintéticos, por inexistencia de matrícula nueva real.

**No es requisito actual por decisión del estudiante:** prueba simultánea con segundo PC físico. Permanece anotada como validación diferida para una etapa posterior.

**Mejoras no bloqueantes / pendientes técnicos:** notificación proactiva externa de fallos, primera alta real de futuro semestre, recuperación de edición remota interrumpida ante bloqueo residual, control/uso de histórico de bloques ya inaccesibles a Canvas, refinamientos de casos especiales por materia y auditoría de posibles modificaciones de fecha efectiva de envío. Estos puntos no se presentan como funciones comprobadas.
