# 32 — Almacén SQLite (fuente de verdad)

**Estado:** En desarrollo en la rama `feat/sqlite-core`. **No desplegado:** el runtime sigue con `database.xlsx` hasta el corte supervisado (ver al final).

## Decisiones

**2026-10-22.** La fuente de verdad pasa a ser `<runtime>/academic-manager.sqlite`. Se mantiene el modelo de un solo escritor (`database-work-state.json`: `status`, `lock`, `published.sha256`). Después del corte, `published.sha256` es el hash de la base SQLite.

**2026-10-22.** Excel desaparece del sistema. El usuario: «quita lo de excel, no lo voy a revisar ni usar».
- No hay vista XLSX generada, ni Excel COM, ni `pywin32`.
- El único código que lee XLSX es el lector de la biblioteca estándar, usado por la migración y el corte.
- Los documentos anteriores (07, 21–25) describen el diseño previo con Excel.

Motivo: con Excel cada lectura abría Excel por COM. Eso no funcionaba desde WSL ni desde el parser XLSX de RDC, y obligaba a un protocolo largo para no romper el workbook.

## Esquema

- Una tabla por hoja V2: `materias`, `actividades`, `clases`, `recursos`, `semestres`, `entregables`. Las columnas son los encabezados de V2, en el mismo orden.
- La primera columna es el identificador: único y obligatorio.
- `dashboard` conserva el tablero anterior tal cual, por posición (`col_1`, `col_2`, …).
- `_columnas` guarda, por cada columna:
  - el orden y el encabezado;
  - la posición de columna original (`columna`);
  - el tipo `bool` de las columnas booleanas. `procesada` y `procesado` están declaradas como booleanas aunque la hoja esté vacía.
- `_meta` guarda `schema_version`, `revision`, `source_sha256` y `migrated_at_utc`.
- Los valores conservan la forma heredada de V2: números como float, fechas como serial de Excel (días desde 1899-12-30, hora local America/Bogota), booleanos como bool y vacíos como `NULL`. Normalizarlo queda como mejora posterior. Las tablas nuevas no deben heredar esta forma.

## Comandos

```text
python -m academic_store migrate [--xlsx RUTA] [--db RUTA]
python -m academic_store verify  [--xlsx RUTA] [--db RUTA]
python -m academic_store info    [--db RUTA]
python -m academic_store cutover [--apply]          # corte supervisado, una sola vez
```

- Por defecto se usan `ACADEMIC_MANAGER_DATA` (o `C:\AcademicManagerData`), `database.xlsx` y `academic-manager.sqlite`.
- `migrate` crea la base en un temporal y la verifica hoja por hoja (filas, encabezados, valores y tipos) antes de instalarla. **Nunca sobrescribe** una base existente.
- La migración falla y no crea nada ante cualquiera de estos casos: hojas que faltan o sobran, encabezados duplicados, filas con datos sin identificador, identificadores duplicados, datos en columnas sin encabezado o columnas que mezclan booleanos con otros valores.

## Lectores

- `academic_knowledge.index.excel_catalog` (nombre heredado), y a través de él `academic_access` y el índice.
- `canvas_sync.routine.workbook_version`: comprueba el hash de la base contra el estado publicado.
- `canvas_sync.semester.workbook_courses`: lee de la base local, o de una copia `.sqlite` descargada.
- `canvas_sync.reconcile.read_v2_activities` y `preview`, que por defecto leen la base.
- Los errores de SQLite (archivo corrupto, sin esquema) se informan como «Base SQLite ilegible…».

## Escritores y publicación

- Los planificadores de Canvas no cambiaron su lógica de validación:
  - `workbook_blocks.plan` (bloques);
  - `workbook_enrollment.plan_enrollment` (altas);
  - `workbook_activities.plan_activities` (actividades).
- Leen la base con `academic_store.sheetview.open_book`, una vista de solo lectura organizada por hojas, filas y columnas.
- `workbook_blocks.mutate` aplica los diffs `(hoja, fila, columna, antes, después)` con `store.apply_cells`. Todo el lote va en una transacción:
  - Si algún `antes` no coincide, no se aplica nada («cambió desde la previsualización»).
  - Rechaza cambios de encabezado, columnas desconocidas, tipos incorrectos en columnas booleanas, fórmulas (`=…`), altas sin identificador e identificadores duplicados.
  - Cada lote aplicado incrementa `revision`.
