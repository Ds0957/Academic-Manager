# 31 — Operaciones académicas controladas para el Coordinador

**Fecha:** 2026-10-12. **Estado actual:** `preview` y `complete` validados en uso real desde el Project Coordinador en Diego, con autorización específica del estudiante; aceptación del issue #5 confirmada para una clase. No es un servicio público ni otorga permisos generales de escritura a ChatGPT.

## Necesidad observada

El Coordinador preparó análisis de SO2 y Sistemas Transaccionales, pero una invocación remota de incorporación fue rechazada antes de ejecutarse. Desarrollo pudo completar el flujo técnico y detectó además una incompatibilidad de estados que corrigió. **No se conoce con certeza la regla específica que causó el rechazo original de la herramienta.** No hay evidencia de que solo Desarrollo tenga más permisos.

La solución no consiste en eludir el control de herramientas ni en permitir comandos arbitrarios. La operación cotidiana debe tener una interfaz definida, limitar sus entradas y conservar autorización y comprobaciones de integridad.

## Separación de contratos

- `academic_access` sigue siendo **exclusivamente de lectura**.
- `academic_operations` ofrece únicamente **`preview` y `complete` de clases Capture existentes**. No acepta comandos de sistema, escritura de rutas arbitrarias, materias no autorizadas ni cambios de código.
- Capture mantiene las operaciones independientes para incorporar análisis, publicar los cuatro archivos y registrar en el workbook. El orquestador llama a estos contratos existentes; **no escribe directamente Excel ni modifica hashes de trabajo**.
- La revisión académica y la aprobación de publicar corresponden al estudiante, asistido por el Coordinador. Ni `--approve` ni el estado `reviewed` demuestran automáticamente exactitud semántica.

### Escrituras desde el chat (2026-10-22)

`academic_operations` también acepta `track`, `note`, `task`, `task-status` y `class`. Sirven para guardar desde el chat el estado de entregas, notas y decisiones, tareas personales y clases sin Capture.

- Siguen el mismo contrato de dos pasos: vista previa con `plan_id`, y después `--plan-id … --approve`.
- No necesitan `--job` ni el archivo `academic-operations.json`.
- `--job` sigue siendo obligatorio para `preview` y `complete`, que no cambian.
- Aceptan `--input RUTA.json` con los campos en un archivo JSON; es la forma recomendada para texto libre, porque `cmd.exe` altera `% ^ & "`.

Contrato completo en [33-escritura-desde-chat.md](33-escritura-desde-chat.md).

### Desde el conector de Claude (2026-11-06)

`completar_clase` ([43](43-conector-claude.md)) reutiliza `preview` y `complete` sin cambiarlos. Genera el informe del índice por minutos, lo guarda como `claude-<job>.md` en `analysis-review`, corre `complete` en un proceso aparte y guarda el índice.

## Configuración inicial por Desarrollo (una vez por equipo)

Archivo local `C:\AcademicManagerData\control\academic-operations.json`, no versionado y sin credenciales:

```json
{
  "schema_version": 1,
  "drive_root": "<RAIZ_LOCAL_AUTORIZADA_DE_ACADEMIC_MANAGER>",
  "database_state_file_id": "<ID_REAL_DEL_ARCHIVO_DE_ESTADO>",
  "subjects": {
    "Sistemas Operativos II": "MAT-SO2",
    "Sistemas Transaccionales": "MAT-ST"
  }
}
```

**`subjects` es opcional desde el issue #16 (2026-10-25).** `preview` y `complete` resuelven el nombre visible de la materia del job de Capture (`--subject`) con la base local: buscan ese nombre en `01 - Materias` entre las materias del semestre activo, sin distinguir mayúsculas ni espacios al principio o al final. Así, una materia dada de alta en la base no necesita editar este archivo. Si el nombre está en `subjects`, esa entrada manda, como antes. Si no coincide con ninguna materia del semestre activo (o no se puede leer la base), la operación falla con `SUBJECT_UNMAPPED`; si coincide con varias, con `SUBJECT_AMBIGUOUS`. En ambos casos no se escribe nada. `subjects` puede faltar o estar vacío; si existe, debe ser un diccionario de nombres a `MAT-...` válidos (`INVALID_CONFIG` si no).

Si se conserva, el diccionario de materias debe reflejar las matriculaciones vigentes verificadas, ampliándose solo mediante el proceso de desarrollo/alta de materias. El ID de estado no es una contraseña. Las credenciales Drive permanecen en su mecanismo local existente, fuera de este JSON. Una instalación nueva no debe copiar la unidad de otro PC sin validación.

