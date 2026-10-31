# 25 — Etapa 2 V1: índice de conocimiento académico por materia

**Fecha:** 2026-10-08. **Estado:** primer incremento implementado y validado con las fuentes auténticas disponibles en PC Diego. **No representa todavía toda la Etapa 2:** faltan consolidación transversal entre clases y fuentes autorizadas de Inglés III. Las fuentes canónicas permanecen intactas.

## Objetivo y contrato confirmado

Academic Manager mantiene **fuentes originales** fuera del chat: clases Capture publicadas en Drive, XLSX canónico, TXT/PDF de Canvas Study en runtime local, metadatos verificados y anotaciones de recursos. El nuevo módulo `academic_knowledge/` crea un catálogo derivado y una base de búsqueda SQLite FTS5 **local y reconstruible**.

- `scripts/knowledge.cmd build` reconstruye el índice si cambió el contenido verificado; no genera archivos académicos nuevos en Drive ni modifica Excel ni Canvas.
- `scripts/knowledge.cmd status` muestra materias, actividades, documentos disponibles y cobertura.
- `scripts/knowledge.cmd search --materia MAT-SER --query "felicidad consumismo"` busca **fragmentos textuales reales**; presenta texto literal en contexto, título, tipo, materia, eje y ruta de procedencia. No fabrica resúmenes ni atribuye a una fuente hechos que no están en ella.
- `scripts/knowledge.cmd activity --actividad ACT-SO2-004` informa del estado de la obligación y de su contexto académico, distinguiendo asociación explícita de relación por eje. Es una consulta contextual, no una decisión automática sobre cumplimiento o requisitos.
- `scripts/knowledge.cmd sources --materia MAT-GBD` lista la trazabilidad exacta de las fuentes, incluyendo fuentes **sin texto descargado**.

### Fuente de verdad y privacidad

```text
Google Drive (clases reales)  ─┐
Canvas Study (PDF, TXT, índice) ├── lectura/validación ──> academic_knowledge
Excel V2.1 (materias, actividades, clases, recursos) ─┘
                                                │
                                                ▼
C:\AcademicManagerData\procesamiento\academic-knowledge\
    index.json          # procedencia, fuentes, actividades y relaciones
    search.sqlite3      # índice de pasajes FTS5 reconstruible
```

El índice **no se sube automáticamente a GitHub ni a Google Drive**. No contiene credenciales ni audiovisual. Las fuentes no se copian de forma permanente como nuevas versiones; el texto de búsqueda es caché local derivada. El estado estructurado del estudiante sigue en XLSX, las transcripciones/análisis en Drive y los TXT/PDF con hashes en runtime.

Antes de indexar, el módulo verifica SHA del XLSX publicado y la matrícula Canvas; inspecciona `03 - Clases`, `04 - Recursos`, `02 - Actividades` y `01 - Materias` en Microsoft Excel COM ReadOnly; comprueba el hash de cada índice `sync-study` y de sus PDF y textos. Rechaza rutas de clase fuera del semestre/carpeta esperada, traversal, archivos redirigidos o texto excesivamente grande. Si no se puede leer un artefacto de clase existente, **detiene la reconstrucción sin borrar las versiones anteriores**. Las rutas ausentes/no autorizadas de Inglés III no se inventan.

## Relaciones académicas

Hay dos tipos que **no deben confundirse**:

- **`contexto_del_mismo_eje`**: actividad y referente, lectura o guía comparten una materia y el mismo número de eje comprobable. Ayuda a encontrar qué estudiar; **no demuestra** que el archivo sea consigna, rúbrica ni entregable.
- **`guia_asociada_en_excel`**: referencia exacta a un ID interno de actividad presente en las notas verificadas de un recurso de `04 - Recursos`. Ejemplo comprobado: `REC-SO2-001` → `ACT-SO2-004`. Conecta el recurso de apoyo existente sin crear una tabla especulativa.
- Los análisis de clases se asocian a la materia, pero **no se atribuyen automáticamente** a una actividad ni a un eje. Su contenido se recupera con búsqueda textual por materia.

La diferencia entre material conceptual y obligación evaluativa permanece: las 31 actividades son registros de Excel; los referentes/lecturas son fuentes de estudio y las guías son fuentes de apoyo verificadas. No se da por obligatorio el material complementario de Canvas.

## Material de estudio de Capture (2026-10-25)

Capture puede guardar videos de apoyo de YouTube o archivos locales como **material de estudio** (`--tipo material --subject-id MAT-...`, ver `05-capture.md`). Se publican en `<semestre>/<materia>/Material/<título>/` y **no** se registran en `03 - Clases` ni reciben ID `CLS-`.

- `build` recorre `<semestre>/<nombre de la materia>/Material` de cada materia de `01 - Materias` y añade cada carpeta publicada como fuente `material:<job_id>` de tipo `material`, con `transcript.md` como texto, el título del video o archivo como título y la URL de YouTube o la ruta local original como `origen`.
- Solo cuentan carpetas con `metadata.json` de Capture; se omiten archivos sueltos y publicaciones incompletas (con `.capture-owner.json`). Si `metadata.json` indica otra materia, otro tipo de contenido o un `job_id` inválido, o no se puede leer, la reconstrucción **se detiene** sin reemplazar el índice anterior. El error nombra la carpeta (`<semestre>/<materia>/Material/<título>`) para corregirla. Los nombres de carpeta se buscan igual que los crea Capture: los caracteres no válidos en Windows (`<>:"/\|?*`) pasan a `_`.
- `academic-read search` y `academic-read source` encuentran y leen este material igual que las clases; el material no se asocia a actividades ni a ejes.
- No hay tabla ni columna nueva: la carpeta publicada en Drive es la fuente.