- `workbook_blocks.publish` y `refresh_from_published` siguen el mismo protocolo que antes: lock, comprobación de hash, checkpoint, edición, verificación, subida, re-descarga, comprobación y liberación. El artefacto es `academic-manager.sqlite` (MIME `application/vnd.sqlite3`) y se valida con `store.validate`, que revisa esquema, tablas e `integrity_check`.
- `capture.academic_manager.database.register_class` mantiene su firma y su protocolo:
  - Busca la clase repetida por materia, fecha y grabación.
  - Calcula el siguiente `CLS-…-NN`.
  - Inserta en la primera fila libre (2–500).
- `capture.academic_manager.drive_workbook` importa las librerías de Google solo al usar Drive, y tiene `create` para crear archivos nuevos (nunca reemplaza).

## Base nueva para una persona nueva (`bootstrap`)

`academic_store.bootstrap.create(runtime, drive_root)` lo usa el paso 6 del asistente ([38-asistente-configuracion.md](38-asistente-configuracion.md)); no tiene CLI. Solo actúa si la carpeta de datos no tiene `academic-manager.sqlite`, `database.xlsx`, `control/database-work-state.json`, `control/canvas-sync/course-registry.json` ni `control/academic-operations.json`, y si la carpeta de Drive no tiene ya la base, su estado ni un `database.xlsx`. Nunca sobrescribe, fusiona ni borra lo que existía, en el PC ni en Drive.

