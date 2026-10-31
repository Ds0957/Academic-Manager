# 07 — Estado actual

**Fecha de referencia:** 2026-10-12

Este documento registra lo que existe de forma real en Academic Manager.

## Nombre y repositorio

El proyecto se llama **Academic Manager**.

Repositorio principal:

```text
Ds0957/Academic-Manager
```

`/docs` es la memoria persistente del desarrollo entre chats.

Workspace de desarrollo estable del PC actual:

```text
C:\AcademicManagerDev\Academic-Manager
```

Contiene un clon de `Ds0957/Academic-Manager` alineado con `origin/main`. Código y documentación se editan y prueban aquí; `C:\AcademicManagerData` queda reservado para runtime/datos.

## Almacenamiento

Publicación/sincronización compartida:

```text
Google Drive / Academic Manager
```

Ruta visible mediante Google Drive Desktop en el PC actual (`Diego`):

```text
G:\Mi unidad\03 - Universidad\Academic Manager
```

La letra de unidad no forma parte del contrato del sistema y puede variar entre equipos.

Área local de trabajo del PC actual:

```text
C:\AcademicManagerData\
├── database.xlsx
├── temporales\
├── procesamiento\
└── control\
    └── database-work-state.json
```

Remote Desktop Commander está restringido a `C:\AcademicManagerDev`, `C:\AcademicManagerData` y a la raíz de Academic Manager autorizada en el PC actual (`G:`). No tiene acceso general al resto del disco `C:`.

El parser XLSX específico de RDC sigue fallando incluso sobre `C:`; RDC se usa para filesystem, terminal y procesos. Excel nativo y herramientas XLSX validadas se usan para inspección/edición estructural.

## Coordinación multi-equipo

Existe en Google Drive:

```text
database-work-state.json
```

Implementa un modelo de un solo escritor a la vez mediante `status`, `lock` y `published.sha256`.

El 2026-10-06 se validó por primera vez el ciclo operativo completo **en un solo PC**:

```text
free
→ comprobar hash publicado
→ checkpoint
→ editing (lock ApoyoTIC)
→ modificar/importar
→ validar
→ comprobar versión base de Drive
→ publicar
→ re-descargar
→ verificar hash + apertura Excel
→ alinear copia local
→ free
```

La prueba entre dos computadores distintos sigue pendiente.

Estado compartido después del registro de la segunda clase GBD:

```text
status = free
lock = null
published.sha256 = 9b4686373de57881b36a5712b70df263f98272a5392508c662fd2a21189962c8
published.size_bytes = 88059
published.drive_modified_time = 2026-10-07T23:59:23.111Z
```

La publicación del workbook se realizó por Google Drive API, se re-descargó, se comprobó por SHA-256, se abrió con Microsoft Excel y se realineó con `C:\AcademicManagerData\database.xlsx`.

## Semestre y materias

Semestre activo:

```text
2026-2
```

Materias actuales:

```text
Gestión de Bases de Datos
Inglés III
SER (BE-IT)
Sistemas Operativos II
Sistemas Transaccionales
```

La estructura interna estándar completa por materia sigue sin definirse; se mantiene el criterio incremental. Ya existe una primera decisión implementada para clases: cada materia puede contener `Clases/<nombre de la grabación>/` con `metadata.json`, `transcript.md`, `transcript.json` y `analysis.md`. En SER ya se creó la carpeta `2026-2/SER (BE-IT)/Clases`.

## Base de datos

Archivo publicado/canónico:

```text
Academic Manager - Database.xlsx
```

Copia local alineada actualmente:

```text
C:\AcademicManagerData\database.xlsx
```

La V2 mínima está implementada con 7 hojas:

```text
00 - Dashboard
01 - Materias
02 - Actividades
03 - Clases
04 - Recursos
05 - Semestres
06 - Entregables
```

### Datos reales cargados

`01 - Materias` contiene las cinco materias. MAT-GBD, MAT-SER, MAT-SO2 y MAT-ING3 ya incluyen profesor y metadata confirmada de Canvas. Para Inglés III: docente, curso `91003`, sección observada `1000 - 100`, período `209960`.

`05 - Semestres` contiene el semestre `2026-2` activo.

`02 - Actividades` contiene actualmente siete registros reales:

```text
ACT-GBD-001 — GBD Eje 1 — examen — 20 puntos
ACT-SER-002 — SER Eje 2 — tarea grupal — 25 puntos
ACT-SER-003 — SER Eje 3 — tarea grupal — 20 puntos
ACT-SO2-002 — SO2 Eje 2 — foro calificado — 20 puntos
ACT-SO2-004 — SO2 Eje 4 — tarea con dos entregables — 20 puntos
ACT-ING3-HW2 — Inglés III semana 4 / unidad 2 — homework — 5.7/6 — peso 6%
ACT-ING3-ORAL — Inglés III — examen oral con modalidades alternativas — 20 puntos
```

Los registros se importaron desde evidencia documentada sin inferir valores no verificados. Cuando la fuente publica puntaje o peso —como en Inglés III HW2— se conservan estructuradamente; los campos desconocidos permanecen nulos.

`04 - Recursos` contiene `REC-SER-001`, `REC-SER-002` y `REC-SO2-001` (guía Eje 4, Canvas file `22401341`).

`06 - Entregables` contiene los dos entregables acumulativos de SER, `ENT-SO2-004-01` / `ENT-SO2-004-02` para SO2 E4 y `ENT-ING3-ORAL-VIDEO` / `ENT-ING3-ORAL-LIVE` para el oral. En Inglés III ambas filas comparten `alternativa_grupo = ING3-ORAL-MODALIDAD`, por lo que son opciones de una sola obligación.

`03 - Clases` contiene dos registros reales: `CLS-SER-20261001-01` (`MAT-SER`) y `CLS-GBD-20261001-01` (`MAT-GBD`), ambos del `2026-10-01`. Capture V0 procesó las grabaciones, publicó los cuatro artefactos por clase y registró rutas relativas para transcripción y análisis. Los campos sin evidencia suficiente (`hora_inicio`, `hora_fin`, `tema_principal`) permanecen nulos.

### Primera importación — validación técnica

La primera importación se realizó siguiendo el protocolo documentado:

- hash inicial de la versión publicada: `6cba833c9cb19fd436526fc00991f23a844a1b81ae47861b2968555a4ecf382d`;
- checkpoint creado en `C:\AcademicManagerData\temporales`;
- lock adquirido por `ApoyoTIC`;
- workbook modificado sobre una copia verificada del mismo hash;
- XLSX exportado y validado estructuralmente;
- antes de publicar se comprobó que Drive seguía en la versión base;
- archivo publicado conservando el mismo ID de Drive;
- nueva versión re-descargada y SHA-256 comprobado;
- Microsoft Excel abrió la copia publicada y confirmó las 7 hojas y `ACT-GBD-001`;
- `C:\AcademicManagerData\database.xlsx` quedó alineado con la versión publicada;
- lock liberado.

### Segunda importación — SER acumulativo

