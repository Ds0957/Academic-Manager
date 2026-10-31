# 21 — Semestres, matrícula y continuidad de cursos Canvas

**Estado (2026-10-08):** pertenencia **multibloque** y bloque activo **publicados en el XLSX canónico** tras migración local verificada, con Inglés III transversal a ambos bloques. El alta futura de nuevas materias/semestres sigue local y pendiente de persistencia automática; la réplica se probó en un entorno simulado, no en dos PCs físicos. Ver `22-publicacion-bloques-v21.md`.

## Objetivo confirmado

Academic Manager debe conservar el historial de todos los semestres y poder incorporar cursos nuevos sin editar el código ni solicitar cada día la misma clasificación. Una nueva cursada se representa con **otra instancia de materia** e ID interno diferente, incluso si el nombre de asignatura se repite. Las fechas o nombres de Canvas por sí solos **no prueban** a qué semestre pertenece un curso: el estudiante confirma la correspondencia solo una vez.

## Origen y persistencia actual

- `01 - Materias`: contiene `materia_id`, `semestre`, `lms`, `curso_externo_id` y ahora **`bloques`**; `05 - Semestres` contiene `semestre_id`, `estado` y ahora **`bloque_activo`**. Estas columnas están publicadas y se pueden reconstruir desde la base canónica.
- `canvas_sync/semester.py`: lee Excel **ReadOnly** y comprueba SHA-256/estado local antes de inicializar un registro local con el semestre marcado como `Activo`.
- `C:\AcademicManagerData\control\canvas-sync\course-registry.json`: mantiene semestres, IDs de curso Canvas, IDs internos distintos y procedencia; `active_semester` es una selección local explícita.
- **Migración histórica única (completada):** cuatro de los cinco cursos 2026-2 se obtuvieron inicialmente de `01 - Materias`. MAT-ST no tenía el `curso_externo_id`, pero la relación verificada `82892 ↔ MAT-ST` se conservó y ahora **se publicó en Excel** junto con `lms=Canvas`. La excepción no se usa para inventar materias futuras.
- `C:\AcademicManagerData\procesamiento\canvas-sync\discovery\latest.json`: último catálogo de cursos Canvas visibles y sus candidatos sin asociación, por SHA. Los nombres e IDs de cursos proceden de un GET autorizado; no se infiere el semestre automáticamente.
- `C:\AcademicManagerData\procesamiento\canvas-sync\routine\semestres\<semestre>\latest.json`: informes de reconciliación segmentados por semestre. El informe legado `routine\latest.json` de 2026-2 se conserva para migrar sin repetir avisos históricos. Los snapshots V0.1 mantienen carpetas por ID externo de curso.

La correspondencia recién aprobada **solo localmente** tiene `provenance=aprobado_local_pendiente_excel`; no se presenta como materia definitivamente publicada ni aparece todavía en el Dashboard. No se modifica el workbook compartido en esta etapa.

## Flujo para el semestre siguiente

1. La rutina `daily` consulta los cursos visibles y los contrasta con el registro; si hay desconocidos, los muestra como **curso sin asociar**. No crea materias automáticamente.
2. Para revisar la oferta, desde la terminal autorizada ejecutar:

   ```powershell
   scripts\canvas-sync.cmd courses-review
   ```

3. Cuando se **conozca y confirme** el código del semestre y cuáles cursos realmente corresponden a la matrícula, iniciar la incorporación interactiva:

   ```powershell
   scripts\canvas-sync.cmd onboard --semester YYYY-N
   ```

   Introducir los IDs seleccionados desde la lista autenticada y la palabra exacta `CONFIRMAR`. Si se cancela, no se guarda nada. No se aceptan IDs que Canvas no devolvió, asociaciones duplicadas ni semestres con formato inválido. El ID interno de la nueva instancia se genera de forma estable, por ejemplo `MAT-20271-C990001` para un curso **sintético**: no es una asignatura real del estudiante.