- La base se crea con `store.create_empty`, que usa la misma construcción y verificación que `migrate` sobre una plantilla de solo encabezados (`bootstrap.template`, tomada de `workbook_enrollment`, `workbook_activities` y `capture.academic_manager.database`). `_meta` lleva `origen = vacia` en lugar de `source_sha256`.
- Se publica con la identidad de `_drive_service` (cuenta de servicio o el inicio de sesión propio del asistente; ver [03](03-almacenamiento-y-seguridad.md#identidad-para-publicar-la-base-en-drive)) como archivo nuevo `Academic Manager - Database.sqlite`, se re-descarga y se compara; el estado compartido `database-work-state.json` se crea libre (`schema_version` 1, `resource`, `canonical_drive_file_id`, `published.sha256`) y se relee; `_state_id` debe encontrarlo junto a la base. Luego se instala igual que tras el corte (`cutover._install`) y se escribe `control/academic-operations.json` (`schema_version` 1, `drive_root` del paso 5, `database_state_file_id` = el estado recién creado, sin `subjects`), validado con `academic_operations.actions.check_config`, la misma regla que lo lee; la carpeta del paso 5 debe llamarse «Academic Manager» y se comprueba antes de crear nada. Si ya existe otra configuración de operaciones, no se toca: sin diario impide empezar y con diario detiene el paso (queda «a medias»).
- Diario `procesamiento/bootstrap/journal.json` (carpeta de Drive, identificadores creados, hash de la base preparada, paso). Al retomar, un archivo de Drive se adopta solo si su identificador está en el diario o su hash/estado coincide con lo preparado; si no, se detiene sin tocarlo. Si la subida de la base propia llegó dañada, se vuelve a subir una vez sobre ese mismo archivo.
- Una base recién creada no tiene semestres: `semester.onboard` crea el primer registro con el semestre elegido (`is_empty_base`) y `plan_enrollment` admite añadir el primer semestre solo cuando la base no tiene ninguno.

## Corte supervisado (pendiente de ejecución)

Se ejecuta **una sola vez**, en el PC, con `ACADEMIC_MANAGER_DATA` apuntando al runtime y con la tarea diaria detenida:

```text
python -m academic_store cutover            # simulación: no escribe nada
python -m academic_store cutover --apply    # publica
```

1. **Precondiciones.** Exige que no exista `academic-manager.sqlite`, que el estado compartido esté libre y que la copia local `database.xlsx` y la de Drive coincidan con `published.sha256`.
2. **Migración.** Migra la copia descargada a un candidato verificado.
3. **Simulación.** Sin `--apply`, devuelve los conteos y termina.
4. **Lock y checkpoint.** Con `--apply`, toma el lock y guarda un checkpoint del xlsx en `temporales/database-…-before-cutover.xlsx`.
5. **Archivo nuevo en Drive.** Crea en la misma carpeta `Academic Manager - Database.sqlite`, la re-descarga y la valida.
   - La cuenta de servicio no tiene cuota propia en un Drive personal y **no puede crear archivos** (Drive responde con error de cuota). Por eso, antes del corte, el dueño crea el archivo en la carpeta, aunque esté vacío. Puede hacerlo copiándolo en `G:\Mi unidad\03 - Universidad\Academic Manager\` con Drive para escritorio. Si el archivo ya existe, el corte sube la base encima. La simulación lo indica con `store_file`: `reuse` o `create`.
   - Si hay varios archivos con ese nombre, el corte se detiene antes de tomar el lock.
6. **Liberación.** Libera el estado con:
   - `canonical_drive_file_id` = la base;
   - `legacy_xlsx_drive_file_id` = el xlsx anterior;
   - `published.sha256` = el hash de la base y `published.source_xlsx_sha256` = el hash del xlsx de origen.
7. **Instalación local.** Solo después instala localmente la base y actualiza `workbook_sha256` del registro de cursos.
8. **Después.** Hay que reconstruir el índice con `scripts\knowledge.cmd build`.

**El xlsx anterior no se modifica** ni en Drive ni en el equipo: queda como archivo histórico. Si algo falla con el lock tomado, el lock se conserva para recuperación supervisada y no se instala nada localmente.

La instalación local es atómica: la base se copia a un temporal y se instala con `os.replace`. Si falla después de liberar Drive (disco lleno, antivirus, permisos), el error lo indica y **repetir `cutover --apply` completa la instalación**: detecta que Drive ya apunta a la base (`legacy_xlsx_drive_file_id` = el xlsx local), la descarga, comprueba `published.sha256`, la valida e instala (`mode: resumed`). `cutover` no admite `--xlsx`/`--db`, y `--apply` solo vale para `cutover`.

## Recuperación supervisada de un lock (`unlock`)

```text
scripts\academic-store.cmd unlock            # simulación: describe y no escribe
scripts\academic-store.cmd unlock --apply    # libera
```

Cuando una publicación falla con el lock tomado, el protocolo lo conserva a propósito. `unlock` lo libera solo si es seguro, y en cualquier otro caso se niega y explica por qué:

1. El lock es de **este** equipo (`COMPUTERNAME`); si es de otro, se recupera desde ese equipo.
2. Tiene más de 10 minutos, o no tiene fecha: ninguna publicación normal dura tanto.
3. No hay una escritura del chat en curso; su turno se mantiene tomado mientras dura `unlock`.
4. El lock se tomó desde la versión publicada (`base_sha256` = `published.sha256`) y la base de Drive, descargada y validada, sigue teniendo ese hash: nada se subió bajo el lock.

Con `--apply` vuelve a leer el estado remoto, lo libera sin cambiar `published`, lo confirma y actualiza el estado local; si la base local difiere de la publicada, instala la copia descargada y el registro de cursos (como el corte). La simulación lo avisa (`base_local: se_reemplazara`) y, antes de sustituirla, guarda la base local en `temporales\database-…-before-unlock.sqlite` (`copia_local` en el resultado): lo que solo estaba en este equipo no se pierde. Si la instalación local falla después de liberar Drive, repetir `unlock` la termina (`instalar_publicada`, con su copia). Si Drive y el estado local están libres pero la base local es otra, `unlock` no la sustituye: hay que actualizar el equipo. Si Drive ya está libre y solo el estado local quedó en edición, lo repara copiando el remoto, siempre que la base local coincida con la publicada.

Hasta hacer el corte **no** se debe desplegar esta rama: los lectores y escritores ya exigen la base SQLite. El seguimiento está en `odd/tasks/sqlite-core.md`.
