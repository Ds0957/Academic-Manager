# 04 — Base de datos

**Estado:** V2 mínima confirmada e implementada. Importaciones reales de GBD, SER, SO2 e Inglés III validadas; `03 - Clases` contiene dos clases reales registradas desde el 2026-10-07 (SER y GBD).

## Archivos y roles

Versión publicada/canónica compartida:

```text
Google Drive / Academic Manager / Academic Manager - Database.xlsx
```

Copia de trabajo del PC:

```text
C:\AcademicManagerData\database.xlsx
```

Estructura local:

```text
C:\AcademicManagerData\
├── database.xlsx
├── temporales\
├── procesamiento\
└── control\
    └── database-work-state.json
```

Google Drive es el punto de publicación e intercambio entre equipos. La publicación binaria del workbook se realiza por Google Drive API, no escribiendo el XLSX mediante la unidad virtual de Drive Desktop. Para el Drive personal se usa una identidad técnica con acceso limitado por los permisos compartidos de la carpeta `Academic Manager`.

## Principio

> El Excel sabe qué existe, a qué pertenece, dónde está y en qué estado se encuentra. Los archivos contienen el contenido completo.

No se guardan transcripciones completas ni análisis extensos dentro del Excel. Los IDs del LMS son metadata externa y no sustituyen los IDs internos de Academic Manager.

## Procedimiento operativo obligatorio

Toda modificación debe seguir este ciclo:

```text
leer estado compartido
→ verificar/adquirir lock
→ comprobar última versión publicada por SHA-256
→ checkpoint
→ editar una copia de trabajo válida
→ validar localmente
→ comprobar que Drive no cambió desde la versión base
→ publicar
→ re-descargar
→ revalidar bytes + estructura
→ alinear C:\AcademicManagerData\database.xlsx
→ actualizar estado compartido y liberar lock
```

Reglas:

1. No publicar si falla la validación previa.
2. No considerar correcta una publicación hasta re-descargarla y validarla.
3. No usar una unidad virtual de Google Drive Desktop (`G:`, `H:` u otra letra) como vía programática de publicación binaria del workbook.
4. Crear checkpoint antes de cambios estructurales, importaciones o actualizaciones masivas.
5. Mantener un solo escritor del workbook a la vez.
6. Un error del parser XLSX de Remote Desktop Commander no demuestra corrupción: validar con otro lector.
7. Antes de publicar, comprobar que la versión de Drive sigue correspondiendo al `base_sha256` del lock.

### Herramientas de edición/validación

Para XLSX se pueden usar herramientas que hayan demostrado generar archivos válidos, por ejemplo:

- Microsoft Excel vía COM;
- una librería XLSX validada;
- herramientas de spreadsheet del entorno de ejecución cuando operen sobre una copia verificada.

Remote Desktop Commander queda como acceso a filesystem, terminal y procesos; su parser/writer XLSX específico no forma parte del camino confiable del núcleo mientras continúe el error observado.

### Excepción para entornos aislados de ejecución

Si una herramienta no puede montar directamente `C:\AcademicManagerData` —por ejemplo un entorno aislado de ejecución de ChatGPT— puede trabajar sobre una **copia temporal** únicamente si antes se demuestra que sus bytes corresponden a la última versión publicada (`SHA-256 == published.sha256`). Esa copia temporal nunca es fuente de verdad.

Al terminar, se debe cumplir el mismo cierre: publicar, re-descargar, revalidar y dejar `C:\AcademicManagerData\database.xlsx` alineado con la versión publicada. El núcleo de Academic Manager no depende de que exista ese entorno aislado.

## Integridad y recuperación del workbook

El primer archivo `.xlsx` creado durante la construcción inicial no era un XLSX binariamente válido. Se conserva en Drive como:

```text
Academic Manager - Database - corrupted-2026-10-06.xlsx
```

La base fue reconstruida, actualizada a V2 y validada. Cualquier copia mostrada por Google Drive Desktop se considera una vista sincronizada, no el mecanismo de publicación ni el criterio de versión vigente.

## Hojas actuales

```text
00 - Dashboard
01 - Materias
02 - Actividades
03 - Clases
04 - Recursos
05 - Semestres
06 - Entregables
```

### `01 - Materias`

```text
materia_id
semestre
nombre
codigo
profesor
estado
prioridad
ultima_clase
ultima_actualizacion
ruta_carpeta
notas
lms
curso_externo_id
seccion_externa
periodo_externo
bloques
```

Materias actuales:

| materia_id | nombre | estado |
|---|---|---|
| MAT-GBD | Gestión de Bases de Datos | Activa |
| MAT-ING3 | Inglés III | Activa |
| MAT-SER | SER (BE-IT) | Activa |
| MAT-SO2 | Sistemas Operativos II | Activa |
| MAT-ST | Sistemas Transaccionales | Activa |

