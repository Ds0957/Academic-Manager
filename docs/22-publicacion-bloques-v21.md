# 22 — Publicación canónica de bloques y recuperación entre equipos

**Fecha:** 2026-10-08. **Estado:** primera migración V2.1 publicada y validada en Google Drive desde PC Diego. **Límite:** el alta de materias aún inexistentes en Excel no está automatizada; no se ha probado simultáneamente con dos computadores reales.

## Decisión confirmada

La pertenencia de una materia a bloque 1, bloque 2 o ambos y el bloque activo del semestre forman parte de la base académica canónica. Se almacenan como extensión mínima y compatible de la V2, preservando sus siete hojas.

| Hoja | Nueva columna | Tipo | Semántica |
|---|---|---|---|
| `01 - Materias` | `bloques` (col. P) | texto `1`, `2` o `1,2` | Una materia puede abarcar ambos bloques sin duplicar el ID |
| `05 - Semestres` | `bloque_activo` (col. G) | entero `1` o `2` | Bloque actual, independiente del estado semestral |

Migración del `2026-2`: `MAT-ING3=[1,2]` y `MAT-GBD`, `MAT-SER`, `MAT-SO2`, `MAT-ST=[2]`, con `bloque_activo=2`. Se completó `curso_externo_id=82892` y `lms=Canvas` para MAT-ST conforme a vínculo previamente verificado. No se agregaron materias del bloque 1 sin evidencia ni se alteraron actividades, notas, clases, recursos o entregables.

## Protocolo de publicación ejecutado

Código: `canvas_sync/workbook_blocks.py`; reutiliza `capture.academic_manager.drive_workbook`, `database.validate_workbook`, `_state_lock` y `_ensure_lock`.

1. Identifica en Drive, por carpeta compartida, el único `database-work-state.json` correspondiente al workbook. Usa la identidad técnica guardada **localmente** en `control/service-account-drive.json`, nunca sube secretos al repositorio.
2. Lee el estado remoto `free`, descarga la versión canónica y valida su SHA-256 y estructura.
3. Previsualiza cambios de celda y rechaza IDs Canvas contradictorios, semestres inconsistentes, materia inexistente o columnas ocupadas.
4. Adquiere lock compartido y confirma que persiste, crea un checkpoint y modifica **una copia de trabajo** mediante el adaptador Microsoft Excel COM ya validado.
5. Verifica los datos resultantes, que no quedan diferencias en los campos objetivo y que las siete hojas mantienen contrato V2.
6. Descarga nuevamente la versión anterior para comprobar ausencia de escrituras concurrentes, verifica el lock, publica por Drive API, re-descarga y compara bytes y campos.
7. Alinea `C:\AcademicManagerData\database.xlsx`, actualiza el estado compartido con el nuevo SHA y deja `status=free`/`lock=null`. Conserva checkpoint de recuperación. Ante error posterior al lock, no lo libera ciegamente.

**Resultado real:**
- SHA inicial `9b4686373de57881b36a5712b70df263f98272a5392508c662fd2a21189962c8`.
- **10 modificaciones** exactas: 2 encabezados, 5 asociaciones, 1 bloque activo, 2 campos Canvas faltantes en MAT-ST.
- SHA publicado final `6c5a415933c4f62d434259a442565eb579c637885d7c33fcb7d7744e74eaf682`.
- Checkpoint `C:\AcademicManagerData\temporales\database-20261008-095837-before-blocks.xlsx`.
- Estado final `free`, hash local=registro=Drive, Excel abierto y validado, segunda previsualización con **0 diferencias**.

## Uso operativo

```powershell
scripts\canvas-sync.cmd blocks-preview
scripts\canvas-sync.cmd blocks-publish
scripts\canvas-sync.cmd blocks-refresh
```

- `blocks-preview`: consulta Drive y muestra un diff sin escribir.
- `blocks-publish`: exige aprobación interactiva `PUBLICAR`; solo publica campos previamente verificados y con el lock completo.
- `blocks-refresh`: actualiza desde Drive el Excel y reconstruye el `course-registry.json` local desde las **columnas canónicas**, rechazando sobrescritura de decisiones de matrícula no publicadas o de cambios de bloques pendientes. No escribe Drive. Cada PC necesita su propia identidad técnica/autorización configurada.
- `canvas_sync/semester.py` reconstruye la configuración al primer arranque sin registro local: lee `bloques`, `bloque_activo` y sus IDs directamente de Excel; sigue conservando migración desde la versión anterior cuando las columnas todavía no existen, pero **nunca mezcla una matrícula desconocida con otro semestre**.

La publicación se probó en PC Diego. La recuperación para otro PC se probó con un **runtime sintético aislado** creado a partir de copia verificada del Excel canónico: recreó `2026-2`, bloque 2 y cinco materias con las pertenencias correctas. Se ejecutó asimismo `blocks-refresh` conectado. Esto **no sustituye** la prueba completa desde el segundo computador físico.

## Pendientes antes de cerrar Etapa 1

- **Actualizado posteriormente en `docs/23-alta-canonica-materias.md`:** `enroll-preview` / `enroll-publish` y `onboard` ya contemplan altas de cursos confirmados en `01 - Materias` y `05 - Semestres` con verificación Canvas y protocolo completo. `blocks-publish` sigue dedicado a las relaciones de bloques; la primera matrícula nueva real aún no se ha probado.
- Validar transacciones concurrentes desde **dos PCs reales** (stale hashes, locks y recuperación).
- Reconciliar notas tardías de materias del bloque anterior y clasificar/aprobar la cola de 24 registros pendientes sin trabajo manual recurrente.
- Programar `daily` y comprobaciones de credenciales sólo cuando el flujo y sus alertas sean operativamente seguros.

**Pruebas:** batería completa `scripts/dev-check.ps1`: **98 tests OK, 2 omitidos**. Incluye escritura Excel COM sobre un libro sintético, rechazo por ID externo en conflicto, materia faltante, repetición sin diferencias y transición de bloques, además de las pruebas anteriores del conector. El sistema sigue sin actualizar actividades automáticamente.