La operación rechaza configuraciones incompletas, materias desconocidas y rutas fuera del área de informes `procesamiento/analysis-review`. La raíz Drive debe corresponder a la carpeta autorizada `Academic Manager` y ser independiente del runtime.

## Uso conversacional esperado

Cuando el estudiante pide completar una clase, el Coordinador:

1. Consulta `academic-read.cmd capture-jobs --job ID`; comprueba transcripción, estado de publicación/registro y la materia correcta.
2. Prepara o revisa `procesamiento/analysis-review/<INFORME>.md`. Debe contrastar afirmaciones con la transcripción, no reutilizar texto de otro job a ciegas.
3. Ejecuta **solo lectura**:

   ```powershell
   scripts\academic-act.cmd preview --job JOB_ID --analysis INFORME.md
   ```

   La respuesta JSON indica materia, fecha, nombre verificado, hash de transcripción/informe, estado previo, fases faltantes y `plan_id` ligado a los datos. No escribe.
4. Presenta el resumen al estudiante y **solicita aprobación específica** para incorporar el informe, publicar en Drive y registrar en Excel. Nunca supone aprobación por haber generado un borrador.
5. Solo con esa aprobación ejecuta:

   ```powershell
   scripts\academic-act.cmd complete --job JOB_ID --analysis INFORME.md --plan-id HASH_DEL_PREVIEW --approve
   ```

6. Contrasta `published`, `database_registered`, ID de clase y `index_refreshed`. Informa de pendientes y avisos sin confundir `completed` de transcripción con clase integrada.

Cuando el análisis ya está `reviewed`, puede omitirse `--analysis`; el sistema **no reemplaza** el análisis de una clase publicada. La revisión y publicación se realizan bajo locks locales y el protocolo existente del workbook. Reanudar después de una falla requiere una **nueva vista previa y aprobación**, que evita reutilizar un plan obsoleto.

## Garantías y límites

- Las entradas de archivo son **solo nombres Markdown** en `analysis-review`, no rutas libres, URLs ni comandos.
- La vista previa revalida los hashes de los cuatro artefactos, identidad de job, materia mapeada, grabación y fecha. El identificador del plan vincula esos datos, el informe y la configuración; cualquier cambio exige otra vista previa.
- Para adjuntar, se copia el informe aprobado a una copia privada local y se comprueba su hash antes de entregarlo a Capture. Capture guarda procedencia y verifica la transcripción exacta.
- La publicación reutiliza el verificador SHA-256 y la prevención de sobrescritura de Capture. El registro usa lock compartido, SHA base, checkpoint, Drive API, re-descarga, Excel y liberación. No se modifican clases existentes sin flujo de versionado.
- La operación conserva resultados parciales: si la publicación funciona pero el registro falla, el siguiente intento no necesita volver a transcribir ni reemplazar la clase. `complete` reconstruye el índice local al final; un fallo de índice se informa como advertencia distinta de publicar/registrar.
- `--approve` es una declaración procedimental de aprobación explícita del usuario, **no una forma de saltarse controles** de un conector. Si Remote Desktop Commander u otra herramienta deniega la acción, el Coordinador debe respetar el bloqueo, informar el motivo disponible y solicitar que el usuario use la interfaz local autorizada o que Desarrollo revise el conector. No fragmentar comandos para eludir un rechazo.
- El núcleo no depende de planes concretos de ChatGPT. La integración futura podría exponer las mismas dos operaciones a través de un conector autenticado, con permisos acotados, sin un shell abierto ni un servidor público sin controles. **Esa capa específica sigue pendiente.**

## Validación y siguiente hito

Se añaden tests sintéticos de preview sin escrituras, planes obsoletos, consentimiento, rutas restringidas, mapeo de materias, registro, publicación, reanudación idempotente, recuperación de fallo intermedio y aviso de actualización de índice. La primera configuración y prueba de preview del PC Diego y la ejecución efectiva **desde el Project Coordinador** deben documentarse por separado. No usar clases reales para probar escrituras si ya están registradas.

**Siguiente paso:** conectar/autorizar la operación en el Project Coordinador, ejecutar allí una vista previa de un job real sin mutación y, cuando haya una nueva clase pendiente y el estudiante la apruebe, comprobar una finalización real. Si el conector sigue negando una ejecución legítima, diagnosticar sus permisos antes de ampliar la arquitectura.