4. La selección del nuevo semestre queda activa en el registro local; los cursos anteriores permanecen asociados a sus respectivos semestres. Desde entonces `daily` procesa solo los cursos confirmados del **semestre y bloque activos**; sus informes se guardan por semestre y bloque.
5. **Ya implementado en código (2026-10-08):** tras confirmar, `onboard` permite previsualizar y publicar `01 - Materias`/`05 - Semestres` mediante lock compartido, SHA remoto, checkpoint, Excel COM, Drive API, re-descarga, validación y liberación. Si se omite `PUBLICAR`, el alta queda preparatoria local; puede retomarse con `enroll-publish`. **Probado con cursos sintéticos, no con una matrícula real nueva.** Ver `23-alta-canonica-materias.md`.

La rutina **no declara que un curso fue eliminado** si Canvas deja de exponerlo temporalmente. Si alguno de los cursos asociados al semestre activo no aparece, suspende esa reconciliación (sin sobrescribir el informe anterior), muestra los IDs faltantes y devuelve un código de atención; el usuario puede revisar si cambió de semestre o si existe un problema de visibilidad/permisos.

## Validaciones reales y simuladas

- Lectura real de las hojas `01 - Materias` y `05 - Semestres` mediante Excel COM en el PC Diego. Semestre activo: `2026-2`; cinco vínculos recuperados, cuatro de Excel y uno con excepción documentada.
- `courses-review` auténtico: **cinco cursos visibles y asociados; cero nuevos**. Ninguna matrícula posterior ha sido supuesta.
- `daily --offline` tras migración: **38 registros, 7 coincidencias parciales, 7 excluidos de candidaturas de actividad, 24 pendientes, 0 novedades**; nuevo informe en carpeta `semestres/2026-2`, sin alterar el anterior.
- `daily` completo conectado: cinco cursos GET-only, **66 solicitudes API de extracción**, cinco snapshots sin cambios y **0 novedades**. Se mantuvo el XLSX sin modificar.
- Tests con datos **inventados exclusivamente dentro de fixtures**, no introducidos a Academic Manager: validación de ID de semestre, ausencia/reaparición de aulas, alta confirmada, rechazo de duplicados, idempotencia de descubrimiento, aislamiento de ID de actividad reutilizado entre semestres, registro recuperable.

## Lo que queda abierto

- Sincronizar y publicar de manera segura las **nuevas materias/semestres** dentro del workbook V2 y comprobar conflicto entre equipos; la selección del registro local no sustituye la persistencia canónica.
- Recuperar las asociaciones aprobadas entre computadores o reconstruirlas desde V2 sin depender de un registro local que pudiera perderse.
- La referencia Canvas `82892` de `MAT-ST` **ya se publicó** con el protocolo obligatorio en la migración de bloques del 2026-10-08. No requiere una segunda corrección.
- Definir archivado del semestre anterior solo tras confirmación; no borrar historial al cambiar `active_semester`.
- Activar programación recurrente de `daily` solo tras validar coordinación, gestión de token y alertas. `onboard` seguirá requiriendo confirmación humana una vez por materia/semestre.


## Calendario académico confirmado (2026-10-08)

El régimen habitual del estudiante comprende dos bloques consecutivos de aproximadamente dos meses de estudio cada uno, dentro de un semestre de aproximadamente seis meses que incluye un periodo de descanso. No se conocen fechas exactas ni se han inferido a partir de Canvas.

La pertenencia de las cinco materias registradas de **2026-2** fue aclarada posteriormente: cuatro pertenecen al bloque 2 e Inglés III abarca **ambos** bloques. Se implementó en la capa local como se describe a continuación. El inventario de materias exclusivamente del bloque 1 continúa incompleto.


## Implementación de bloques académicos y materias transversales (2026-10-08)

**Estado: Confirmado e implementado en la capa local.** El modelo real es **semestre → dos bloques**, pero una materia puede tener pertenencia **[1]**, **[2]** o **[1, 2]**. Esto último es esencial: **Inglés III se cursa en ambos bloques** y conserva **una sola** instancia de materia `MAT-ING3` y **un solo** curso Canvas `91003`, sin dividir actividades, notas ni grabaciones. La continuación de inglés en semestres futuros es una expectativa expresada por el estudiante; **no se asignarán automáticamente materias futuras a dos bloques** ni se presupondrá su calendario.

**Correspondencia confirmada para 2026-2:**