## Prueba real del 2026-10-08

```text
materias                  5
actividades (Excel)      31
fuentes catalogadas     133
fuentes con texto        34
pasajes FTS5           1623
relaciones contextuales  62
relaciones explícitas     1
```

En el índice existen los dos análisis y transcripciones reales (SER y GBD, 1 de octubre), 16 referentes institucionales, 11 lecturas con texto, 3 guías PDF identificadas por file ID exacto y las fuentes externas/multimedia catalogadas sin afirmar que están leídas. La consulta sobre SER recupera del **`analysis.md` auténtico** el contenido sobre consumismo/felicidad; la consulta sobre GBD recupera fragmentos del referente institucional de Seguridad de Bases de Datos. La consulta a `ACT-SO2-004` muestra su guía explícita y materiales del Eje 4 como contexto, con advertencia de alcance.

**Idempotencia:** repetir `build` sin cambios devuelve `changed=false`, el mismo hash e índice, sin añadir pasajes duplicados. La reconstrucción compara hashes de documentos y del workbook y escribe primero un SQLite temporal que reemplaza el anterior antes de publicar el manifiesto final. La batería general `scripts/dev-check.ps1` terminó con **117 pruebas ejecutadas, 2 omitidas**. Las cinco pruebas nuevas sintéticas incluyen cambios reales de texto, búsqueda tolerante a tildes, rechazo de traversal, integridad PDF y falta de archivos de clase.

## Operación cotidiana

La tarea Windows `AcademicManager-CanvasDaily` (08:00 y 18:00, Colombia), que ya sincroniza Canvas, se amplió para ejecutar **`python -m academic_knowledge build` inmediatamente después de una sincronización exitosa**. Una falla del índice devuelve error y se registra en los logs existentes; los datos canónicos no se alteran. No hace falta ChatGPT abierto. Este paso NO descarga nuevamente 30 PDF por ejecución; utiliza sus copias oficiales verificadas. `sync-study` debe ejecutarse cuando se requiera actualizar referentes y lecturas y no se ha añadido todavía al programador con descargas masivas automáticas.

**Validación de Windows:** una primera activación manual de la tarea fue interrumpida externamente (resultado `3221225786`) sin generar log nuevo; se comprobó que ejecutar el script completo directamente terminó con código `0`. Al repetir la tarea desde el Programador, terminó con **`LastTaskResult=0`**, registro actualizado a **2026-10-08 11:38:34** y sección `ACADEMIC_KNOWLEDGE` con `changed=false`, 133 fuentes, 34 con texto y 63 relaciones. Se mantiene habilitada la tarea a las 08:00 y 18:00; la validación se refiere a esta PC/sesión, no a ejecución sin sesión de usuario.

## Límites y siguientes incrementos de la Etapa 2

1. **Inglés III:** Canvas responde 403 al listado de archivos; faltan materiales de estudio autorizados. La materia tiene nueve actividades, pero ningún documento con texto en esta primera base de conocimiento. No se eludirán permisos.
2. **Tres lecturas extensas:** PDF disponible y verificable, pero sin TXT por límite de páginas; indizar después solo capítulos/páginas pertinentes, no extraer cientos de páginas sin utilidad demostrada.
3. **Alcance redefinido después de este hito (2026-10-08):** la síntesis conceptual, la interpretación académica entre clases y el plan de estudio conversacional corresponden prioritariamente al futuro Project Coordinador y a Projects de materia **apoyados por fuentes verificadas**. El núcleo debe facilitar recuperación, evidencia y vínculos, pero no desarrollar por anticipado un tutor/coordinador paralelo. Ver `docs/26-roles-coordinador-desarrollo-materias.md`.
4. **Implementado posteriormente (2026-10-08):** el sondeo incremental de `sync-study` ya verifica archivos institucionales y reutiliza caché local por SHA, con revalidación remota cada siete días. La tarea Windows ahora encadena sincronización de actividades → `sync-study` incremental → índice; la nueva cadena todavía necesita una ejecución real completa desde el Programador para validación operativa. Ver `28-actualizacion-incremental-materiales.md`.
5. **Relaciones específicas adicionales** entre guías, rúbricas y actividades solo con evidencia real; no crear una tabla Actividad–Recurso general prematuramente.

## Continuidad

**Incremento posterior (2026-10-08):** el módulo `academic_access/` ofrece ahora una interfaz de consultas JSON v1 de solo lectura sobre el índice y el Excel canónico. Está probada localmente y será la frontera para los futuros Projects, en lugar de exigirles acceso indiscriminado al modelo interno. Ver `27-contrato-consulta-academica.md`.

Este es el **primer incremento operativo de la Etapa 2**, cuyo objetivo técnico queda acotado a fuentes accesibles, trazables y consultables. La futura Etapa 3 configura **un Project Coordinador distinto de este Project de Desarrollo** y los Projects de materia; estos consultarán el índice y citarán fuentes. El Coordinador elevará defectos y features mediante GitHub Issues, sin depender de que el núcleo requiera un plan de ChatGPT. Ver `26-roles-coordinador-desarrollo-materias.md`.
