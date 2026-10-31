# 20 — Etapa 1 V1: reconciliación Canvas Sync → V2

**Nota de continuidad:** este documento conserva el diagnóstico inicial de solo lectura (cuando había 24 casos por revisar); la Etapa 1 operativa posterior, con 31 actividades publicadas, cola en cero y tarea recurrente, está documentada en `docs/24-canvas-actividades-automatizacion-v1.md`.

**Fecha:** 2026-10-08. **Estado:** previsualización y rutina cotidiana local implementadas y validadas, estrictamente de solo lectura. **No implementado:** importación/publicación automática de **actividades** en Excel ni programación horaria autónoma. La migración canónica de **bloques y cursos existentes** ya está publicada por separado en `22-publicacion-bloques-v21.md`.

## Objetivo y alcance

El conector enriquecido `sync-rich` ya conserva snapshots Canvas de cinco cursos. Su clasificación de assignments es provisional, por lo cual está prohibido tratar cada registro como actividad pendiente ordinaria. La primera fase incorpora `canvas_sync/reconcile.py` y `reconcile-preview` para comparar las capturas verificadas contra la copia local `C:\AcademicManagerData\database.xlsx`, abrir Excel **ReadOnly**, identificar coincidencias por `(materia_id, assignment_id)` y proponer revisión. No crea, actualiza o elimina registros, ni sube archivos a Drive ni contacta Canvas.

## Ejecución

```powershell
& "C:\AcademicManagerDev\Academic-Manager\scripts\canvas-sync.cmd" reconcile-preview --all-active
# Individual: reconcile-preview --course-id 82862
```

La salida es JSON por stdout; conserva IDs, SHA de los snapshots, diferencias limitadas, referencias extra quiz/discussion y `submitted_at` observado. No almacena una segunda copia del workbook ni crea registros de actividad. El origen utilizado son los snapshots locales previos: para actualizar las evidencias hay que ejecutar `sync-rich` de manera separada.

## Primera comprobación real

Comparación con los cinco snapshots enriquecidos existentes y la copia local del Excel, en PC Diego:
- 38 assignments de Canvas revisados;
- 7 coincidencias sin diferencias **en los campos comparados**;
- 0 cambios detectados **en esos campos**;
- 17 candidatos nuevos pendientes de clasificación;
- 14 registros marcados para revisar categoría informativa/agregada/complementaria.

Se comparan por ahora título, puntos posibles y, si ambos existen, puntos obtenidos. Las fechas, pesos, estados, descripción, entregables y notas aún **no se reconcilian**. Por tanto la categoría `coincidencia` **no implica igualdad integral** y ningún candidato está autorizado para importación automática. `classification_confirmed=false` de Canvas es evidencia de clasificación provisional, no autorización. No se inventan IDs internos ni se transforman categorías informativas en obligaciones.

Se validó localmente con `scripts/dev-check.ps1`: **76 tests OK, 2 skipped**, incluida una nueva batería de cinco pruebas sintéticas para coincidencia, cambio, no mutación, hash y duplicados. No se modificaron `database.xlsx`, el estado compartido ni Canvas durante la prueba.

## Siguientes incrementos antes de cualquier escritura

1. Formalizar revisión humana/confirmación de categorías y el vínculo estable entre IDs internos y varios IDs Canvas.
2. Comparar campos académicos completos con normalización de fechas/zonas horarias, nulos, notas, entregas y calificaciones, distinguiendo dato confirmado de provisión.
3. Proponer cambios con diff campo a campo y fuente/versión verificable; realizar dry-run repetible, y comprobar que no cambió la versión publicada ni el snapshot.
4. Solo tras aprobar el protocolo de importación: desarrollar escritura sobre copia válida bajo **lock → SHA → checkpoint → validar → publicar por Drive API → re-descargar → Excel → alinear → liberar**. No escribir directamente al disco Drive Desktop.
5. Pruebas integradas con duplicados, conflictos, reintentos/idempotencia, bloqueo multi-PC y regresión de datos existentes.

**Decisión confirmada:** la primera frontera Canvas → V2 será revisión de solo lectura y fallará ante snapshots/hash inválidos o IDs ambiguos. **Pendiente:** contrato final de V2.1 y autorización de las mutaciones estructuradas.

## Segunda fase: rutina de revisión para uso cotidiano (2026-10-08)

**Implementado en código:** `canvas_sync/routine.py` y comando `scripts/canvas-sync.cmd daily`. Une sincronización GET-only `sync-rich --all-active` + reconciliación local, verifica hash del snapshot y consistencia del workbook con el estado **local** publicado, y guarda un informe versionado por contenido en:

```text
C:\AcademicManagerData\procesamiento\canvas-sync\routine\semestres\<semestre>\bloque<1|2>\latest.json
```

`daily --offline` ejecuta la misma comparación con los snapshots locales, sin peticiones de red (sirve para pruebas y diagnóstico). El comando normal utiliza la autorización Canvas DPAPI ya existente. En ausencia de cambios en las evidencias, **no reescribe el informe** ni vuelve a anunciar asuntos ya identificados; mantiene la lista persistente de pendientes y distingue los elementos verdaderamente nuevos o cambiados. La rutina genera señales de actualización por hash de la evidencia, incluidos campos aún no reconciliados como fechas, rúbricas e instrucciones. Una modificación de un registro existente se conserva como excepción pendiente, aunque el comparador limitado no encuentre todavía diferencias en sus tres campos actuales; no la sobrescribe automáticamente.

**Filtro automático muy limitado, no destructivo:** cinco `Nota final [P1]` (agregados de nota) y dos enlaces informativos de Inglés III se apartan de la **cola de actividades para importar**, pero siguen presentes en el snapshot y en el informe local de excluidos. Los complementarios y el `Learning Agreement` continúan en revisión. Clasificar en esta cola **no equivale a aprobación académica ni modifica ninguna entidad**.

**Validación real con datos autorizados:** primera comparación local: 38 assignments; 7 vinculados, 7 apartados de actividades y 24 casos pendientes para revisar. Primera ejecución completa conectada de `daily` en equipo Diego: 5 cursos, 66 GET Canvas, cinco snapshots sin cambio y **0 novedades**; resultado local idempotente, sin escritura al XLSX o Canvas. Se empleó el estado `free` y se cotejó SHA del workbook local con `published.sha256` del **estado local**.

**Limitación importante:** este cotejo no confirma por sí solo que el estado compartido de Drive no cambió desde el último alineamiento del PC. Antes de habilitar importación o programación automática definitiva, hay que comparar la última versión **remota real** y conservar el protocolo completo de publicación. Tampoco hay tarea de Windows programada aún: actualmente es un solo comando invocable manualmente o desde un orquestador futuro.

**Próximas tareas:** decisión de revisión persistente por ID (aprobada/recurso/ignorada) para vaciar la cola una sola vez; normalización completa de cambios (fecha, estado, calificaciones, entregables, zona horaria); reconciliación contra estado remoto antes de leer/escribir; importación segura a V2.1 bajo lock y validación; finalmente programar frecuencia y notificaciones de excepciones sin depender del chat.