El 2026-10-06 se importaron `ACT-SER-002` y `ACT-SER-003`, junto con dos recursos guía y dos entregables. El ciclo técnico completo volvió a pasar: hash base, checkpoint, lock, edición sobre copia válida, validación, comprobación de versión base, publicación, re-descarga, apertura con Excel, alineación de `C:\AcademicManagerData\database.xlsx` y liberación del lock.

La evidencia confirmó que E3 amplía el mismo Documento de Google de E2. Como ese documento real del estudiante aún no tiene URL/ruta verificada, Academic Manager conserva la obligación en `06 - Entregables` pero no inventa un registro en `04 - Recursos`. La relación Actividad–Recurso sigue pendiente de implementación hasta disponer del artefacto real o de otro caso equivalente verificable.

Además, SER introduce el estado observado `bloqueada_por_fecha`, útil para contrastar el vocabulario de estados de V2.

### Tercera importación — SO2 foro + entrega múltiple

El 2026-10-06 se importaron `ACT-SO2-002` y `ACT-SO2-004`, junto con `REC-SO2-001` y dos entregables de E4. El foro conserva `assignment_id = 392286` estructurado y `discussion_id = 279144` en notas; `fecha_cierre` permanece nula porque no fue verificada. E4 conserva documento + video como dos entregables obligatorios.

El ciclo técnico volvió a pasar completo. La versión publicada resultante tiene SHA-256 `bfcadf6f5d511c1141ed2bfefae495bc520ca805e28a0bf53edff71f2f69d2a6`, tamaño `70158` bytes, fue re-descargada y Microsoft Excel confirmó las 7 hojas y los nuevos registros antes de liberar el lock.

SO2 aporta el segundo caso real de múltiples IDs externos por actividad (`assignment + discussion`). La necesidad de admitir varias referencias externas queda confirmada; la forma exacta de almacenarlas sigue pendiente como V2.1.

### Cuarta importación — Inglés III homework + oral alternativo

El 2026-10-06 se importaron `ACT-ING3-HW2` y `ACT-ING3-ORAL`. El homework conserva `puntos_posibles = 6`, `puntos_obtenidos = 5.7`, peso 6%, estados `cerrada / entregada / calificada` y el timestamp real de envío `2026-09-26 12:05` en notas. `fecha_entrega` permanece nula porque Canvas no publicó `due_at`.

El oral quedó como una sola actividad con dos entregables alternativos (`ENT-ING3-ORAL-VIDEO` y `ENT-ING3-ORAL-LIVE`) unidos por `alternativa_grupo = ING3-ORAL-MODALIDAD`. Esto valida en uso real que el modelo de Entregables puede representar alternativas sin duplicar la obligación.

La publicación completó nuevamente hash base, checkpoint, lock, edición/validación con `artifact_tool`, comprobación de que Drive no cambió, publicación, re-descarga, revalidación estructural, apertura con Microsoft Excel, alineación de `C:\AcademicManagerData\database.xlsx` y liberación. El hash publicado resultante es `25dd3b77ba6cb683ad786cf03b4ea7b83dd2a856f839869703ac772e91a15c4c`, tamaño `71595` bytes.

Nuevo pendiente detectado por uso real: V2 no tiene un campo estructurado para la fecha/hora efectiva de envío (`submitted_at`), aunque sí separa apertura, entrega/vencimiento y cierre. Por ahora el dato se conserva en notas; se decidirá si requiere un ajuste mínimo V2.1.


### Fricción detectada por uso real

GBD E1 posee `assignment_id + quiz_id` y SO2 E2 posee `assignment_id + discussion_id`. V2 solo tiene un par estructurado `objeto_externo_tipo/objeto_externo_id`.

Para no perder información, el `assignment_id` queda estructurado y el ID específico adicional se conserva temporalmente en notas. La necesidad de varias referencias externas ya está confirmada por dos tipos diferentes de objeto Canvas.

Sigue pendiente decidir la solución mínima de V2.1: campos adicionales específicos o una relación general de referencias externas.

## Recuperación del workbook inicial

El primer `.xlsx` creado durante la construcción resultó inválido y se conserva en Drive como:

```text
Academic Manager - Database - corrupted-2026-10-06.xlsx
```

La base vigente fue reconstruida y validada. La copia antigua que Google Drive Desktop todavía pueda mostrar en `H:` no es fuente de escritura ni criterio de versión.

## Exploraciones académicas disponibles

Se realizaron exploraciones reales de:

- Gestión de Bases de Datos — `docs/09-exploracion-canvas.md`;
- SER (BE-IT) — `docs/10-exploracion-canvas-ser.md`;
- Sistemas Operativos II — `docs/11-exploracion-canvas-so2.md`;
- Inglés III — `docs/12-exploracion-canvas-ingles3.md`.

Los inventarios JSON correspondientes están en `docs/anexos`.

GBD E1, SER E2/E3, SO2 E2/E4 e Inglés III HW2/oral ya pasaron de evidencia explorada a registros reales en la base.

## Capture / procesamiento

Capture V0 ya está implementado en el repositorio bajo `capture/` y fue validado con dos grabaciones reales completas, SER y Gestión de Bases de Datos, ambas del 1 de octubre de 2026.

Resultado real:
- fuente: `SER  - 2026/10/01 19:50 GMT-05:00 - Recording`;
- 777029390 bytes;
- duración ~57:39;
- WhisperX `large-v3` sobre CUDA/float16;
- 111 segmentos;
- `metadata.json`, `transcript.json`, `transcript.md` y `analysis.md` generados y validados;
- copia audiovisual y WAV temporal eliminados después de validar;
- artefactos publicados en:
  `2026-2/SER (BE-IT)/Clases/SER  - 2026_10_01 19_50 GMT-05_00 - Recording/`.

La primera revisión global de la transcripción confirmó calidad suficiente para análisis académico, con la cautela de contrastar audio para citas literales/nombres propios.

El análisis persistente fue rehecho con una estructura orientada al estudiante: lo esencial, lo que debe saber, lo que debe recordar, acciones, evaluaciones, decisiones/cambios, ejemplos, recursos, dudas, próxima clase, importancia secundaria, mapa temporal y fiabilidad. Esta estructura queda adoptada como base para análisis de clases.

Implementado y validado:
- primera clase SER registrada como `CLS-SER-20261001-01`;
- segunda clase GBD registrada como `CLS-GBD-20261001-01`, job `d61e6976c98749e48d964b2115fef3bc`, duración 43:36, 90 segmentos, análisis de 13 secciones;
- publicación segura del workbook por Drive API, último hash `9b4686373de57881b36a5712b70df263f98272a5392508c662fd2a21189962c8`;
- registro idempotente desde Capture sobre un job existente;
- validación integrada de la segunda clase: descarga independiente desde Drive, igualdad SHA-256, apertura Excel con nueva clase, artefactos y estado `free` sin lock;
- el job GBD terminó localmente a pesar de una desconexión temporal del canal MCP (HTTP 525 Cloudflare/Supabase);
- desde el 2026-10-07, los nuevos jobs registran etapas locales en `events.jsonl` y `--status latest` permite consultarlas sin reiniciar tareas;
- `atomic_json` reintenta denegaciones transitorias de reemplazo en Windows sin sacrificar la atomicidad; validado frente a un bloqueo real inducido sobre un archivo de prueba, sin modificar datos académicos.

