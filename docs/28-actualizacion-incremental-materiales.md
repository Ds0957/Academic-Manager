# 28 — Canvas Study: actualización incremental y revalidación de fuentes

**Fecha:** 2026-10-08. **Estado:** implementado y validado con Canvas real y tests. El programador de Windows apunta al script actualizado; no se ha realizado una ejecución de extremo a extremo desde Task Scheduler **después de esta modificación**, aunque su sintaxis y configuración se verificaron.

## Alcance confirmado

Academic Manager conserva una capa de materiales de estudio verificables, separada de la gestión conversacional del futuro Coordinador. La rutina `canvas_sync/study.py` ya tenía inventario seguro por curso y eje, PDF institucional y texto local. **Cambio de este incremento:** no volver a descargar 30 PDF en cada comprobación cuando la evidencia permite reutilizarlos, pero tampoco suponer que una URL idéntica implica contenido remoto idéntico para siempre.

## Política incremental implementada

1. Autorización Canvas de usuario y consultas **GET-only** de cursos aprobados, lista de archivos y metadatos del HTML por eje. Cada HTML seleccionado se vuelve a leer para detectar modificaciones de contenido por SHA-256; el bearer solo se envía a la API Canvas. Las URLs de descarga Canvas HTML firmadas son temporales y no se persisten.
2. Si el HTML **mantiene el mismo ID Canvas y SHA**, la URL institucional del PDF no cambió, y todavía no venció el plazo de revalidación remota, reutilizar el PDF local solo después de comprobar SHA-256, bytes, nombre derivado del hash y estado de su texto. **No** abrir bibliotecas externas, vídeos ni interactivos como parte de esta tarea.
3. Se añade `text_sha256` a los metadatos de documentos con TXT. Los índices legacy sin hash de texto se migran comparando el TXT existente con una nueva extracción **desde el PDF local previamente validado**, sin GET remoto. Si la evidencia no coincide, se descarga nuevamente el PDF institucional para regenerar ambos archivos.
4. Una discrepancia de PDF, nombre, tamaño, texto o SHA evita la reutilización: se hace GET autorizado al dominio institucional sin bearer Canvas y se **repara atómicamente** el archivo basado en hash, incluso si el nombre ya existía pero su contenido estaba corrupto.
5. Un HTML distinto, documento nuevo, URL nueva o lectura cambiada obliga a obtener el PDF correspondiente. Un error en un eje o descarga **no reemplaza el índice completo del curso**; conserva el índice anterior y devuelve fallo. Las versiones históricas por contenido permanecen separadas.
6. Revalidación remota completa cada **7 días** como máximo por curso (o `--full-refresh` bajo solicitud). `refresh-state.json` registra el último refresco completo exitoso en UTC. Los accesos a caché **no adelantan** esa fecha. Los índices anteriores al cambio toman inicialmente su `captured_at_utc` real como fecha de la última descarga completa.
7. `--approved-only` junto con `--all-active` limita el inventario a cursos cuya matrícula ya consta en el registro de Academic Manager, evitando predescargar materiales de cursos nuevos todavía no confirmados. Esto no crea materias ni registra obligaciones.

**Límite explícito:** entre revalidaciones de siete días puede existir un cambio remoto del PDF bajo la misma URL y con HTML idéntico que el sistema aún no detecte. No afirmar actualización en tiempo real del contenido institucional.

## Operación

```powershell
scripts\canvas-sync.cmd sync-study --all-active --approved-only
scripts\canvas-sync.cmd sync-study --all-active --approved-only --full-refresh
```

El primer comando es incremental y valida las fuentes conocidas. El segundo obliga a consultar otra vez el PDF institucional completo. Ambos conservan la política de dominios autorizados y los permisos Canvas; **Inglés III sigue con `files=403`**, sin intentar eludirlo.

Estado local nuevo por curso:

```text
C:\AcademicManagerData\procesamiento\canvas-sync\study\<course_id>\
    index.json
    refresh-state.json
    ejeN\referente-<SHA>.pdf / .txt
    ejeN\lecturas\<n>\lectura-<SHA>.pdf / .txt
```

La tarea Windows existente `AcademicManager-CanvasDaily` sigue con disparadores a las **08:00 y 18:00**, sesión Windows autorizada, y el script `scripts/canvas-daily.ps1` ejecutará:

```text
Canvas daily --apply (actividades/calificaciones; protocolo canónico)
    ↓ solo si éxito
Canvas sync-study --all-active --approved-only (material incremental)
    ↓ solo si éxito
Academic Knowledge build (catálogo y búsqueda local)
    ↓
Registro local de resultado/código de salida
```

Así, el índice de conocimiento se reconstruye después de detectar materiales nuevos. Un fallo de Study no elimina materiales ya válidos ni finge que el índice se actualizó: queda registrado como error del trabajo. No se creó otro scheduler ni un servicio público.

## Validación auténtica

- Primera ejecución real con el nuevo código: cinco cursos consultados, cuatro índices de curso actualizados para incorporar hashes TXT/estado de revalidación; un curso restringido por 403. **Esta primera migración de metadatos no es una modificación del Excel.**
- Segunda ejecución real incremental: **23 solicitudes GET a Canvas API**, los mismos cinco cursos, **30 PDF institucionales reutilizados y 0 PDF descargados**. Cada uno de los cuatro índices de materias accesibles devolvió `sin cambios`; Inglés III conservó su 403. Las 16 páginas de eje sí se vuelven a revisar.
- Índice académico posterior: `academic_knowledge build` devolvió `changed=false`, 5 materias, **133 fuentes**, 34 con texto y 63 relaciones. `academic_access health` devolvió `ok` y hash de Excel coincidente con el publicado; `status=free`.
- Test sintético de cuatro escenarios críticos: 7 días y `--full-refresh` fuerzan consulta remota, PDF corrupto repara el archivo, HTML cambiado con fallo remoto deja intacto el índice anterior y curso sin aprobación no se descarga. También se validó migración legacy por extracción local de PDF a TXT. **Suite completa: 132 tests ejecutados, 2 omitidos, sin fallos**.
- `scripts/canvas-daily.ps1` analizado por parser PowerShell (**0 errores**); Task Scheduler `AcademicManager-CanvasDaily` sigue existente, `Ready`, apuntando al script correcto. **La ruta nueva Study → Knowledge aún no se ensayó desde el scheduler real después del cambio** y se debe comprobar en la siguiente ejecución o en una prueba operativa controlada.

**No se editó el Excel ni se publicó en Drive durante este incremento**. La primera utilización del flujo nuevo desde Task Scheduler y la detección en vivo de un PDF efectivamente cambiado en servidor quedan como validaciones posteriores; las transiciones se ensayaron sintéticamente.

## Límites y siguientes decisiones

- `files=403` de Inglés III requiere acceso legítimo, no soluciones para saltar permisos.
- Tres lecturas largas (331, 427 y 738 páginas) conservan PDF con estado `page_limit`; extraer capítulos específicos solo si un Project de materia los necesita.
- Multimedia, bibliotecas externas e interactivos siguen catalogados, no ejecutados ni descargados automáticamente.
- Aún no hay interfaz autónoma remota para Projects; la capa de lectura JSON del documento `27` requiere acceso al PC autorizado.
- **Etapa 2: núcleo mínimo de fuentes verificadas, búsqueda, asociación y actualización incremental disponible en este equipo.** La Etapa 3 configurará el Coordinador y Projects por materia. No duplicar funciones de tutor/gestor conversacional en Python.