## Primer despliegue local comprobado (2026-10-12)

En Diego se instaló la configuración restringida en `C:\AcademicManagerData\control\academic-operations.json`, sin almacenar credenciales. Git `main` quedó en `3df7136` al desplegar. Se realizaron vistas previas reales **de solo lectura** sobre SO2 y Sistemas Transaccionales, ambas ya publicadas y registradas; el módulo Python y el wrapper `scripts/academic-act.cmd` respondieron JSON correcto con materia, estado `reviewed`, SHA e ID de clase.

Regresión general de la implementación: 165 pruebas, 2 omitidas; prueba focal posterior de publicación parcialmente reservada: 9 pruebas aprobadas. **No se utilizó `complete` sobre datos reales ni se volvió a registrar una clase**. La prueba desde el propio Project Coordinador se registró posteriormente (véase el hito siguiente); la operación de escritura `complete` sigue pendiente de validación desde ese Project.

## Verificación desde el Project Coordinador (2026-10-12)

**Confirmado según la ejecución comunicada por el propio Coordinador:** en el PC Diego, Remote Desktop Commander aceptó `scripts\academic-act.cmd preview --job 83ac7042104d4ce6bb793543714f6fb5`. El resultado tuvo código de salida `0`, `ok=true` y `action=preview`; identificó la materia Sistemas Operativos II, análisis `reviewed`, publicación y registro confirmados, clase `CLS-SO2-20260929-01` y único paso indicado `refresh_index`. La llamada fue de **solo lectura**: no se aprobó el plan ni se ejecutó `complete`; no hubo modificación de datos.

**Criterio de cierre pendiente:** una nueva clase aún no publicada permitirá probar `complete` desde el Coordinador, **únicamente cuando el estudiante haya aprobado específicamente** el análisis, la publicación y el registro. El éxito de `preview` demuestra acceso de lectura al contrato operativo, no permiso de escritura ni solución definitiva al rechazo remoto original.

**Frescura del índice comprobada por Desarrollo (solo lectura):** `scripts\academic-read.cmd health` informó `status=stale` e `index.fresh=false` en Diego, respecto al workbook ya registrado. En esta sesión no se solicitó aprobación para reconstruirlo y no se ejecutó ninguna operación de escritura. La inclusión de `refresh_index` en un `preview` no sustituye a `health` como comprobación efectiva de frescura; el estado debe contrastarse con ambas consultas cuando corresponda.

## Diagnóstico y reparación de la incidencia #5 (2026-10-12)

**Problema real informado por el Coordinador:** dos clases del 8 de octubre presentaron `preview=ok` y `complete=OPERATION_UNAVAILABLE`, tras aprobación expresa, sin incorporación, publicación ni registro. El error genérico ocultaba el tipo y la etapa original. La tercera clase pendiente no se intentó finalizar.

**Comprobaciones no destructivas en Diego:** los jobs, informes y hashes siguen válidos. Se reprodujo `complete` con una copia aislada de los artefactos reales, raíz Drive ficticia y registro de Excel simulado; completó todas las fases. En el job original se comprobó la llegada hasta el punto previo al intercambio de directorios de `analysis.md`, interceptándolo antes del renombrado real; la metadata original permaneció en `pending`, sin temporales ni diario residual. Por eso es plausible una denegación de acceso transitoria de Windows durante el intercambio, pero **el error del intento histórico no se conservó y su causa exacta no está demostrada**.

**Corrección implementada:** los renombrados del conjunto de artefactos (incluida recuperación) reintentan únicamente `PermissionError` con esperas acotadas, conservando validación, journal y rollback. `academic_operations complete` ahora comunica en JSON la `stage` afectada (`acquire_lock`, `validate_plan`, `attach_analysis`, `verify_analysis`, `publish`, `register_database`, `verify_completion`, `refresh_index`) y un código estable y seguro, por ejemplo `FILE_ACCESS_DENIED`, `FILE_NOT_FOUND`, `LOCAL_IO_FAILURE`, `INVALID_OPERATION_STATE` o `CAPTURE_FAILED`; nunca devuelve la ruta/exception original. `retryable` indica solo una posibilidad de reintento técnico: el Coordinador debe volver a inspeccionar el job, obtener un `preview` nuevo y obtener aprobación acorde al plan vigente. No hay reintentos ciegos de publicación/registro.