Pendiente:
- decidir la experiencia de usuario final de Capture;
- implementado historial local `events.jsonl`, consulta de estado `python -m capture --status latest` y reintentos atómicos acotados ante `WinError 5`; quedan pendientes heartbeat, visibilidad integral de consola y determinar la causa exacta de cualquier bloqueo persistente;
- validar coordinación real entre dos PCs.

## Próximo hito recomendado

**Ruta V1 acordada:** comenzar por integrar Canvas Sync con V2; después, organización del conocimiento de materias, asistentes conversacionales, Dashboard, simplificación de Capture y validación integral. Las operaciones académicas frecuentes deben hacerse mediante rutinas idempotentes que solo eleven excepciones; no repetir revisiones ya resueltas.

**Siguiente incremento inmediato de Etapa 1:** resolver las 24 candidaturas/excepciones con un mecanismo de decisión persistente verificable, normalizar fechas, calificaciones y estados y verificar contra la versión publicada remota antes de habilitar la incorporación segura a Excel. Los ajustes mínimos V2.1 (referencias múltiples, `submitted_at`) se deciden por estos casos reales. Toda futura escritura mantiene el ciclo lock, checkpoint, validación, publicación y revalidación.

## Conectividad remota del PC Diego

El 2026-10-07 se verificó el dispositivo remoto `Diego` conectado y con la sesión local previamente autorizada. Se creó la tarea de Windows `AcademicManager-DesktopCommanderRemote` (al iniciar sesión, `LogonType=Interactive`, `RunLevel=Limited`, una instancia) para arrancar automáticamente el lanzador `scripts/start-remote-agent.cmd` sin utilizar `npx.ps1`. La tarea quedó registrada y habilitada. La prueba de recuperación real tras cerrar sesión/reiniciar **aún no se ha realizado**. Durante Capture GBD, el agente perdió la conexión por un error externo HTTP 525 y problemas de canal/presencia; tras recuperar el acceso se confirmó que el proceso local había finalizado correctamente.

## Canvas Sync — integración API

El 2026-10-07 se probó manualmente el acceso autorizado por API Canvas Areandina con token personal: `GET /api/v1/users/self/profile`, curso GBD `82862` y su lista de actividades; Canvas devolvió **5 actividades en la primera página**. Esta evidencia proviene de la prueba manual, no de una ejecución del nuevo sincronizador.

**Implementado y validado localmente con tests:** `canvas_sync/` y `scripts/canvas-sync.cmd` con `login`, `renew`, `status`, `courses`, `sync`. Token cifrado localmente con DPAPI Windows bajo `C:\AcademicManagerData\control\canvas-sync` (solo al autorizar desde una terminal local); vencimiento máximo de 90 días, aviso desde 15 días, reemplazo tras comprobar la misma cuenta; extracción GET paginada, JSON locales de cursos/actividades/entregas visibles, detección de cambios por ID. La suite íntegra de tests pasa.

**Validación auténtica completada el 2026-10-07:** el usuario realizó `login` local, Canvas autorizó la cuenta y se guardó el token cifrado por DPAPI (caducidad `2027-01-04 23:30 -0500`). El sistema ejecutó `status` (GET de perfil, código 0) y `sync` del curso GBD `82862` (código 0), sin revelar la credencial. Se recuperaron **5 registros con IDs únicos**: los Ejes 1–4 previamente documentados (`391671`, `391683`, `391687`, `391690`) y `Nota final [P1]` (`391692`, 100 puntos), que **no debe tratarse como actividad pendiente ordinaria sin clasificarla**. Fechas de apertura/vencimiento/cierre: presentes en 4/5; `quiz_id` de E1: `228076`; los cinco `submission.workflow_state` informaron `unsubmitted` y no se observaron envíos/calificaciones efectivos. El primer JSON tiene SHA-256 consistente y un snapshot; una segunda ejecución devolvió «sin cambios», sin crear otro snapshot. El workbook compartido siguió `free`, `lock=null`, sin modificación mediante Canvas Sync.

**Pendiente:** probar `courses`, revisar otros cursos, casos con entregas/calificaciones y resolver el mapeo seguro hacia V2.1; no existen avisos automáticos proactivos ni sincronización al Excel. Evidencia y límites: `docs/15-canvas-sync.md`.

### Auditoría de cobertura de Canvas API — cinco materias (2026-10-07)

**Exploración real confirmada**, sin modificar código de Sync ni el workbook: se consultaron los cinco cursos activos `82862` (GBD), `82892` (Sistemas Transaccionales), `91003` (Inglés III), `91004` (SO2) y `91005` (SER). Se recuperaron **38 registros de assignments**, **51 metadatos de archivos** accesibles (Inglés III devuelve 403 en archivos), **18 temas de discusión**, **18 quizzes**, **6 módulos / 42 ítems**, **14 rúbricas embebidas en assignments** y **9 assignments con descripción HTML**. La información contiene categorías no evaluativas (como `Nota final [P1]`), por lo que no se debe importar automáticamente cada registro como obligación.

En Inglés III **5 assignments** devuelven `graded`, `score` y `submitted_at`; el planificador del usuario devuelve **24 entradas** en el intervalo 2026-09-01 a 2026-12-20. Primera batería instrumentada: **82 GET**, HTTP 200=75, 403=6, 404=1, 429=0; coste agregado reportado por cabecera ≈18,55 unidades y cuota restante observada 700,0 (cuota dinámica, no un límite mensual). Se guardaron **solo informes de cobertura locales** en `C:\AcademicManagerData\procesamiento\canvas-sync\audit-coverage*.json`. No se descargaron binarios, leyeron respuestas de exámenes ni modificó la base. Resultados, alcance y pendientes en `docs/16-cobertura-canvas-api.md`.

**Pendiente de diseño/implementación:** proponer Sync V0.1 con campos enriquecidos, clasificador de asignaciones, metadatos de recursos, planner y calificaciones, sin mutar Canvas ni Excel antes de decidir V2.1.

### Canvas Sync V0.1 — extracción persistente enriquecida (2026-10-07)

**Implementado y probado en uso real.** Se añadió `canvas_sync/enriched.py` y la acción `sync-rich --all-active`. Utiliza **únicamente GET autenticados** a Canvas, sin descargar archivos binarios ni leer preguntas/respuestas de exámenes, sin modificar contenidos de Canvas mediante peticiones de escritura ni editar/publicar el workbook. Conserva capturas JSON locales separadas bajo `C:\AcademicManagerData\procesamiento\canvas-sync\v01\<course_id>\latest.json` y `v01\user\latest.json`, con SHA-256 y snapshots no duplicados. Mantiene V0 intacto.