Desde las importaciones reales, `MAT-GBD`, `MAT-SER`, `MAT-SO2` y `MAT-ING3` contienen metadata confirmada de Canvas. **Nota multisemestre (2026-10-08):** `MAT-ST` todavía no tiene `curso_externo_id` en la hoja, aunque la relación con el curso Canvas `82892` se verificó previamente; el registro local de transición la conserva etiquetada como excepción, sin modificar el XLSX. En semestres futuros cada nueva cursada requiere una instancia `materia_id` independiente; el alta guiada local todavía no equivale a registrar una materia en esta hoja. Ver `docs/21-semestres-y-matricula.md`. Para Inglés III se registraron el docente, LMS Canvas, curso externo `91003`, sección observada `1000 - 100` y período externo `209960`.

**Extensión local de bloques (2026-10-08):** el registro `course-registry.json` modela una materia como perteneciente a uno o varios bloques del mismo semestre (`[1]`, `[2]`, `[1,2]`), sin duplicar `materia_id`. En particular `MAT-ING3` pertenece a ambos y las otras cuatro materias registradas pertenecen al bloque 2 de `2026-2`. **Estas relaciones ya están almacenadas en el Excel canónico** (2026-10-08): `bloques` en `01 - Materias` y `bloque_activo` en `05 - Semestres`; se añadieron sin modificar las columnas V2 previas ni el resto de las hojas. Publicación por Drive API y re-descarga verificadas. Ver `docs/22-publicacion-bloques-v21.md`. Ver `docs/21-semestres-y-matricula.md`.

### `02 - Actividades`

```text
actividad_id
materia_id
tipo
titulo
descripcion
organizacion
modalidad
fecha_apertura
fecha_entrega
fecha_cierre
estado_acceso
estado_entrega
estado_calificacion
prioridad
puntos_posibles
puntos_obtenidos
peso_porcentual
nota_institucional
lms
objeto_externo_tipo
objeto_externo_id
url_fuente
verificacion
procesada
notas
fecha_envio_real
referencias_externas
```

**Extensión V2.1 confirmada y publicada (2026-10-08):** `fecha_envio_real` (col. Z, envío efectivo local del alumno) se conserva por separado de `fecha_entrega` y `fecha_cierre`; `referencias_externas` (col. AA, JSON con `sistema`, `tipo`, `id`) conserva identificadores Canvas adicionales de quiz o discussion sin perder el assignment principal. El snapshot original mantiene el timestamp UTC y los datos enriquecidos de procedencia. En la primera migración se completaron **5 fechas y 4 referencias** y se verificó su lectura en Excel. Ver `docs/24-canvas-actividades-automatizacion-v1.md`.

Reglas confirmadas:

- apertura, entrega y cierre son fechas diferentes;
- acceso, entrega y calificación son estados diferentes;
- puntos posibles, puntos obtenidos, peso porcentual y nota institucional son conceptos distintos;
- ausencia de calificación permanece nula y no se convierte en cero;
- `organizacion` puede representar eje, unidad, semana o corte sin imponer una jerarquía universal;
- en el archivo XLSX, `peso_porcentual` usa formato de porcentaje de Excel: el valor físico `0.06` representa `6%` (no se escribe `6`, porque se mostraría como `600%`).

#### Primera importación real

El 2026-10-06 se importó y publicó el primer registro real:

```text
actividad_id: ACT-GBD-001
materia_id: MAT-GBD
tipo: examen
titulo: Actividad evaluativa - Eje 1
organizacion: Eje 1
fecha_apertura: 2026-09-28 00:00
fecha_entrega: 2026-10-12 23:59
fecha_cierre: 2026-10-12 23:59
estado_acceso: disponible
estado_entrega: pendiente
estado_calificacion: sin_calificar
puntos_posibles: 20
puntos_obtenidos: null
peso_porcentual: null
nota_institucional: null
lms: Canvas
objeto_externo_tipo: assignment
objeto_externo_id: 391671
url_fuente: https://areandina.instructure.com/courses/82862/quizzes/228076
procesada: false
```

La fuente confirma además `quiz_id = 228076`, 15 preguntas, 30 minutos y 1 intento. Como la V2 solo tiene un par `objeto_externo_tipo/objeto_externo_id`, el `assignment_id` quedó estructurado y el `quiz_id` se conservó en `notas` para no perderlo.

**Nuevo pendiente detectado por uso real:** una actividad de Canvas puede requerir conservar simultáneamente varios IDs externos relacionados (por ejemplo assignment + quiz, o assignment + discussion). La primera importación demuestra que un único par de campos externos puede ser insuficiente. No se cambia todavía el esquema; se contrastará con SO2 antes de decidir una solución mínima.