| materia_id | curso Canvas | bloques |
|---|---:|---|
| MAT-ING3 | 91003 | [1, 2] |
| MAT-GBD | 82862 | [2] |
| MAT-SO2 | 91004 | [2] |
| MAT-SER | 91005 | [2] |
| MAT-ST | 82892 | [2] |

El semestre activo es **2026-2**, el **bloque activo es 2**. No contamos todavía con la lista verificable de asignaturas **exclusivas del bloque 1**: no se han inventado ni inferido. Las cinco materias actuales no constituyen necesariamente el inventario completo del semestre.

**Contrato local:**
- `course-registry.json` se actualiza de esquema **1 a 2** de forma atómica e idempotente. Añade `active_block` (1/2) y `blocks: [1]`, `[2]` o `[1,2]` por curso. Solo la migración documentada de los cinco cursos actuales 2026-2 asigna bloques automáticamente a partir de lo confirmado por el estudiante. Datos históricos desconocidos mantienen `blocks: []` y bloque activo no confirmado, **bloqueando la conciliación**, nunca inventándolos.
- `active_mappings` selecciona únicamente las materias del semestre **y bloque activo**. Un curso transversal aparece en ambos conjuntos con **el mismo ID**; no duplica filas, clases ni entregas.
- `daily` revisa únicamente visibilidad obligatoria para las materias **del bloque activo**: la ausencia en Canvas de una materia exclusiva de un bloque anterior **no suspende** el bloque actual. Las evidencias históricas permanecen disponibles; la vigilancia automática de calificaciones tardías de bloques pasados sigue **pendiente**.
- Los informes nuevos se guardan bajo `procesamiento/canvas-sync/routine/semestres/<semestre>/bloque<1|2>/latest.json`. Se reutiliza como antecedente el informe del otro bloque o el anterior del semestre, **filtrando por cursos compartidos**: la continuidad de inglés no genera alertas duplicadas ni falsas eliminaciones al cambiar de bloque.
- Alta guiada `onboard --semester YYYY-N`: solicita **por curso** los bloques `1`, `2` o `1,2`, confirmados una vez. Si es otro curso del mismo semestre, **no cambia el semestre ni el bloque activo**. Al entrar en un semestre nuevo, selecciona inicialmente el primer bloque representado en la nueva matrícula.
- `course-blocks`: permite corregir la pertenencia de un curso ya registrado, por ejemplo ampliar Inglés de `[1]` a `[1,2]`, **sin duplicarlo**.
- `block-set --block 1` / `block-set --block 2`: transición explícita del bloque activo, sin crear semestre nuevo ni borrar el anterior. Ambas acciones requieren confirmación interactiva en el PC autorizado; no modifican el Excel.

**Comandos locales:**

```powershell
scripts\canvas-sync.cmd courses-review
scripts\canvas-sync.cmd daily
scripts\canvas-sync.cmd onboard --semester 2027-1
scripts\canvas-sync.cmd course-blocks
scripts\canvas-sync.cmd block-set --block 2
```

El valor `2027-1` es **un ejemplo de uso futuro**, no una matrícula real. El código jamás interpreta el nombre «Inglés» como autorización para asignarlo de manera transversal automáticamente en otro semestre.

**Validaciones:** pruebas sintéticas cubren pertenencia múltiple, conservación de un único `materia_id`, cambio de bloque sin cambio de semestre, descubrimiento de nuevas materias por bloque, falta de visibilidad de materias del bloque anterior, migración V1→V2 del registro local e idempotencia de alertas para un curso compartido. En la migración real del PC Diego: registro local de cinco cursos, `2026-2` bloque 2 activo, `MAT-ING3=[1,2]`; `daily --offline` devolvió 38 registros, 7 coincidencias parciales, 24 pendientes y **0 novedades**. El Excel canónico y el estado de publicación compartido **no se modificaron**.

**Hito completado:** `bloques` y `bloque_activo` ya se publicaron de forma canónica y fueron re-descargados y verificados. **Pendiente:** alta canónica de materias nuevas, coordinación con segundo computador físico, vigilancia de notas tardías del bloque anterior e inventario del bloque 1, además de la programación diaria. El contrato de actualización/recuperación entre PCs está en `docs/22-publicacion-bloques-v21.md`.