**Primera ejecución real:** 5 cursos, 66 solicitudes GET, 38 registros de assignments, 14 rúbricas integradas, 51 archivos indexados (metadata), 18 discusiones, 18 quizzes, 6 módulos y 42 ítems, con 5 calificaciones y 5 timestamps `submitted_at` en Inglés III; resumen personal con 5 inscripciones, 5 tareas todo, 5 próximos eventos y 25 elementos del planner en ventana móvil. Inglés III: `files=forbidden`, `pages=not_found`; estados registrados, no convertidos en «ausentes». Los seis JSON superaron verificación SHA-256; una segunda sincronización devolvió **sin cambios en los cinco cursos**. Clasificación de actividades marcada explícitamente como **provisional**. No se importaron datos a Excel. Los GET pueden producir trazas de acceso o indicadores de lectura propios de Canvas; el sistema no ejecutó operaciones de modificación.

**Pendientes:** propuesta controlada para PDFs y materiales externos, clasificación y reconciliación de actividades/recursos en V2.1, avisos de token y políticas de reintento. Contrato y limitaciones en `docs/17-canvas-sync-enriquecido.md`.

### Canvas Sync — recuperación selectiva de PDF (2026-10-07)

**Implementado y validado en uso real:** `canvas_sync/resources.py`, comando `pdf --course-id ID --file-id ID` y `requirements-canvas.txt` (parser `pypdf==5.9.0`). El archivo debe figurar en el catálogo V0.1 del curso y ser PDF admisible (máx. 5 MB). Se consulta identidad/metadata de Canvas mediante **GET autenticados**; la URL firmada se descarga con **GET sin token Bearer**, permitiendo únicamente HTTPS y redirecciones a dominios de infraestructura verificados. Se valida SHA-256, contenido y número de páginas; el texto se extrae localmente sin ejecutar scripts ni seguir enlaces externos. No se modifica Canvas mediante operaciones de escritura ni el workbook.

**Evidencia real:** se descargaron **12 PDF de cuatro materias (56 páginas)**, todos con capa textual y SHA-256 local correcto. GBD: 3; Sistemas Transaccionales: 3; SO2: 2; SER: 4. Inglés III permanece restringido por 403, sin intento de eludir permisos. Un segundo GET del PDF GBD `22387883` conservó **un solo archivo por hash**. Los datos viven exclusivamente en `C:\AcademicManagerData\procesamiento\canvas-sync\pdfs`. Las tres guías de Sistemas Transaccionales contienen cabecera/plantilla «Negocios internacionales» y `Lorem ipsum`, que exige separar plantilla de instrucciones reales; SER contiene **dos `Tarea2.pdf` con IDs/contenidos distintos**. La consigna PDF de GBD E3 sigue pendiente de contrastar con el cambio docente anunciado.

**Enlaces externos:** auditoría local de hostnames (Google Drive, Meet, Canva y servicios institucionales) sin abrir URL externas ni transferir token. El emparejamiento seguro Actividad–Archivo y la reconciliación con V2.1 aún son pendientes. Contrato y hallazgos: `docs/18-recursos-canvas-pdf.md`.

### Descubrimiento de materiales de estudio por eje (2026-10-07)

**Extracción real completada, pero aún exploratoria (no es todavía un comando estable de `canvas_sync`):** los HTML `eje1.html` a `eje4.html` de GBD, Sistemas Transaccionales, SO2 y SER contienen el atributo `referente-completo`, que enlaza los referentes de pensamiento del servidor institucional `contenidos.areandina.edu.co`. Se inspeccionaron **16 HTML** por GET, se identificaron y descargaron **16/16 PDF de referente** (**309 páginas**) y se extrajo su texto local íntegro. Se inventariaron **26 lecturas recomendadas**, de las cuales **14 PDF institucionales se descargaron** (**1.995 páginas**); **11/14** tienen texto extraído y los otros **3** (738, 331 y 427 páginas) conservan PDF, con extracción pendiente. Se identificaron **84 referencias a multimedia/interactivos** sin ejecutarlas y 12 lecturas que no cumplen el flujo de descarga institucional directo (11 enlaces externos y 1 no resuelta).