Los valores `disponible`, `pendiente` y `sin_calificar` fueron usados operativamente en este primer registro. El vocabulario controlado definitivo de estados se validará con más casos antes de convertirlo en una enumeración contractual.

#### Segunda importación real — SER acumulativo

El 2026-10-06 se importaron dos actividades relacionadas de SER para probar continuidad entre cortes:

```text
ACT-SER-002 — Actividad evaluativa - Eje 2
assignment_id: 404419
puntos_posibles: 25
apertura: 2026-10-12 00:00
entrega/cierre: 2026-10-26 23:59
estado_acceso: bloqueada_por_fecha
estado_entrega: pendiente
estado_calificacion: sin_calificar

ACT-SER-003 — Actividad evaluativa - Eje 3
assignment_id: 404422
puntos_posibles: 20
apertura: 2026-10-26 00:00
entrega/cierre: 2026-11-09 23:59
estado_acceso: bloqueada_por_fecha
estado_entrega: pendiente
estado_calificacion: sin_calificar
```

También se registraron dos entregables (`ENT-SER-002-01`, `ENT-SER-003-01`) que representan el Documento de Google requerido por ambos ejes, y dos recursos reales de apoyo (`REC-SER-001` Tarea2.pdf y `REC-SER-002` Tarea3.pdf).

La evidencia confirma que E3 amplía **el mismo Documento de Google iniciado en E2**. Sin embargo, todavía no existe una URL verificada del documento real del estudiante. Por integridad de datos no se creó un `Recurso` ficticio para ese documento. La relación muchos-a-muchos Actividad–Recurso sigue siendo un pendiente de diseño que se validará cuando exista el artefacto real o un caso equivalente con URL/ruta verificable.

Este caso amplía además el vocabulario observado con `estado_acceso = bloqueada_por_fecha`; no se convierte todavía en enumeración contractual universal.

#### Tercera importación real — SO2

El 2026-10-06 se importaron dos casos representativos: `ACT-SO2-002` (foro calificado, 20 puntos, assignment `392286`, discussion `279144`, cierre no verificado) y `ACT-SO2-004` (tarea «Mi PC Android», 20 puntos, assignment `392291`).

El foro confirma un segundo patrón real de identificadores externos múltiples: `assignment + discussion`, además de `assignment + quiz` en GBD. La V2 conserva temporalmente el ID adicional en `notas`; la solución mínima de persistencia queda pendiente como posible V2.1.

Para E4 se registraron `REC-SO2-001` (guía Canvas file `22401341`) y dos entregables obligatorios: `ENT-SO2-004-01` documento y `ENT-SO2-004-02` video con audio de 3 a 6 minutos mediante enlace de streaming. No son alternativas.

#### Cuarta importación real — Inglés III

El 2026-10-06 se importaron dos casos representativos del núcleo común:

```text
ACT-ING3-HW2 — HOMEWORK ACTIVITY - WEEK 4 - UNIT 2
assignment_id: 391963
puntos_posibles: 6
puntos_obtenidos: 5.7
peso_porcentual: 6%
apertura: 2026-08-03 00:00
fecha_entrega (due_at): null
cierre: 2026-09-27 23:59
estado_acceso: cerrada
estado_entrega: entregada
estado_calificacion: calificada

ACT-ING3-ORAL — ORAL EXAM
assignment_id: 391984
puntos_posibles: 20
peso_porcentual: null (ambigüedad de fuente preservada)
apertura: 2026-08-03 00:00
fecha_entrega (due_at): null
cierre Canvas: 2026-10-18 23:59
estado_acceso: disponible
estado_entrega: pendiente
estado_calificacion: sin_calificar
```

El homework conserva la entrega real observada `2026-09-26 12:05` en `notas`, porque V2 todavía no tiene un campo estructurado equivalente a `submitted_at`. Esto abre un pendiente real de V2.1: decidir si la fecha/hora efectiva de entrega merece un campo propio separado de apertura, vencimiento/cierre y estados.

El oral validó el uso de `alternativa_grupo`: `ENT-ING3-ORAL-VIDEO` y `ENT-ING3-ORAL-LIVE` pertenecen a `ING3-ORAL-MODALIDAD`. Son dos formas alternativas de satisfacer **una sola obligación**, no dos entregas obligatorias. Ambas conservan duración 1–3 minutos; la modalidad video tiene cierre verificable el 18 de octubre 23:59, mientras la modalidad en vivo conserva en notas la fecha docente del 28 de octubre porque no se publicó hora/límite específico.

El perfil específico de idiomas sigue fuera del núcleo V2; estos casos se representaron con las tablas comunes sin necesitar una estructura paralela.


### `03 - Clases`

```text
clase_id
materia_id
fecha
hora_inicio
hora_fin
titulo
tema_principal
estado_sesion
url_sesion
url_grabacion
estado_grabacion
transcripcion
analisis
procesada
fuente
verificacion
notas
```

