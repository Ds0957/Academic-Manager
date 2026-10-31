# 23 — Alta canónica controlada de nuevas materias y semestres

**Fecha:** 2026-10-08. **Estado:** implementado y probado con matrícula **sintética** y workbook Excel COM; listo para usarse cuando Canvas presente cursos nuevos reales. No se han inventado ni registrado nuevos cursos del estudiante en Drive.

## Objetivo confirmado

Una vez por matrícula/bloque, el estudiante selecciona en un catálogo Canvas autenticado qué cursos incorporar y a qué bloques pertenecen. Academic Manager conserva esos datos, previsualiza el cambio, solicita confirmación de publicación y realiza el protocolo completo de escritura sobre Google Drive. Si no hay novedades no repite tareas ni altera el libro. El usuario no tiene que editar manualmente columnas de Excel ni modificar código cada semestre.

## Modelo e invariantes

- `onboard --semester YYYY-N` ya solicitaba los IDs Canvas y `blocks` (`1`, `2` o `1,2`). Ahora persiste también el **nombre y código** auténticos vistos en la selección, con procedencia `aprobado_local_pendiente_excel`.
- Antes de crear filas, `publish_enrollment` revalida los cursos pendientes con **Canvas autenticado en vivo**: mismo ID, nombre y código; rechaza cursos que desaparecieron o cambiaron de identidad y textos que Excel podría interpretar como fórmulas. No deduce cursos solo por nombre parecido, duración aproximada o calendario.
- Nuevo `materia_id` determinista por cursada: `MAT-<YYYYN>-C<canvas_course_id>`. Nunca duplica un curso Canvas en dos materias ni inventa profesores, horarios, fechas, notas, actividades, calificaciones, archivos o recursos.
- Si el semestre confirmado es el mismo que está activo, añade únicamente las materias nuevas: **no cambia semestre/bloque activo**. Si es un semestre distinto, crea el nuevo registro `05 - Semestres` con nombre generado del código confirmado, `estado=Activo` y `bloque_activo` explícito; cambia el semestre anterior de `Activo` a `Inactivo` **como estado operativo, no como aprobación, cierre académico ni calificación**. Conserva las filas anteriores.
- Materias nuevas de `01 - Materias`: `materia_id`, `semestre`, nombre y código Canvas verificados, `lms=Canvas`, `curso_externo_id`, bloques confirmados, `estado=Activa` como registro de matrícula; lo desconocido se deja nulo. `MAT-ING3` continúa con un único `materia_id`, aunque esté en ambos bloques.
- No se permite agregar materias de un semestre distinto del activo aprobado, recuperar materias no verificadas, sobrescribir IDs externos contradictorios, usar IDs internos arbitrarios ni sobrepasar la plantilla de 200 filas.
- Las publicaciones repetidas sobre la misma versión comprobada generan un **diff vacío** (idempotencia). El estado `aprobado_local_pendiente_excel` pasa a `workbook` **solo después** de publicación y revalidación correctas.
- La base publicada sigue siendo la fuente canónica; `blocks-refresh` y el arranque sin registro local reconstruyen las asociaciones desde las hojas `01 - Materias` y `05 - Semestres`.

## Operación

```powershell
scripts\canvas-sync.cmd courses-review
scripts\canvas-sync.cmd onboard --semester YYYY-N
```

En la incorporación interactiva, tras confirmar `CONFIRMAR`, se propone la publicación como paso del **mismo comando**: muestra número de modificaciones y solicita `PUBLICAR`. Si se omite, deja el alta local pendiente, sin modificar Drive. Se puede reanudar con:

```powershell
scripts\canvas-sync.cmd enroll-preview
scripts\canvas-sync.cmd enroll-publish
```

`enroll-preview` nunca publica; `enroll-publish` solicita consentimiento explícito en terminal real. Ambos vuelven a consultar el catálogo Canvas **solo si hay altas pendientes**. La nueva función `canvas_sync/workbook_enrollment.py` genera un diff de celdas; `canvas_sync/workbook_blocks.py` reutiliza el mismo protocolo de lock remoto → hash → checkpoint → Excel COM → validación → comprobar base/lock remoto → Drive API → re-descarga y SHA → actualizar estado → liberar. **En un fallo posterior al lock no se desbloquea ciegamente:** se detiene para recuperación supervisada.

## Validaciones

Con **fixtures exclusivamente sintéticos**, se probó:
- alta en un semestre existente sin cambiar el bloque activo;
- transición a otro semestre, conservación de su anterior estado como `Inactivo` y creación del nuevo como `Activo`, sin fechas inventadas;
- una materia con `blocks=[1,2]` sin duplicados;
- reintento después de escritura, diff de 0 celdas;
- rechazo a nombre Canvas cambiado, ausencia de curso, duplicados, materia no aprobada e identificadores contradictorios;
- prueba simulada de publicación completa con bloqueo, hash, checkpoint, re-descarga, liberación y promoción de la procedencia a `workbook`.

**Pruebas generales:** `scripts/dev-check.ps1` terminó con **104 tests ejecutados (2 omitidos)** en el equipo Diego.

**Comprobación real sin escritura:** `enroll-preview` frente al workbook 2026-2 devolvió **0 celdas pendientes**, porque actualmente no hay matrículas nuevas aprobadas. `daily --offline` sigue reportando 38 registros Canvas y 0 novedades; **no se editó Excel** durante este incremento. El último SHA canónico conserva el valor de `docs/22-publicacion-bloques-v21.md`.

## Pendientes

1. Ejecutar por primera vez una matrícula **real** futura y revalidarla desde una segunda instalación física. Las pruebas sintéticas no equivalen a una matrícula real nueva.
2. Mantener el inventario histórico de materias del bloque 1 de 2026-2: no se inventan si ya no son visibles en Canvas.
3. Resolver la cola de 24 registros de actividades con decisiones persistentes y habilitar actualización automática de datos académicos confirmados.
4. Recuperar calificaciones tardías de bloques pasados y programar `daily` con manejo de credenciales, avisos y errores.
5. Reforzar recuperación ante fallo parcial de publicación para los escenarios donde se haya alterado Drive pero todavía no se haya actualizado el registro local, y validar la concurrencia real con dos PCs.