Los PDF se obtuvieron por HTTPS GET del dominio oficial **sin enviar el token Canvas**; el token se utilizó solo para los GET de metadatos de archivos Canvas. Todos los **16 referentes y 14 lecturas** pasaron verificación local de bytes/SHA-256. Quedaron en `C:\AcademicManagerData\procesamiento\canvas-sync\study\<course_id>\ejeN\` con TXT y metadatos de procedencia. Inglés III continúa fuera de esa estructura: Canvas `files=403`. No se modificó Canvas mediante peticiones de escritura ni el workbook. Ver `docs/19-material-estudio-canvas.md`. **Pendiente histórico resuelto en el hito siguiente:** el comando `sync-study` ya permite repetir la extracción y crear índices estables por materia/eje. Siguen pendientes la vinculación definitiva con guías/actividades y el acceso autorizado a materiales externos.

### Canvas Sync Study — sincronización permanente de referentes (2026-10-07)

**Implementado y validado con la cuenta real:** `canvas_sync/study.py`, acción `scripts/canvas-sync.cmd sync-study --all-active` y 8 nuevas pruebas automáticas. Extrae con **GET-only** los `ejeN.html` disponibles, sus 16 referentes PDF, 14 lecturas institucionales, y conserva metadata de 12 enlaces de lectura externos/no resueltos y 84 recursos multimedia sin abrirlos. Token DPAPI solo para los GET API Canvas; archivos HTML y PDF GET **sin bearer**, con allowlists HTTPS. No hay métodos de escritura a Canvas ni flujo Excel.

**Primera ejecución real:** 5 cursos revisados, 23 GET API, 4 índices de materias terminados y un `403` explícito en Inglés III. Se verificaron hashes SHA-256 de los cuatro índices y de los 30 documentos PDF asociados; un segundo `sync-study --course-id 82862` indicó «índice sin cambios» y mantuvo un solo snapshot. Código, rutas, límites e implicaciones en `docs/19-material-estudio-canvas.md`. Los scripts exploratorios quedaron en runtime como referencia; los cambios futuros se mantienen en código versionado.

**Pendiente:** clasificación y relación Actividad–Guía–Recurso, cobertura autorizada de Inglés III, revisión de bibliotecas/recursos multimedia, opción de programación futura y reconciliación con V2.1.

## Integración Canvas Sync → base de datos (2026-10-08)

**Implementado y validado, solo lectura:** nuevo `canvas_sync/reconcile.py` y acción `reconcile-preview`, que comparan los snapshots enriquecidos locales (integridad SHA-256) de los cinco cursos con la copia local V2 abierta con Excel COM ReadOnly. Primera comparación: **38 assignments**, de ellos **7 coincidencias en los campos comparados**, **17 candidatos nuevos**, **14 para revisar clasificación** y **0 diferencias detectadas en los campos comparados**. No compara aún todos los campos (fechas, estados, notas, etc.), por lo que no representa igualdad integral. Pruebas del lote: 76 OK, 2 omitidas. Sin escrituras al XLSX, Canvas o Drive. Detalle en `docs/20-reconciliacion-canvas-v2.md`.

**Nuevo avance de automatización cotidiana (2026-10-08):** se incorporó `canvas_sync/routine.py` y `scripts/canvas-sync.cmd daily`, que ejecuta `sync-rich` GET-only y reconciliación local con informe persistente e idempotente (`procesamiento/canvas-sync/routine/latest.json`). Filtro conservador: 5 agregados `Nota final [P1]` y 2 enlaces informativos no se tratan como actividades; quedan **24 pendientes** para clasificar en una sola cola, sin volver a anunciar los mismos casos al no cambiar la evidencia. Prueba conectada: 5 cursos, 66 GET, sin cambios en los snapshots y **0 novedades**; workbook y Canvas sin modificaciones. `daily --offline` permite comparar sin consultar la red. El control SHA de esta etapa se hace contra el **estado local**, no garantiza aún que Drive remoto siga en la misma versión.

**Soporte multisemestre confirmado e implementado parcialmente (2026-10-08):** `canvas_sync/semester.py`, `courses-review` y `onboard --semester YYYY-N` introducen descubrimiento dinámico de cursos, registro local por semestre, alta con confirmación explícita e instancias internas diferentes de materia; `daily` utiliza solo cursos asociados del semestre activo y separa sus informes en `routine/semestres/<semestre>/latest.json`. La primera migración real recuperó las cinco materias 2026-2 (cuatro por Excel, MAT-ST mediante la relación documentada 82892 que aún falta en su fila); `courses-review` autenticado identificó 5/5 cursos conocidos, 0 sin asociar. `daily` real: 5 cursos, 66 GET de extracción, 38 assignments, 24 casos pendientes y 0 novedades. El cambio de semestre futuro se validó con cursos sintéticos, no existe todavía matrícula real de un semestre posterior. Ver `docs/21-semestres-y-matricula.md`.

**Bloques y materia transversal (2026-10-08):** migrado el registro local `course-registry.json` de esquema 1 a 2 con `active_block=2` para `2026-2` y pertenencia por curso `blocks=[1]`, `[2]` o `[1,2]`. Inglés III (`MAT-ING3`, Canvas `91003`) se vincula a **ambos** bloques en una sola instancia; GBD, SO2, SER y ST se vinculan al bloque 2. La lista completa del bloque 1 no está verificada. `daily` reconcilia solo materias del bloque activo; mantiene informes por bloque y continuidad de firmas de materias compartidas. `onboard` solicita los bloques por curso, `course-blocks` edita una pertenencia existente y `block-set` cambia el bloque activo sin cambiar el semestre. Prueba real `daily`: 5 cursos, 66 GET, 38 assignments, 24 pendientes y **0 novedades**; ninguna modificación a Excel o Canvas. Contrato y límites en `docs/21-semestres-y-matricula.md`.

**Hito de publicación V2.1 (2026-10-08):** se añadieron `bloques` en `01 - Materias` y `bloque_activo` en `05 - Semestres`, y se completaron `curso_externo_id=82892` y `lms=Canvas` en MAT-ST. **10 celdas** cambiadas bajo lock compartido y Drive API, con checkpoint, SHA, re-descarga y Excel validado; estado final libre. Hash nuevo: `6c5a415933c4f62d434259a442565eb579c637885d7c33fcb7d7744e74eaf682`. Repetición: cero diferencias. `blocks-refresh` re-descarga y reconstruye el registro local desde la base; otra instalación aislada (simulación de segundo PC) recuperó cinco materias, 2026-2 bloque 2 e Inglés III transversal. Ver `docs/22-publicacion-bloques-v21.md`.

**Alta canónica implementada (2026-10-08, aún sin nueva matrícula real):** `canvas_sync/workbook_enrollment.py` incorpora planificador de filas de materias y semestres confirmados, con nombre/código Canvas revalidados, detección de duplicados, diferenciación de cambio de bloque vs. semestre y publicación por el protocolo compartido de Excel/Drive. `onboard` puede continuar a la publicación en el mismo flujo con confirmación `PUBLICAR`; existen `enroll-preview` y `enroll-publish` para reanudar. Se validó con matrícula sintética en Excel COM y Drive simulado, incluyendo bloqueo, checkpoint, re-descarga e idempotencia. Comprobación **real sin escritura**: `enroll-preview` informó **0 cambios**; `daily --offline` confirmó 38 registros y 0 novedades. Ver `docs/23-alta-canonica-materias.md`.

**Pendiente:** primera utilización con un curso nuevo real, prueba concurrente entre dos PCs físicos, clasificación persistente de las 24 candidaturas de actividades, reconciliación completa/notas tardías y programación de `daily`. No se alteró la base canónica en este incremento.

## Etapa 1 V1 — Cierre operativo en este computador (2026-10-08)

**Confirmado, implementado y validado en PC Diego:** Canvas Sync sincroniza las cinco materias del bloque 2, clasifica los 38 assignments Canvas con decisiones persistentes y mantiene **31 actividades** en el Excel compartido, con **0 excepciones de clasificación actuales** (cinco resúmenes de nota y dos enlaces informativos quedan fuera de Actividades). Se incorporaron **24 actividades nuevas** en dos publicaciones bajo lock, checkpoint, SHA, Drive API y re-descarga, preservando las 7 preexistentes. Las seis complementarias tienen tipo diferenciado y obligatoriedad no confirmada; el acta de Inglés III consta como actividad ya calificada 4/4. Los datos publicados incluyen puntuaciones verificadas, estados, apertura/vencimiento/cierre cuando Canvas los aporta y valores nulos cuando no existen. Dos columnas mínimas adicionales de `02 - Actividades` conservan `fecha_envio_real` (5 fechas verificadas) y `referencias_externas` (4 IDs relacionados) de forma estructurada. SHA canónico final **`ff41a86f40bc355ecf9685c9b86d48e9c993f344d19bb91d696f5a3b6f3cc54f`**, estado `free`, diff posterior = 0.

**Automatización efectiva:** Windows Task Scheduler `AcademicManager-CanvasDaily` programado a las **08:00 y 18:00** (SA Pacific Standard Time, Colombia), con ejecución real de prueba exitosa (`LastTaskResult=0`), bajo la cuenta Windows autorizada y sin ChatGPT abierto. `daily --apply` actualiza automáticamente diffs verificados bajo protocolo compartido y guarda logs/estado local en `procesamiento/canvas-sync/logs`. Permite calificaciones tardías de asignaciones **ya registradas** de bloques anteriores cuando Canvas aún las muestra; probado con workbook y curso sintéticos, no todavía en una materia histórica real distinta al bloque activo. No se programan altas de materias desconocidas sin confirmación inicial.

**Alcance de la decisión del estudiante:** por ahora operará **solo en este PC**, por lo cual **se omite y aplaza expresamente la validación entre dos PCs físicos**. La Etapa 1 se considera operativa para este entorno; la primera matrícula futura nueva y el manejo de fallos/concurrencia entre PCs son validaciones posteriores. No hay notificación móvil/correo proactiva: los errores y novedades se conservan en logs locales. Documentación temática `24-canvas-actividades-automatizacion-v1.md`.

**Etapa 2 en desarrollo (primer incremento implementado el 2026-10-08):** `academic_knowledge/` y `scripts/knowledge.cmd` crean índice local reconstruible desde el Excel V2.1, las clases publicadas en Drive y los materiales de Canvas Study/PDF verificados. Primera ejecución real: **5 materias, 31 actividades, 133 fuentes catalogadas, 34 con texto de búsqueda, 1.623 pasajes y 63 asociaciones con actividades (62 contextuales por eje y una guía explícita SO2 E4)**. La búsqueda devuelve fragmentos originales trazables; `activity` presenta el contexto de una obligación diferenciando referencias explícitas de coincidencia de eje. Repetir `build` sin cambios no modifica el índice. La tarea diaria de Windows pasa a ejecutar también `academic_knowledge build` después de completar Canvas Sync correctamente; no re-descarga los PDF cada día. Documentación `25-indice-conocimiento-academico-v1.md`.

**Decisión de alcance actualizada (2026-10-08):** este Project continúa como **desarrollo del núcleo**; un **Project Coordinador de ChatGPT separado y futuro** administrará la vida universitaria, y cada materia tendrá su Project de estudio. Se definió el circuito *limitación real → consulta de documentación/issues → issue nuevo si procede → evaluación/implementación en Desarrollo*. **Confirmado en diseño; no están creados aún el Coordinador ni los Projects de materia y no se ha probado el circuito GitHub Issue.** Ver `26-roles-coordinador-desarrollo-materias.md`.

**Segundo incremento de Etapa 2 implementado (2026-10-08):** `academic_access/` incorpora un contrato **JSON v1, exclusivamente de lectura**, con `scripts/academic-read.cmd`. Consulta materias y bloques **directamente del Excel canónico**, actividades y fechas/estados, clases, contexto de una actividad, resultados de búsqueda y extractos de origen con SHA, sin crear registros ni modificar fuentes. `health` muestra versión local conocida y último estado registrado de Canvas Sync; errores de validación son JSON con códigos estables para que el futuro Coordinador distinga un acceso fallido de una necesidad de producto. Validación real: nueve actividades de Inglés III, clases de SER, contexto SO2 E4, búsqueda y lectura de `analysis.md` de SER, ID desconocido gestionado como `NOT_FOUND`. Ver `27-contrato-consulta-academica.md`. El Project Coordinador y Projects de materia siguen **sin configurar**.

**Tercer incremento de Etapa 2 confirmado (2026-10-08):** `sync-study` integra caché verificable de PDF/TXT institucional por URL y SHA, migración de hashes legacy sin descargas y **revalidación remota obligatoria cada 7 días** o con `--full-refresh`. `--all-active --approved-only` restringe el inventario a cursos confirmados. Se comprobó en Canvas real: cinco cursos, 23 GET API, **30 PDF reutilizados / 0 descargados** en la ejecución incremental posterior; Inglés III sigue respondiendo 403. La tarea Windows existente se modificó para ejecutar `daily --apply → sync-study incremental → academic_knowledge build`, y se confirmó la sintaxis del script y la tarea registrada. El encadenamiento nuevo completo desde Task Scheduler **todavía no se probó después de la modificación**. La batería general pasó con 132 tests (2 omitidos); el workbook canónico sigue libre con SHA correcto y no fue modificado. Documento temático: `28-actualizacion-incremental-materiales.md`.

**Capture: progreso de transcripción incorporado al código (2026-10-08):** la versión WhisperX instalada dispone de `progress_callback`. Se añadió `progress.json` por job, estimación **orientativa y limitada a ASR**, consulta de todos los jobs y monitor `--status latest --watch 15`; `academic_access capture-jobs` ofrece lectura JSON para el Coordinador y diferencia entre transcribir, publicar y registrar en Excel. SO2 del 2026-09-29, job `83ac7042104d4ce6bb793543714f6fb5`, **terminó localmente a las 21:57:15Z** y aún no estaba publicado ni registrado al inspeccionarlo. No se observó un segundo job nuevo activo en Diego. Las pruebas sintéticas verificaron porcentajes/ETA, no se ha medido aún un flujo ASR real **con la nueva instrumentación**. Documento `30-capture-monitoreo-progreso.md`.

**Etapa 3: primer incremento implementado (2026-10-08):** `academic_projects/` contiene plantillas diferenciadas para Coordinador y asistentes de materias y un generador local `scripts/project-instructions.cmd` que obtiene nombre/semestre/bloques de la **base canónica** para cada `materia_id` real. La plantilla `.github/ISSUE_TEMPLATE/necesidad-o-defecto.md` orienta la creación de Issues de mejora sin publicar datos privados. Se comprobó con `MAT-SER` y se hizo búsqueda **de solo lectura** en GitHub para `Ds0957/Academic-Manager` (0 issues abiertos al consultar). **Confirmado el código preparado; no se crearon Projects ni se verificó escritura de Issues o herramientas en otros Projects.** Guía `29-preparacion-chatgpt-projects.md`.

**Núcleo mínimo de Etapa 2 disponible en este PC** para fuentes autorizadas: materiales y clases verificables, búsqueda, relaciones y consultas JSON + actualización incremental. **Pendientes que no bloquean pasar a Etapa 3:** confirmar en una ejecución real del Programador la nueva cadena Study → Knowledge, acceso autorizado a materiales de Inglés III (files 403), y extraer capítulos específicos de tres lecturas extensas solo según necesidad. El Coordinador y Projects de materia harán la síntesis/planificación conversacional; el núcleo no implementará un segundo asistente.

## Flujo de desarrollo

Desde el 2026-10-07 se adopta un workspace local estable y el flujo de `14-flujo-desarrollo.md`:

```text
contexto mínimo una vez
→ git pull --ff-only
→ edición local por lote
→ scripts/dev-check.ps1
→ prueba integrada relevante
→ revisión/persistencia
→ /docs al final
→ cierre corto
```

`C:\AcademicManagerData\temporales\capture-v0-validation-20261007` deja de formar parte del flujo normal una vez validada esta migración.



## Capture — incorporación de análisis revisado antes de publicar (2026-10-12)

**Implementado en código y probado solo con casos sintéticos:** el comando \`--resume JOB_ID --attach-analysis RUTA --analysis-transcript-sha256 SHA256 --analysis-reviewed\` permite incorporar/revisar \`analysis.md\` en jobs locales validados, antes de publicarlos. Conserva la integridad de los cuatro artefactos, \`metadata.analysis_status=reviewed\` y la vinculación verificable con la transcripción; un diario permite recuperar intercambios interrumpidos. \`--status\` presenta la huella de transcripción y el estado del análisis; \`academic_access capture-jobs\` incluye \`analysis_status\`.

**Sin cambios en datos académicos reales durante esta implementación.** No se ha importado automáticamente ningún análisis anterior ni publicado o registrado una clase real mediante este nuevo flujo. Pendiente su primera validación con un informe humano revisado y publicación/registro efectivos. Detalles: [05-capture.md](05-capture.md), [issue #2](https://github.com/Ds0957/Academic-Manager/issues/2).

### Despliegue local de Capture --attach-analysis (2026-10-12)

**Confirmado en PC Diego:** el clon principal se actualizó por fast-forward de `b9f9e5b` a `9f93426` (`origin/main`), sin cambios locales previos. La CLI del Python instalado en `C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe` reconoce `--attach-analysis`, `--analysis-transcript-sha256` y `--analysis-reviewed`. Las 13 pruebas específicas pasaron en este entorno. No se modificaron jobs, archivos académicos, Drive ni Excel. **Pendiente:** primera incorporación y publicación/registro reales de análisis revisados.

### Capturas SO2 y Sistemas Transaccionales integradas (2026-10-12)

**Confirmado en uso real en PC Diego:** los jobs `83ac7042104d4ce6bb793543714f6fb5` (SO2, 29/09) y `e0eb990002ab423b91d06ebac3dec9c0` (Sistemas Transaccionales, 30/09) incorporaron correctamente los análisis del Coordinador con `analysis_status=reviewed`. Ambos publicaron los cuatro artefactos en las carpetas de las clases bajo Google Drive y registraron las clases en `03 - Clases`: `CLS-SO2-20260929-01` y `CLS-ST-20260930-01`. Los dos jobs finalizaron `completed`, `published=true`, `database_registered=true`.

Se corrigió una incompatibilidad detectada entre el estado de análisis nuevo y la precondición del registrador heredado; 13 pruebas sintéticas de la funcionalidad pasaron tras el cambio. La publicación del workbook completó el protocolo de integridad. Último estado compartido comprobado: `status=free`, `lock=null`; SHA-256 publicado y copia local: `4505ff3be59ad3a366243ec41afbcff5018d705e3a27701ac4e8750b733ad369`. Sin retranscripción ni manipulación manual de hashes. Detalles: [05-capture.md](05-capture.md).

### Interfaz de operaciones del Coordinador (2026-10-12)

**Implementado en código y validado con pruebas sintéticas:** nuevo `academic_operations` con vista previa JSON `preview` y operación acotada `complete`, separado de `academic_access` que sigue siendo de solo lectura. Verifica identidad, hashes de transcripción e informe, ruta de análisis permitida, mapeo de materia, consentimiento y vigencia del plan. Reutiliza publicación/registro de Capture, puede reanudar una ejecución parcial y reconstruye el índice local al terminar. Wrapper `scripts/academic-act.cmd`.

**Estado posterior confirmado el 2026-10-12:** configuración y `preview` desplegados/validados en Diego; el Project Coordinador también ejecutó `preview` con éxito y solo lectura. **Pendiente:** verificar `complete` desde dicho Project para una futura clase nueva, con aprobación explícita. No repetir la publicación de SO2/ST. Ver `31-operaciones-coordinador.md`.

### Despliegue de academic_operations y primera previsualización (2026-10-12)

**Confirmado en PC Diego:** PR #4 integrado en `main` (`3df7136`), copia local alineada, y configuración `C:\AcademicManagerData\control\academic-operations.json` creada con carpeta Drive autorizada, archivo de estado canónico y los cinco mapeos de materia verificados. `scripts/academic-act.cmd preview` se ejecutó **solo en lectura** sobre los jobs ya registrados de SO2 y Sistemas Transaccionales y devolvió los IDs `CLS-SO2-20260929-01` y `CLS-ST-20260930-01`, ambos `reviewed`, `published=true`, `database_registered=true`. Suite general: 165 tests OK, 2 omitidos; tras reforzar publicación parcial, 9 pruebas específicas OK.

**Validación comunicada por el Project Coordinador (2026-10-12):** ejecutó desde Diego `scripts\academic-act.cmd preview --job 83ac7042104d4ce6bb793543714f6fb5`; la herramienta aceptó la llamada y el proceso terminó con código 0 y JSON `ok=true`, `action=preview`, `analysis_status=reviewed`, `published=true`, `database_registered=true`, clase `CLS-SO2-20260929-01` y paso restante `refresh_index`. Fue **exclusivamente lectura**; el Coordinador no invocó `complete`, no aprobó ningún plan y no modificó datos. **Pendiente:** validar `complete` desde el Project Coordinador sobre una futura clase que realmente lo requiera, con autorización explícita. Esta prueba no explica retrospectivamente la regla del bloqueo remoto anterior.

### Comprobación adicional de frescura del índice (2026-10-12)

**Consulta local de solo lectura en Diego:** `scripts\academic-read.cmd health` devolvió `ok=true`, `status=stale`, `index.fresh=false`. El SHA-256 del workbook local es `4505ff3be59ad3a366243ec41afbcff5018d705e3a27701ac4e8750b733ad369`, distinto del SHA almacenado por el índice (`0f712af24da3f2c309988c34f645a79efdf44b221569309e23fb06d0959f5618`). Esto confirma que el índice requiere actualización respecto a la base local; **no indica que las dos clases estén sin registrar**. No se ejecutó reconstrucción ni `complete` durante esta comprobación.

### Despliegue del endurecimiento de complete — issue #5 (2026-10-12)

**Confirmado en Diego:** el PR #6 se integró en `main` y se desplegó con fast-forward hasta la revisión `89645f2`; árbol de Git limpio. Se añadieron diagnóstico por etapa en `academic_operations.complete` y reintentos acotados frente a `PermissionError` durante el intercambio/recuperación de artefactos de Capture. La regresión completa de Windows pasó con **171 pruebas, 2 omitidas**, y las focalizadas con **27 pruebas sin fallos**.

Tras la instalación, `scripts/academic-act.cmd preview` devolvió `ok=true` sobre el job de Sistemas Transaccionales del 8 de octubre; el análisis continúa `pending`, `published=false`, `database_registered=false` y la vista previa propone incorporar/publicar/registrar/indexar. **No se ejecutó `complete` sobre ninguna clase real en este incremento.** La causa exacta del fallo anterior no pudo establecerse porque la excepción original había sido ocultada; ver `docs/31-operaciones-coordinador.md` e issue #5. Pendiente validación de `complete` por el Coordinador con nuevo plan y autorización explícita.

### Segundo incremento Windows de Capture: fallback de archivos (2026-10-12)

**Implementación confirmada y desplegada en PC Diego:** PR #7 integrado con revisión `76b733f`, árbol local limpio y coincidente con `origin/main`. El flujo de incorporación de análisis mantiene primero el intercambio validado de directorios, pero ante `PermissionError` persistente al renombrar el directorio original usa una alternativa recuperable: respaldo verificado, diario `mode=files` y sustitución exclusivamente de `analysis.md` y `metadata.json`. Los dos artefactos de transcripción no se reescriben. La recuperación reconcilia estados antiguo, nuevo o mixto; se añadió `error.substage` sanitizado.

**Pruebas en Diego:** suite completa **177 casos, 2 omitidos, 0 fallos**; la simulación de `complete` con denegación persistente del renombrado original incorporó/publicó/registró una clase ficticia; tests de rollback e interrupción también pasaron. La comprobación posterior a despliegue con `preview` sobre Transaccionales del 8 de octubre dio `ok=true` y confirmó que ese job **sigue pendiente**, `analysis_status=pending`, `published=false`, `database_registered=false`. No se ejecutó `complete` real ni se alteró Drive, Excel ni las clases reales durante la implementación.

**Pendiente:** nueva aprobación y aceptación operativa de `complete` desde el Project Coordinador en una clase real; si hay un nuevo error, registrar únicamente `error.code`, `stage`, `substage`, número de error Win32 y estados oficiales. El WinError 5 previo fue confirmado en `attach_analysis`, pero no en la operación interna exacta. El [issue #5](https://github.com/Ds0957/Academic-Manager/issues/5) permanece abierto hasta verificar publicación, registro e índice.

### Primera finalización real desde el Project Coordinador tras PR #7 (2026-10-12)

**Confirmado por ejecución real del Coordinador y comprobaciones independientes de Desarrollo en Diego:** con aprobación explícita del estudiante, `academic_operations complete` finalizó una sola vez la clase de **Sistemas Transaccionales del 8 de octubre de 2026**, job `129e3dafdbbb488080df6d50aa1b2faf`, versión local `46ac096`. El contrato devolvió `ok=true`, salida 0, `analysis_status=reviewed`, `published=true`, `database_registered=true`, `index_refreshed=true` y clase `CLS-ST-20261008-01`, sin advertencias. El Coordinador comprobó además cuatro artefactos coincidentes por SHA-256 con los publicados y lectura del análisis.

**Verificación independiente de solo lectura por Desarrollo:** `scripts/academic-read.cmd capture-jobs --job 129e3dafdbbb488080df6d50aa1b2faf` confirmó análisis `reviewed`, publicación y registro positivos; `classes --materia MAT-ST` devolvió **exactamente dos clases**: `CLS-ST-20260930-01` y `CLS-ST-20261008-01`. `health` devolvió `status=ok`, `index.fresh=true` y `warnings=[]`; SHA del workbook e índice concuerdan. **No se realizaron escrituras de datos académicos desde Desarrollo.**

**Pendientes operativos separados de este defecto:** GBD del 8 de octubre (job `bb8ba9617e824b34985a490970be7c2c`) y SO2 del 6 de octubre (job `2be81f13d71f42fdacb72b20a8792a07`) conservan `analysis_status=pending`, `published=false` y `database_registered=false`. Corresponde al Coordinador gestionar cada una mediante nueva vista previa y **autorización independiente**. El issue #5 puede cerrarse como resuelto por aceptación real; no se ha demostrado exactamente cuál llamada interna de Windows causó la denegación anterior.

### Plantilla práctica para los cinco Projects de materia (2026-10-12)

**Implementado en código, instalación en ChatGPT pendiente:** se amplió `academic_projects/templates/materia.md` para que cada asistente especializado sirva como espacio de trabajo académico, con desarrollo de actividades reales, productos entregables, revisión de consignas y rúbricas, ejercicios y estudio. Conserva consultas de solo lectura acotadas a `materia_id` y el papel global del Coordinador. El catálogo verificado en Diego conserva cinco materias del semestre 2026-2: `MAT-GBD`, `MAT-ING3`, `MAT-SER`, `MAT-SO2`, `MAT-ST`; Inglés III pertenece a bloques 1 y 2 en un solo registro. No se ha creado ningún Project ni alterado actividad o base canónica en este cambio.

### Integración Coordinador–materias, fase 1 (2026-10-25, issue #16)

**Implementado:** `academic-read.cmd subject-brief --materia MAT-...` resume una materia (pendientes, entregas, tareas, decisiones vigentes, resúmenes de clase) y los Projects de materia empiezan cada chat con esa orden. `project-instructions.cmd all` escribe en una sola orden las instrucciones del Coordinador y de todas las materias activas (en `temporales\instrucciones-projects` por defecto; no borra archivos de semestres anteriores). `academic-act.cmd preview/complete` resuelven la materia de Capture desde `01 - Materias` del semestre activo; `subjects` en `academic-operations.json` pasa a ser opcional y sigue mandando. `summary` muestra solo notas y tareas del semestre activo (y tareas sin materia). Se quitaron el respaldo a cursos fijos de 2026-2 en la reconciliación y el nombre fijo de Inglés III en la plantilla del Coordinador; el renderizador acepta semestres `-3`. **Pendiente operativo:** regenerar con `project-instructions.cmd all` y pegar de nuevo las instrucciones del Coordinador y de cada Project de materia.

### Capture: material de estudio desde YouTube y archivos locales (2026-10-25, issue #17)

**Implementado:** Capture transcribe un video de YouTube por URL (subtítulos escritos por el autor o, si no hay, solo el audio con WhisperX; nunca subtítulos automáticos; sin cookies; un solo video público o no listado de hasta 4 h) o un archivo local (`C:/downloads/...`). `--tipo material --subject-id MAT-...` lo guarda como material de estudio en `<semestre>/<materia>/Material`, sin fecha ni ID `CLS-` y sin registro en `03 - Clases`; `knowledge build` lo indexa como `material:<job_id>` para `academic-read search`/`source`. Acceso con `scripts\capture.cmd`. **Pendiente:** instalar `requirements-capture.txt` en el entorno de Capture y probar en Windows un video público corto con y sin subtítulos manuales.

### App para personas no técnicas, fase 1: rutas y comprobación del equipo (2026-10-25, issue #20)

**Implementado:** la carpeta de datos y la de Drive se configuran por usuario en `%APPDATA%\AcademicManager\config.json` (claves `runtime` y `drive_root`; `ACADEMIC_MANAGER_CONFIG` cambia su ubicación); sin el archivo, todo funciona como antes ([03](03-almacenamiento-y-seguridad.md)). `python -m academic_setup check [--texto] [--benchmark]` comprueba si el PC sirve para transcribir con WhisperX (`apto_gpu`, `solo_cpu` con modelo recomendado, `no_apto`) con un resumen sin jerga ([37](37-comprobacion-equipo.md)); Capture acepta `--model {large-v3,medium,small}` (por defecto `large-v3`). **Pendiente:** asistente de configuración en el navegador (T3), instalador de Windows (T4) y probar la comprobación en un PC Windows sin tarjeta gráfica.

### App para personas no técnicas, fase 1: asistente, base nueva y Drive propio (2026-10-25, issue #20)

**Implementado:** `scripts\academic-app.cmd` (o `python -m academic_app`) abre un asistente local en el navegador ([38](38-asistente-configuracion.md)): carpeta de datos, comprobación del equipo, token de Canvas, su propio proyecto de Google Cloud con un solo inicio de sesión que lee y escribe en Drive, carpeta de Drive y materias con casillas. En una carpeta vacía crea la base sin datos, la publica por primera vez en su Drive con el archivo de control y deja `control/academic-operations.json`; se puede retomar tras cualquier fallo y nunca sobrescribe datos existentes. Tras publicar las materias construye el índice de búsqueda. La publicación usa la cuenta de servicio cuando existe (instalación actual, sin cambios) y, si no, ese inicio de sesión propio. **Pendiente:** instalador de Windows (T4), tarea diaria para otras carpetas de datos (los `.ps1` usan `C:\AcademicManagerData`) y probar todo con Google Drive y Windows reales.