Contiene dos clases reales registradas:

- `CLS-SER-20261001-01` — `MAT-SER`, `2026-10-01`, primera clase procesada.
- `CLS-GBD-20261001-01` — `MAT-GBD`, `2026-10-01`, segunda validación completa de Capture.

Ambas conservan el título real de la grabación, `estado_sesion = realizada`, `estado_grabacion = procesada` y `procesada = true`. `hora_inicio`, `hora_fin` y `tema_principal` permanecen nulos porque no existen valores verificados. `transcripcion` y `analisis` usan rutas relativas a la raíz de Academic Manager para no depender de la letra de unidad de cada PC. La convención confirmada de ID es `CLS-<codigo-materia>-<YYYYMMDD>-<secuencia-2d>`.

### `04 - Recursos`

```text
recurso_id
materia_id
tipo
titulo
descripcion
ruta
url
fecha
origen
lms
objeto_externo_id
verificacion
procesado
notas
```

Contiene actualmente tres recursos reales:

```text
REC-SER-001 — Tarea2.pdf — Guía Eje 2 — Canvas file 22615502
REC-SER-002 — Tarea3.pdf — Guía Eje 3 — Canvas file 22615479
REC-SO2-001 — Actividad evaluativa eje 4.pdf — Guía Eje 4 — Canvas file 22401341
```

No se creó como recurso el Documento de Google acumulativo porque todavía no existe una URL/ruta real verificada.

### `05 - Semestres`

```text
semestre_id
nombre
fecha_inicio
fecha_fin
estado
notas
bloque_activo
```

Registro actual: `2026-2`, `Semestre 2 - 2026`, estado `Activo`, bloque activo `2`. La pertenencia a bloques de las materias ya es persistente en la base publicada; ver `docs/22-publicacion-bloques-v21.md`.

### `06 - Entregables`

```text
entregable_id
actividad_id
tipo
modalidad
descripcion
formato
duracion_min
duracion_max
fecha_limite
ruta
url
estado
alternativa_grupo
notas
```

Se creó por evidencia real de múltiples entregables y modalidades alternativas. Ya contiene seis registros reales:

```text
ENT-SER-002-01 — ACT-SER-002 — Documento de Google / enlace compartido
ENT-SER-003-01 — ACT-SER-003 — el mismo documento ampliado
ENT-SO2-004-01 — ACT-SO2-004 — documento obligatorio
ENT-SO2-004-02 — ACT-SO2-004 — video obligatorio de 3 a 6 minutos
ENT-ING3-ORAL-VIDEO — ACT-ING3-ORAL — modalidad alternativa por video (1–3 min)
ENT-ING3-ORAL-LIVE — ACT-ING3-ORAL — modalidad alternativa en vivo (1–3 min)
```

Los seis registros conservan únicamente datos verificados. En SER no se conoce todavía la URL real del Documento de Google; en SO2 no existe todavía la URL del video y el formato del documento no fue especificado. En Inglés III, las dos filas del oral comparten `alternativa_grupo` y representan opciones excluyentes de una sola obligación; no se interpretan como dos pendientes acumulativos.

## Relaciones actuales

```text
SEMESTRES
   └── MATERIAS
          ├── ACTIVIDADES
          │      └── ENTREGABLES
          ├── CLASES
          └── RECURSOS
```

### Actividad–Recurso — pendiente

La importación de SER confirmó semánticamente que un mismo artefacto mutable puede pertenecer a varias actividades/cortes: E2 y E3 requieren el mismo Documento de Google en versiones sucesivas.

No se crea todavía `07 - Actividad-Recurso` porque el documento real del estudiante aún no tiene una URL/ruta verificada en Academic Manager. La necesidad conceptual está confirmada, pero la forma mínima de persistir la relación se decidirá cuando exista el artefacto real o al contrastar otro caso equivalente.

### Perfil de idiomas — pendiente

Idioma, nivel, habilidad, gramática y vocabulario no forman parte obligatoria de V2. Inglés III se probará primero con el núcleo común.

## Validación funcional de V2

Estado actual:

- **GBD E1:** importado, publicado, re-descargado y validado correctamente.
- **SER E2/E3:** importados, con recursos guía y entregables acumulativos; publicado y revalidado correctamente.
- **SO2 E2/E4:** importados; foro + documento/video publicados y revalidados correctamente.
- **Inglés III:** homework calificado + oral alternativo importados, publicados y revalidados correctamente.
- **Clases:** validadas `CLS-SER-20261001-01` y `CLS-GBD-20261001-01`, con transcripciones y análisis publicados. Segunda clase registrada mediante Capture y protocolo completo del workbook.

La V2 no se considera completamente validada hasta pasar esos contrastes. Sistemas Transaccionales queda como validación adicional y no bloquea el trabajo actual.