**Validación de código:** las pruebas de errores de acceso, ausencia de archivos, procedencia segura del error, renombrados transitorios, fallos persistentes y rollback se añaden a los tests existentes. Se han pasado 27 pruebas focalizadas en PC Diego, incluida incorporación/publicación/registro sintéticos y recuperación. La aceptación operativa **aún requiere que el Coordinador vuelva a ejecutar una clase realmente pendiente mediante un plan vigente y aprobación del estudiante**, verifique hashes/Drive/Excel y confirme que `health.index.fresh=true` después. No tratar la corrección de observabilidad como prueba de que el bloqueo histórico ya desapareció.

**Despliegue de reparación (2026-10-12):** PR #6 fusionado, `main` local de Diego en `89645f2`; suite completa Windows **171 OK (2 omitidas)** y **27 pruebas focalizadas OK**. Vista previa del job de Transaccionales del 8 de octubre respondió `ok=true` sin modificar los artefactos. La finalización real desde Coordinador permanece pendiente de nueva ejecución autorizada; si vuelve a fallar, conservar solo `error.code`, `error.stage`, `error.os_error` numérico cuando esté y el estado de `capture-jobs`, sin datos personales, para decidir el siguiente arreglo.

## Segundo incremento para Windows: alternativa a renombrado de directorios (2026-10-12)

El Coordinador comunicó después del PR #6 un error real `FILE_ACCESS_DENIED` en `attach_analysis` con error Windows **5**, pese a `preview` y aprobación correcta. Los tres jobs nuevos permanecen pendientes. En PC Diego Desarrollo comprobó los atributos de los artefactos (sin marca de solo lectura), que la creación, sincronización y renombrado de archivos y directorios temporales funcionan dentro de los jobs y que una prueba de `os.replace` sobre archivos temporales internos de `artifacts` también funciona. **No se renombraron ni sobrescribieron los archivos canónicos durante el diagnóstico.**

Se implementó una **alternativa transaccional** al intercambio de directorios, únicamente si Windows deniega el renombrado del directorio original, con copia previa validada, diario `mode=files`, sustitución temporal de solo `analysis.md` y `metadata.json`, verificación de los cuatro hashes, rollback/crash recovery y conservación de `job.json` hasta confirmar la integridad. El transcript permanece byte a byte inmutable. Si Windows también bloquea un archivo individual, se devuelve código de error seguro y `error.substage` acotado; no se eluden controles del sistema ni se fuerzan permisos.

**Este incremento está implementado y probado con casos sintéticos, NO validado todavía sobre las tres clases pendientes desde Coordinador.** El contrato externo `preview` / `complete`, la aprobación y la idempotencia siguen intactos. El cierre del issue requiere una nueva aceptación real, con estado, registro y frescura del índice comprobados. Ver `docs/05-capture.md` e [issue #5](https://github.com/Ds0957/Academic-Manager/issues/5).

## Aceptación operativa del issue #5 (2026-10-12)

**Confirmado por el Coordinador y verificado independientemente por Desarrollo:** tras instalar PR #7 (`46ac096`) y obtener aprobación explícita, el Coordinador ejecutó `complete` una sola vez para el job de Sistemas Transaccionales del 8 de octubre. El resultado fue `ok=true`, código 0, `analysis_status=reviewed`, `published=true`, `database_registered=true`, `index_refreshed=true`, sin advertencias. El registro quedó como `CLS-ST-20261008-01`.

El Coordinador verificó SHA-256 de los cuatro archivos originales/publicados, ausencia de duplicados, lectura del análisis e índice fresco. Desarrollo realizó consultas independientes en Diego con `capture-jobs`, `classes --materia MAT-ST` (dos clases: 30 de septiembre y 8 de octubre) y `health` (`status=ok`, `fresh=true`, sin advertencias). No se forzó ningún permiso ni hubo modificación manual de Excel, Drive o hashes. **La operación real cumple el criterio de cierre del defecto**, sin que ello permita afirmar cuál suboperación original de Windows había fallado.

**Continuidad normal:** los jobs de Gestión de Bases de Datos (8 de octubre) y Sistemas Operativos II (6 de octubre) siguen `pending` y sin publicar/registrar. El Coordinador debe gestionarlos como trabajo académico ordinario, uno por uno, siempre con vista previa y aprobación independiente. La documentación anterior de pruebas pendientes se mantiene como **histórico de las etapas de desarrollo**, no como estado actual.
