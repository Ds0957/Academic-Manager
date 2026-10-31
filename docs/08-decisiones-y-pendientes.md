# 08 — Decisiones y pendientes

Este documento separa explícitamente lo decidido de lo que sigue abierto.

## Decisiones confirmadas

### Operaciones controladas para el Coordinador (2026-10-12)

**Decisión de diseño confirmada, código inicial probado sintéticamente:** el Coordinador puede completar clases mediante una operación limitada, con vista previa solo lectura y aprobación explícita. `academic_operations` incluye `preview` y `complete`, separado de `academic_access`; reutiliza protocolos confirmados y restringe informes e IDs. Un rechazo del conector externo se respeta, no se elude.

**Confirmado posteriormente el 2026-10-12:** instalación/configuración en Diego y ejecución de `preview` desde el propio Project Coordinador con JSON válido y código de salida 0. **Pendiente:** autorización y ejecución de `complete` desde ese Project cuando exista una clase nueva que requiera escritura; respetar cualquier bloqueo del conector sin intentar eludirlo. El índice local muestra `fresh=false` respecto al workbook actual según `academic-read.cmd health`; su reconstrucción no se ha autorizado ni ejecutado en esta verificación. Ver `docs/31-operaciones-coordinador.md`.


### Capture — incorporación autorizada de análisis académico (2026-10-12)

**Confirmado e implementado en código, validado con datos sintéticos:** el análisis del Coordinador se incorpora explícitamente a un job Capture local validado y aún no publicado, mediante \`--attach-analysis\`, SHA-256 de \`transcript.json\` y aprobación humana \`--analysis-reviewed\`. Se almacena \`analysis_status=reviewed\` y procedencia verificable, con intercambio recuperable de los cuatro artefactos. El núcleo no certifica automáticamente la calidad académica ni modifica análisis publicados. La publicación y el registro siguen siendo fases separadas bajo los protocolos existentes. Referencias: [issue #2](https://github.com/Ds0957/Academic-Manager/issues/2), \`docs/05-capture.md\`.

**Pendiente operativo:** aplicar por primera vez el comando a un informe real ya revisado y ejecutar posteriormente publicación/registro con comprobaciones efectivas. No usar un informe redactado para otra transcripción con SHA distinto.



### Actualización incremental de materiales de estudio (2026-10-08)

**Confirmado e implementado:** `sync-study` consulta metadatos y HTML de los ejes en Canvas mediante GET, y reutiliza PDF/TXT institucionales locales únicamente al comprobar integridad, URL y origen; si el HTML cambia o la fuente está dañada, re-descarga con seguridad. **Revalidación remota completa cada siete días** o a solicitud con `--full-refresh`; las consultas a caché no posponen el plazo. `--approved-only` limita la rutina automática a matrículas confirmadas. La segunda ejecución real recuperó 30 PDF desde caché con 0 nuevas descargas; test completo 132 casos, 2 omitidos. Documento `28-actualizacion-incremental-materiales.md`.

**Pendiente operativo:** ejecución integral de la cadena nueva desde Task Scheduler tras el cambio (su script, tarea y datos locales fueron verificados), 403 legítimo de Inglés III, y extracción selectiva de capítulos de lecturas extensas cuando sea necesaria.

### Paquete de preparación para el Coordinador y Projects de materia (2026-10-08)

**Confirmado e implementado como infraestructura de Etapa 3:** plantillas separadas en `academic_projects/templates/`, renderizador `python -m academic_projects coordinador|materia --materia MAT-...` con identidad contrastada contra el Excel canónico, y plantilla `.github/ISSUE_TEMPLATE/necesidad-o-defecto.md` para solicitudes técnicas sin datos privados. La conexión GitHub actual permitió buscar issues (no había abiertos al momento de la prueba), sin crear ningún issue. Ver `29-preparacion-chatgpt-projects.md`.

**Pendiente de ejecución real:** crear y configurar el Project Coordinador y al menos un Project piloto de materia, comprobar sus propios permisos de GitHub/Remote Desktop Commander y consultas de `academic_access`, y validar un issue genuino cuando exista una limitación. No afirmar que los Projects o sus conexiones ya existen.

### Contrato de consultas del núcleo para Projects (2026-10-08)

**Confirmado e implementado:** interfaz local `academic_access/` JSON v1 para futura integración de Project Coordinador y Projects de materia. Contrato `read_only`; operaciones de diagnóstico, materias/bloques, clases/actividades, búsqueda y fragmentos con SHA. Los datos se leen desde el Excel local validado y el índice existente; no se publican archivos ni se aplica lógica conversacional. Se distinguen `INDEX_STALE`, `SOURCE_CHANGED`, `NO_TEXT`, `NOT_FOUND` y errores de argumentos. Documentado en `27-contrato-consulta-academica.md`.

**Pendiente:** instalar/configurar las herramientas autorizadas en los futuros Projects, comprobar su acceso efectivo, su uso de las consultas y el ciclo real de GitHub Issues. Las lecturas locales no implican conexión en vivo a Canvas/Drive.

### Separación de roles: desarrollo, coordinador y asistentes por materia (2026-10-08)

**Confirmado como diseño, no aún como despliegue:** el Project actual es **Desarrollo de Academic Manager**; el futuro Project independiente **Coordinador** será la interfaz conversacional que organiza la vida universitaria; los Projects de cada materia apoyarán el estudio especializado. El núcleo ofrece fuentes persistentes, sincronización, índices y operaciones seguras, **no debe duplicar un tutor/coordinador conversacional completo**.

**Flujo de mejora confirmado:** si el Coordinador descubre una limitación real, primero consulta el estado y los issues existentes; cuando se necesita una corrección o feature del sistema, abre un **GitHub Issue** en `Ds0957/Academic-Manager` con evidencia no sensible, alcance e impacto. El **Project de Desarrollo** revisa, decide, implementa, prueba y documenta. No crear issues para cada consulta académica ni dar por resuelto un problema por haber abierto un issue. Contrato: `26-roles-coordinador-desarrollo-materias.md`.

**Planificado y pendiente de validación:** crear Projects Coordinador/materias, establecer sus permisos/conectores a datos y GitHub, redactar instrucciones concisas y probar el ciclo issue → solución → continuidad.


### Nombre y continuidad

El sistema se llama **Academic Manager**.

El repositorio `Ds0957/Academic-Manager`, especialmente `/docs`, es la memoria persistente del desarrollo entre chats. Al iniciar un chat nuevo se consulta primero `docs/README.md`, `docs/07-estado-actual.md` y este documento; después solo la documentación temática necesaria.

### Almacenamiento y trabajo local

Se adopta arquitectura local + publicación:

```text
C:\AcademicManagerData\
├── database.xlsx
├── temporales\
├── procesamiento\
└── control\
        ↓
  publicación
        ↓
Google Drive / Academic Manager
```

`C:\AcademicManagerData\database.xlsx` es la copia de trabajo del PC. Google Drive conserva la versión publicada/canónica compartida.

La unidad virtual de Google Drive Desktop (`H:` en el PC de trabajo) no se usa como vía programática de escritura del workbook mientras siga mostrando comportamiento inestable.

### Acceso mediante Remote Desktop Commander

RDC queda restringido a:

```text
C:\AcademicManagerDev
C:\AcademicManagerData
G:\Mi unidad\03 - Universidad\Academic Manager
```

No se habilita acceso general al disco `C:`.

RDC se usa para filesystem, terminal y procesos. Su parser/writer XLSX específico no es dependencia del núcleo; Excel nativo u otra herramienta XLSX validada debe comprobar la integridad del workbook.

### Continuidad de Desktop Commander Remote

**Confirmado:** en el PC `Diego` se utilizará una tarea programada de Windows al iniciar sesión, con permisos limitados, para reactivar **la identidad de dispositivo ya vinculada**. El comando usa `npx.cmd` para no depender de la política que bloquea `npx.ps1`. La tarea no requiere permisos de administrador y no amplía los directorios autorizados.

**Pendiente de validación operativa:** cerrar e iniciar sesión en Windows (o reiniciar el PC), comprobar que el dispositivo original se conecta sin terminal manual ni nuevo registro y revisar posibles restricciones de Windows o la organización. El arranque de un PC apagado o sin sesión no está soportado por esta tarea.

### Canvas Sync: integración autorizada

**Decisión confirmada (2026-10-07):** conectar Canvas Areandina mediante API REST, con token personal generado por el estudiante, entrada oculta en la terminal de su PC, protección DPAPI vinculada al usuario Windows y renovación **manual** asistida por `renew`. El máximo observado en Canvas para tokens personales es de 90 días. Se avisará del vencimiento al consultar `status` desde 15 días antes; no se presupone renovación automática. El primer componente `canvas_sync/` realiza únicamente peticiones HTTP GET y conserva primero evidencia JSON local, sin modificar el workbook. La documentación funcional está en `docs/15-canvas-sync.md`.

**Estado de validación confirmado:** se completó `login` con nuevo token en el PC Diego (DPAPI, expiración `2027-01-04 23:30 -0500`) y después `status` y `sync` real por Canvas API para GBD curso `82862`. Se extrajeron 5 registros y se comprobó idempotencia en segunda ejecución: un solo snapshot, sin diferencias. Ejes 1–4 coinciden con exploración anterior; el quinto, `Nota final [P1]`, necesita clasificación y no debe importarse automáticamente como actividad común. No se modificó el workbook. **Pendientes:** validación real de `courses`, clasificación de tipos de actividad Canvas, datos de entregas/calificaciones cuando existan, reconciliación hacia V2.1, avisos programados de caducidad y control de concurrencia. Ver evidencia `docs/15-canvas-sync.md`.

### Cobertura de Canvas API en las cinco materias

**Confirmado por auditoría real (2026-10-07):** la autorización de estudiante permite, mediante GET, consultar cursos, 38 registros de assignments en las cinco materias, módulos/ítems, metadatos de 51 archivos accesibles, 18 temas de discusión, 18 quizzes, 14 rúbricas adjuntas, planificador con 24 elementos y cinco registros de Inglés III con calificación y `submitted_at`. El token sigue cifrado localmente; no se modificaron el workbook ni los contenidos de Canvas. En 82 solicitudes instrumentadas hubo 0 respuestas 429. **Rutas restringidas/no disponibles:** archivos de Inglés III (403), rúbricas listadas por endpoint general (403 en los cinco), algunas páginas y ciertos endpoints de conferencias/progreso (404). Detalle: `docs/16-cobertura-canvas-api.md`.

**Pendiente de decisión e implementación:** Sync V0.1 con extracción enriquecida y clasificación de recursos/actividades informativas/notas agregadas; reconciliación de IDs externos y `submitted_at` en V2.1; deduplicación de planner/assignments, extracción opcional de archivos y cobertura de herramientas externas. No interpretar el catálogo público de API como datos realmente disponibles para el estudiante.

### Canvas Sync V0.1 — extracción enriquecida

**Confirmado e implementado (2026-10-07):** acción `sync-rich` para consultar las materias activas con exclusivamente HTTP GET de Canvas, host verificado y redirecciones bloqueadas; no se permiten POST/PUT/PATCH/DELETE. Se extraen curso, actividades/fechas, rúbricas, instrucciones a texto no ejecutable, módulos/ítems, archivos indexados sin descarga, páginas, foros (sin respuestas de compañeros), quizzes sin preguntas/intentos, avisos, planner y estado académico propio. El snapshot v01 es local y separado del V0 y del workbook. Las clasificaciones quedan como **candidatas**, no autorizaron importación automática al XLSX.

**Validación real:** cinco cursos sincronizados con 66 GET, **38 registros**, **14 rúbricas**, **51 referencias de archivos**, 18 discusiones, 18 quizzes, 6 módulos/42 ítems y 5 calificaciones/fechas de envío de Inglés III. Integridad SHA-256 validada y segunda ejecución idempotente. Vía de archivos Inglés III bloqueada (403) y páginas listadas 404, estados persistidos. Se advierte que GET puede dejar registro de acceso/lectura en el servidor aunque no altere el contenido académico. Detalle: `docs/17-canvas-sync-enriquecido.md`.

**Pendientes:** extracción selectiva de archivos académicos, LTI/contenido externo, clasificación revisada, modelo de referencias y `submitted_at` en V2.1, importación a Excel bajo protocolo, avisos de caducidad y reintentos 429.

### Recuperación selectiva de PDF Canvas

**Confirmado e implementado (2026-10-07):** los PDF accesibles del catálogo V0.1 pueden descargarse **únicamente por elección explícita de course ID + file ID**, vía GET, sin enviar token a servidores de archivos/redirección. Se validan origen HTTPS, dominio de redirección institucional, tipo, tamaño, bytes y SHA-256; se guarda PDF y texto local, nunca URL firmada ni credencial. **No existen métodos de escritura Canvas ni cambios al workbook en este flujo.** Se comprobaron 12 PDF reales de cuatro materias, 56 páginas, y no se eludió el 403 de Inglés III.

**Hallazgos que necesitan revisión antes de V2.1:** tres PDF de Sistemas Transaccionales muestran plantilla de «Negocios internacionales» y texto de prueba; SER contiene dos `Tarea2.pdf` con contenido/IDs distintos; GBD E3 requiere contraste con la modificación indicada en clase. El seguimiento automático de enlaces Drive/LTI/grabaciones no está habilitado. Siguiente pendiente: índice de evidencias y relaciones Actividad–Archivo con estado de confirmación antes de cualquier importación. Ver `docs/18-recursos-canvas-pdf.md`.

### Materiales de estudio por eje y referentes de pensamiento

**Confirmado por exploración real (2026-10-07):** los HTML `ejeN.html` de cuatro materias contienen referencias oficiales a los PDF de **referentes de pensamiento** y recursos complementarios. Se descargaron/validaron **16 referentes principales (309 páginas y texto completo)** y **14 PDF de lecturas institucionales (1.995 páginas)** bajo el runtime local, sin usar el token fuera de la API Canvas, sin operaciones de escritura en Canvas ni alteración del workbook. Hay **26 lecturas** y **84 referencias multimedia** identificadas; 3 lecturas extensas mantienen PDF sin texto extraído íntegro, 11 lecturas externas y otra URL no resuelta. Inglés III requiere un canal de estudio diferente y el endpoint de archivos sigue en 403. Resultados completos en `docs/19-material-estudio-canvas.md`.

**Confirmado e implementado posteriormente:** el comando `sync-study --all-active` resuelve sincronización GET-only, versión por SHA-256 e índice Materia/Eje/Referente/Lectura/Multimedia. Primer uso real: 5 cursos consultados, 4 índices, 23 GET Canvas, 16 referentes, 14 lecturas, 12 enlaces catalogados y 84 recursos multimedia catalogados; Inglés III bloqueado (403), respetado. Un segundo uso sobre GBD confirmó idempotencia. **Pendiente** clasificar los vínculos con actividades/guías, gestionar acceso externo con autorización independiente, programar invocaciones y reconciliar con V2.1; **no se incorpora contenido al XLSX** sin decisión y protocolo de publicación.

### Protocolo obligatorio del workbook

Toda modificación debe seguir:

```text
estado compartido / última publicada
→ lock
→ verificar hash base
→ checkpoint
→ editar copia válida
→ validar
→ comprobar que Drive no cambió
→ publicar
→ re-descargar
→ revalidar
→ alinear copia local
→ liberar lock
```

No se publica si falla una validación. Una subida no se considera terminada hasta re-descargar y validar lo almacenado.

Un entorno aislado de ejecución puede usar una copia temporal solo si demuestra que parte exactamente de `published.sha256`; al finalizar debe publicar/revalidar y alinear `C:\AcademicManagerData\database.xlsx`. Ese entorno no es fuente de verdad ni requisito arquitectónico.

### Flujo de desarrollo eficiente

**Confirmado.** El código y `/docs` se trabajan desde `C:\AcademicManagerDev\Academic-Manager`, separado del runtime `C:\AcademicManagerData`.

El flujo estándar es: recuperar contexto una vez, sincronizar el repo, editar un lote coherente localmente, ejecutar una batería de validación, hacer solo la prueba integrada relevante, persistir el cambio y actualizar `/docs` al final. No se usa `temporales` como mirror normal del repositorio ni se repiten validaciones equivalentes sin una razón concreta.

Las validaciones críticas del workbook no se reducen. La optimización elimina latencia de orquestación, no controles de integridad. Detalle en `14-flujo-desarrollo.md`.

### Publicación del workbook por Google Drive API

**Confirmado.** La publicación binaria habitual de `Academic Manager - Database.xlsx` se realiza por Google Drive API y conserva el mismo Drive file ID. Google Drive Desktop no se usa como vía programática de escritura del XLSX.

Para el Drive personal de Academic Manager se usa una identidad técnica separada cuyo acceso efectivo queda limitado por los permisos compartidos de la carpeta `Academic Manager`. Esto evita depender del OAuth personal de usuario para cada publicación.

El ciclo ya fue validado con la primera clase real: lock, checkpoint, modificación local, validación, comprobación de versión base, actualización por API, re-descarga, SHA-256, apertura con Excel, alineación local y liberación.

### Flujo multi-equipo

Se creó en Drive:

```text
database-work-state.json
```

Coordina un solo escritor mediante:

```text
status = free | editing
lock = null | { machine, started_at, base_sha256 }
published.sha256 = hash de la última versión validada
```

No hay timeout automático. Un lock aparentemente abandonado se investiga antes de liberarlo.

El 2026-10-06 se validó el ciclo completo de lock/publicación/liberación en un solo PC. La prueba real entre dos computadores distintos sigue pendiente.

Estado publicado actual:

```text
status = free
lock = null
published.sha256 = 9b4686373de57881b36a5712b70df263f98272a5392508c662fd2a21189962c8
```

### Organización académica

Semestre activo: `2026-2`.

Materias actuales:

- Gestión de Bases de Datos
- Inglés III
- SER (BE-IT)
- Sistemas Operativos II
- Sistemas Transaccionales

No se creará una jerarquía interna completa por anticipado; se evoluciona según uso real.

### Estructura mínima confirmada para clases

La estructura interna de una materia sigue siendo incremental, pero ya se confirmó el primer bloque persistente para clases:

```text
<semestre>/<materia>/Clases/<nombre de la grabación>/
├── metadata.json
├── transcript.md
├── transcript.json
└── analysis.md
```

La carpeta se deriva del nombre real de la grabación. La grabación audiovisual se descarga solo de forma temporal para procesamiento y se elimina automáticamente después de validar las salidas persistentes. No se duplica por defecto en Google Drive.

### Base estructurada V2

Archivo publicado:

```text
Academic Manager - Database.xlsx
```

Hojas confirmadas:

- `00 - Dashboard`
- `01 - Materias`
- `02 - Actividades`
- `03 - Clases`
- `04 - Recursos`
- `05 - Semestres`
- `06 - Entregables`

Reglas de V2:

- IDs internos de Academic Manager son la identidad estable;
- IDs de Canvas/Moodle son metadata externa;
- Actividades separa apertura/entrega/cierre;
- separa acceso/entrega/calificación;
- puntos posibles, puntos obtenidos, peso y nota institucional son conceptos diferentes;
- dato ausente permanece nulo y no se convierte en cero;
- Clases distingue estado de sesión y grabación;
- Recursos conserva procedencia/verificación;
- Entregables representa varias evidencias o modalidades alternativas dentro de una actividad.

### Primera importación real

El 2026-10-06 se importó correctamente `ACT-GBD-001`, correspondiente a `Actividad evaluativa - Eje 1` de GBD.

Se preservaron fechas, estados observados, 20 puntos posibles, `assignment_id = 391671`, URL del quiz y `quiz_id = 228076` en notas. No se inventaron peso, puntaje obtenido ni nota institucional.

La versión fue publicada, re-descargada, comprobada por SHA-256, abierta con Excel y alineada con la copia local antes de liberar el lock.

### Segunda importación real — SER

El 2026-10-06 se importaron `ACT-SER-002` y `ACT-SER-003`, con los recursos `REC-SER-001`/`REC-SER-002` y los entregables `ENT-SER-002-01`/`ENT-SER-003-01`.

Se confirmó que E3 amplía el mismo Documento de Google iniciado en E2. Como la URL/ruta real del documento del estudiante no existe todavía en la evidencia, no se creó un `Recurso` ficticio. El requisito acumulativo queda representado en Entregables mientras la relación Actividad–Recurso permanece pendiente de implementación.

La segunda importación volvió a completar correctamente lock, checkpoint, validación, publicación, re-descarga, apertura con Excel, alineación local y liberación. El hash publicado resultante es `4ac9b787f72dd0680714c8e15232c9291f93149191037f5bd4eac4c8e7253a58`.

### Tercera importación real — SO2

El 2026-10-06 se importaron `ACT-SO2-002` (foro calificado) y `ACT-SO2-004` (tarea con documento + video), además de `REC-SO2-001` y dos entregables de E4. La publicación fue re-descargada, verificada por SHA-256 y abierta con Excel antes de liberar el lock.

**Confirmado por evidencia real:** una actividad externa puede requerir varias referencias simultáneas. GBD aporta `assignment + quiz` y SO2 aporta `assignment + discussion`. El supuesto de un único par externo por actividad es insuficiente como modelo general. La solución concreta de persistencia sigue pendiente como ajuste V2.1.

### Cuarta importación real — Inglés III

El 2026-10-06 se importaron `ACT-ING3-HW2` y `ACT-ING3-ORAL`, además de `ENT-ING3-ORAL-VIDEO` y `ENT-ING3-ORAL-LIVE`.

**Confirmado por evidencia real:** `Entregables.alternativa_grupo` sirve para representar dos modalidades excluyentes de una misma obligación. El oral es una sola actividad: video **o** presentación en vivo, no dos tareas separadas.

El homework aportó el primer caso calificado real de la base: 5.7/6 puntos, peso 6%, entrega observada y estados `cerrada / entregada / calificada`. El `due_at` de Canvas permanece nulo y no se sustituye con la fecha de cierre.

La cuarta publicación fue re-descargada, revalidada con `artifact_tool`, abierta con Microsoft Excel y alineada con la copia local. Hash final: `25dd3b77ba6cb683ad786cf03b4ea7b83dd2a856f839869703ac772e91a15c4c`.


### Frontera inicial Canvas Sync → V2 (2026-10-08)

**Confirmado e implementado:** primera previsualización de reconciliación estrictamente local y de solo lectura, que valida SHA de snapshots Canvas, identifica registros existentes por IDs, expone diferencias parciales y candidatos sujetos a revisión. No modifica Canvas, Excel, Drive ni estado compartido. Primera ejecución: 38 registros, 7 coincidencias parciales, 17 nuevos por revisar, 14 de clasificación dudosa; suite de 76 tests OK (2 omitidas). Documentado en `20-reconciliacion-canvas-v2.md`. Esto **no confirma** importación automática ni resolución definitiva de V2.1.

**Avance confirmado posteriormente (2026-10-08):** `daily` reúne captura GET-only y revisión idempotente, persiste informe solo local, presenta novedades por firma y mantiene pendientes. Cinco resúmenes de nota y dos enlaces informativos se excluyen únicamente de candidaturas a actividad; no desaparecen de la evidencia. Primera ejecución con Canvas: 5 cursos/66 GET, 38 registros; 7 vinculados, 7 apartados de candidaturas, 24 por revisar, 0 novedades en la ejecución conectada (sin cambios de fuente). **Pendiente:** decisiones aprobadas persistentes, comparación integral, comprobación del Drive remoto y publicación segura; no se ha configurado ejecución programada.

### Descubrimiento y alta multisemestre (2026-10-08)

**Confirmado e implementado en fase local:** el flujo cotidiano no dependerá de IDs de curso Canvas codificados. `semester.py` recupera la correspondencia del workbook V2 y mantiene un registro local de cursos por semestre; detecta nuevos cursos visibles, requiere una confirmación única por nueva matrícula y genera un `materia_id` nuevo por cursada. `daily` reconcilia solo cursos aprobados del semestre activo; los reportes se separan por semestre y, si alguno deja de estar visible, no se declara eliminado ni se sobrescribe la revisión previa. Validado con las cinco materias reales 2026-2 y transición futura sintética. **Pendiente:** persistir el alta en V2 bajo protocolo compartido, coordinación de la selección entre PCs y programación automática. La asociación MAT-ST ↔ Canvas 82892 está documentada pero aún no guardada en la celda `curso_externo_id` del workbook. Detalle: `21-semestres-y-matricula.md`.

### Bloques académicos y materia transversal — 2026-10-08

**Confirmado e implementado en el registro local:** el estudiante cursa el semestre en dos bloques consecutivos, y algunas materias pueden continuar a través de **ambos**. El registro migró a esquema 2: `active_block` independiente de `active_semester`; por curso, `blocks` puede ser `[1]`, `[2]` o `[1,2]`. El inglés no se divide en dos cursos: `MAT-ING3` / Canvas `91003` abarca ambos bloques. Las otras cuatro materias registradas de 2026-2 se asignaron al bloque 2, que está activo actualmente. No se conoce aún el inventario completo de materias exclusivas del bloque 1.

**Confirmado funcionalmente:** `daily` exige visibilidad en Canvas solo de los cursos pertenecientes al bloque activo; mantiene histórico e informes separados por bloque, permite añadir materias nuevas al mismo semestre y conservar la firma de una materia transversal cuando cambia de bloque. `onboard` solicita bloques por curso, `course-blocks` permite ampliar una pertenencia existente sin duplicación y `block-set` cambia el bloque activo sin alterar el semestre. Migración real e integración Canvas comprobadas; ninguna escritura al Excel.

**Avance confirmado (2026-10-08):** `bloques` y `bloque_activo` ya están publicados en el Excel canónico bajo protocolo completo de lock, SHA, checkpoint y revalidación. Se corrigió el ID Canvas faltante de MAT-ST; `blocks-refresh` puede reconstruir el registro en otro PC desde esa base. Hito y SHA en `22-publicacion-bloques-v21.md`. **Implementación posterior (2026-10-08):** el alta canónica de materias/semestres nuevos ya dispone de flujo `onboard → previsualizar → PUBLICAR` con verificación Canvas en vivo y protocolo de Excel/Drive; probado con matrícula sintética, **sin alta real en este semestre**. Detalles en `23-alta-canonica-materias.md`. **Pendiente:** primera matrícula real nueva, prueba multi-PC, calificaciones tardías y programación autónoma. La expectativa de seguir cursando inglés transversal en semestres futuros no autoriza asignar `[1,2]` automáticamente a materias aún no inscritas. Ver `docs/21-semestres-y-matricula.md`.

## Decisiones planificadas / pendientes

### Persistencia de referencias externas múltiples

**Implementado y publicado (2026-10-08):** el assignment principal conserva `objeto_externo_tipo/objeto_externo_id` y la columna nueva `referencias_externas` de `02 - Actividades` guarda otros IDs con campos neutrales `sistema/tipo/id`, incluso GBD E1 `quiz_id` y SO2 E2 `discussion_id`. Se verificaron cuatro referencias reales. Es la solución mínima V2.1; ampliaciones solo si nuevos LMS/casos reales lo exigen. Véase `24-canvas-actividades-automatizacion-v1.md`.

### Vocabulario de estados

Los casos reales ya usan `disponible`, `bloqueada_por_fecha`, `cerrada`, `pendiente`, `entregada`, `sin_calificar` y `calificada`. Son valores operativos observados; todavía falta decidir si este vocabulario queda como enumeración contractual universal o si necesita categorías adicionales.

### Fecha/hora efectiva de envío (`submitted_at`)

**Implementado y publicado (2026-10-08):** la nueva columna `fecha_envio_real` en `02 - Actividades` conserva el timestamp efectivo del envío (convertido desde UTC a `America/Bogota` con serial Excel explícito). Se verificó el envío de Inglés III del 2026-09-26 12:05, diferente de apertura/vencimiento/cierre, sin inferir un `due_at` ausente. Se completaron cinco fechas reales. Véase `24-canvas-actividades-automatizacion-v1.md`.

### Relación Actividad–Recurso

SER ya confirmó que E2 y E3 trabajan sobre el mismo artefacto mutable. La relación muchos-a-muchos existe conceptualmente, pero **todavía no se crea `07 - Actividad-Recurso`** porque el Documento de Google real del estudiante aún no tiene URL/ruta verificada. Se decidirá la estructura mínima al disponer del artefacto real o de otro caso verificable.

### Perfil opcional de idiomas

Idioma, nivel, habilidades, gramática y vocabulario siguen fuera del núcleo V2. Inglés III se importará primero usando el modelo común.

### Persistencia del acceso a grabaciones institucionales

La publicación del workbook ya quedó resuelta por Google Drive API. Sigue pendiente cerrar la experiencia de autorización de las grabaciones institucionales para que no requiera reautorizaciones frecuentes. El acceso a esas grabaciones continúa usando OAuth de usuario en solo lectura; no se sustituye por la identidad técnica del Drive personal porque son cuentas y permisos distintos.

### Flujo multi-equipo — validación real

**Prueba diferida expresamente por decisión del estudiante (2026-10-08):** por ahora Academic Manager opera solo desde PC Diego. **No es criterio de cierre de Etapa 1 para este entorno.** Cuando se incorpore el segundo PC, deberán probarse:

- preparación del PC de casa;
- adquisición del lock desde un equipo y bloqueo efectivo del otro;
- entrega del turno después de publicación/revalidación;
- recuperación ante lock abandonado real.

### Academic Manager Capture

Objetivo aceptado: flujo sencillo para obtener transcripciones y registrarlas.

**Confirmado e implementado en V0:**
- código persistido en `capture/` dentro del repositorio;
- entrada por Google Drive, archivo local o transcripción existente;
- OAuth local Drive de solo lectura;
- jobs recuperables;
- FFmpeg + WhisperX `large-v3`;
- CUDA con fallback;
- fragmentos de prueba;
- destino `<semestre>/<materia>/Clases/<nombre de la grabación>/`;
- persistir `metadata.json`, `transcript.md`, `transcript.json` y `analysis.md`;
- descargar audio/video solo temporalmente;
- eliminar automáticamente el temporal después de validar las salidas;
- no conservar por defecto una copia audiovisual permanente;
- saneamiento de nombres y protección frente a colisiones;
- publicación de artefactos de texto con relectura y SHA-256;
- publicación segura del workbook por Drive API;
- registro idempotente de clases en `03 - Clases` respetando el protocolo completo.

**Primera validación completa:** SER 2026-10-01, ~57:39, 777029390 bytes, 111 segmentos; transcripción y análisis validados y publicados. La estructura de `analysis.md` quedó confirmada con orientación al estudiante: lo esencial, lo que debe saber, recordar y hacer, evaluaciones, decisiones/cambios, ejemplos, recursos, dudas, próxima clase, importancia secundaria, mapa temporal y fiabilidad.

**Segunda validación completa:** GBD 2026-10-01, ~43:36, 269179845 bytes, 90 segmentos, análisis revisado y cuatro artefactos publicados. Clase `CLS-GBD-20261001-01` registrada mediante flujo completo del workbook, re-descarga por Drive API y validación con Excel; hash actual `9b4686373de57881b36a5712b70df263f98272a5392508c662fd2a21189962c8`, `status=free`, `lock=null`. La conexión MCP cayó por error remoto HTTP 525, pero el job continuó y terminó localmente.

**Estabilización posterior implementada:** historial local de etapas `events.jsonl`, `--status latest` y reintentos acotados de `atomic_json` para bloqueos Windows, validados. **Incremento adicional (2026-10-08):** nuevo `progress.json` para porcentajes VAD de WhisperX y ETA **solo orientativa del ASR** mediante callback real de la librería, vista `--status all`/`--watch`, y acción `academic_access capture-jobs` de solo lectura; no hay un heartbeat universal para descargas/análisis ni un porcentaje de la publicación. **Pendiente:** validar el porcentaje en una transcripción real iniciada con el nuevo código. Ver `30-capture-monitoreo-progreso.md`.

**Pendiente cerrar:**
- interfaz/experiencia final;
- evolución fina de `metadata.json` y contraste de la estructura de `analysis.md` con más materias, sin reabrir su enfoque base orientado al estudiante;
- **Parcialmente implementado:** progreso persistente y ETA orientativa de WhisperX; seguir evaluando heartbeat genérico y captura de stdout/stderr de otras fases largas solo si se necesitan. El monitor no infiere actividad a partir de un evento antiguo;
- si reaparecen bloqueos persistentes, determinar el proceso que mantiene abierto el archivo; se implementaron reintentos atómicos limitados y se comprobó el escenario Win32 sin `FILE_SHARE_DELETE`, sin atribuir el caso pasado a un proceso concreto;
- autorización institucional estable a largo plazo.

### Projects de ChatGPT

**División de roles confirmada:** Project de **Desarrollo** (este), futuro **Coordinador** independiente y futuros Projects especializados por materia. La continuidad persistente reside fuera de los chats. Las **plantillas iniciales** del Coordinador y de materias ya están preparadas en `academic_projects/templates`, junto con un renderizador que obtiene los identificadores de matrícula real. **Pendiente** instalarlas y comprobar acceso y funcionamiento en esos Projects; no se ha habilitado su comunicación directa. Ver `26-roles-coordinador-desarrollo-materias.md` y `29-preparacion-chatgpt-projects.md`.

### Procesamiento de clases

Ya quedaron validados en uso real:
- nombres/rutas de artefactos por grabación;
- persistencia de transcripción y análisis fuera del workbook;
- una plantilla mínima de análisis para V0.

Sigue pendiente:
- contrastar la plantilla con otras materias/tipos de clase;
- decidir qué conocimiento se consolida entre clases;
- decidir cómo se refleja ese conocimiento agregado en el estado de una materia.

### Comandos conversacionales

`/class`, `/study`, `/task`, `/exam`, `/status` y `/close` siguen siendo propuestas, no contrato del sistema.

## Pendientes prioritarios

**Prioridad de V1 aprobada en octubre de 2026:** Etapa 1 Canvas Sync ↔ base, Etapa 2 conocimiento académico, Etapa 3 asistentes, Etapa 4 Dashboard, Etapa 5 uso sencillo/Capture y Etapa 6 pruebas integrales. **La Etapa 1 quedó funcionalmente cerrada para PC Diego el 2026-10-08:** 31 actividades registradas, decisiones persistidas, calificaciones/fechas/referencias verificadas y `daily --apply` programado diariamente a las 08:00 y 18:00 (ejecución de Windows comprobada). No se exigió validar dos PCs. Ver `24-canvas-actividades-automatizacion-v1.md`.

1. **Etapa 2: núcleo mínimo implementado y probado en este PC** (2026-10-08): índice local de 133 fuentes/63 vínculos, contrato JSON de lectura, y `sync-study` incremental con comprobación hash y revalidación remota cada siete días; la segunda consulta real reutilizó los 30 PDF y descargó cero. **Pendiente operativo:** validación de la cadena nueva completa desde Task Scheduler (la tarea y el script actualizado están comprobados). **Otras limitaciones no bloqueantes:** Inglés III files 403, tres lecturas extensas para extraer por capítulos solo cuando se necesiten. La síntesis conversacional sigue siendo responsabilidad del futuro Coordinador/Projects de materia, no del núcleo. Véanse `25`, `26`, `27` y `28-actualizacion-incremental-materiales.md`.
2. **Etapa 3 (preparación técnica implementada):** plantillas y renderizador de instrucciones canónicas, más plantilla de Issue y búsqueda GitHub verificada. **Siguiente:** crear el Project Coordinador independiente y un Project piloto por materia, instalar las instrucciones, comprobar acceso autorizado a `academic_access` y GitHub y validar el circuito con una necesidad real; sin issues ficticios.
3. **Etapa 4:** hacer útil `00 - Dashboard` calculando a partir de datos reales.
4. **Etapa 5:** simplificar UX de Capture; el primer monitor de porcentaje/ETA ASR y consulta del Coordinador ya existe en código, falta validar una transcripción instrumentada real y considerar barras de otras fases según uso.
5. **Etapa 6:** pruebas integrales y fiabilidad del conjunto, con multi-PC **solo cuando el estudiante decida volver a utilizarlo**.
6. **Mantenimiento no bloqueante de Etapa 1:** primera matrícula nueva real, recuperación avanzada de locks, avisos proactivos externos (actualmente logs/estado local), y seguimiento de cursos ya inaccesibles en Canvas. No considerar complementarias como obligaciones ordinarias sin evidencia.

## Pendientes por materia

### Gestión de Bases de Datos

E1 ya está importado. La grabación inicial del 1 de octubre ya fue procesada, publicada y registrada como `CLS-GBD-20261001-01`; su análisis resume también el cambio anunciado por el docente para E3 (actividad en inglés en lugar del curso de Cisco), **pendiente de contrastar con Canvas**.

Siguen pendientes: zona horaria de Canvas, conversión institucional de puntos, otras grabaciones que aparezcan, enunciado oficial específico del Eje 3, Turnitin cuando corresponda y opciones reales de entrega al abrirse cada actividad.

### SER (BE-IT)

Importados E2/E3, sus guías y entregables acumulativos. La grabación del 1 de octubre fue descargada temporalmente, transcrita, analizada, publicada y registrada en `03 - Clases` como `CLS-SER-20261001-01`. Pendientes adicionales: URL/ruta real del Google Doc cuando exista, forma mínima de la relación Actividad–Recurso, enunciado/modalidad del E4, discrepancias de rúbricas, pertinencia de archivos de propiedad industrial, horario definitivo, zona horaria y escala institucional.

### Sistemas Operativos II

Importados E2 y E4, la guía de E4 y sus dos entregables. Pendientes: consigna/rúbrica/cierre del foro E2 cuando se habilite, tamaño/modalidad de equipo E4, opciones reales de entrega, zona horaria, conversión institucional y realización/grabaciones de sesiones.

### Inglés III

Importados HW semana 4 / unidad 2 y el oral con modalidades alternativas. Pendientes: examen final cuando aparezca, tratamiento/calificación del oral en vivo después del cierre Canvas, peso exacto del oral frente a la redacción del acta/consigna, zona horaria, discrepancias de fechas/temas, reproducción de grabaciones y posible campo estructurado para `submitted_at`. El perfil específico de idiomas sigue siendo opcional y no forma parte del núcleo V2.

## Regla de mantenimiento

Cuando un pendiente quede resuelto:

1. actualizar el documento temático correspondiente;
2. mover la decisión aquí a **Confirmadas** si aplica;
3. actualizar `07-estado-actual.md` si ya cambió el sistema real;
4. evitar documentación duplicada o contradictoria.

### Validación real de Capture con análisis del Coordinador (2026-10-12)

**Confirmado e integrado:** la primera incorporación real mediante `--attach-analysis` se completó para SO2 (job `83ac7042104d4ce6bb793543714f6fb5`, clase `CLS-SO2-20260929-01`) y Sistemas Transaccionales (job `e0eb990002ab423b91d06ebac3dec9c0`, clase `CLS-ST-20260930-01`). Ambos análisis quedaron `reviewed`, cuatro artefactos publicados y dos registros canónicos confirmados; el estado compartido quedó `free`, sin lock y con SHA publicado/local `4505ff3be59ad3a366243ec41afbcff5018d705e3a27701ac4e8750b733ad369`.

**Corrección adicional confirmada:** el registrador heredado exigía `job.analysis_status` iniciado en `completed`, incompatible con la nueva metadata `reviewed`. El adaptador transmite `completed_reviewed` exclusivamente después de validar los archivos; 13 pruebas de análisis pasaron. **Ya no está pendiente** la primera publicación y registro reales de esos dos informes. No constituye una revisión humana adicional del audio ni una auditoría semántica automática. Ver `docs/05-capture.md`.

### Despliegue inicial de operaciones para el Coordinador (2026-10-12)

**Confirmado:** nueva interfaz `academic_operations` integrada mediante PR #4 y desplegada en Diego, junto a configuración local restringida. La vista previa real con `scripts/academic-act.cmd preview` verificó lectura sobre SO2 y Sistemas Transaccionales ya integradas, sin escrituras. Las 9 pruebas específicas pasan; la suite general de desarrollo terminó con 165 casos, 2 omitidos.

**Confirmado por prueba reportada desde el Project Coordinador (2026-10-12):** la herramienta remota aceptó `scripts\academic-act.cmd preview --job 83ac7042104d4ce6bb793543714f6fb5` y devolvió JSON válido con salida 0. La consulta identificó correctamente la clase SO2 ya registrada; indicó `refresh_index` como paso restante. No hubo aprobación, `complete` ni mutaciones. **Pendiente real:** confirmar la autorización y ejecución de `complete` para un job nuevo que requiera escritura, tras revisión y aprobación expresa del estudiante. No afirmar que el permiso de publicar ya quedó demostrado. Véase `docs/31-operaciones-coordinador.md`.

### Fallo de finalización desde Coordinador: issue #5 (2026-10-12)

**Confirmado:** el Coordinador informó de dos fallos `OPERATION_UNAVAILABLE` durante `complete` sobre jobs nuevos, pese a `preview` válido y aprobación explícita. La reproducción aislada en Diego sobre una copia de los artefactos reales finalizó correctamente, y una prueba controlada alcanzó el preintercambio sin modificar el job original. **Implementado en código:** errores estructurados por etapa y reintentos acotados de renombrado frente a `PermissionError`; pruebas focalizadas (27) exitosas. No afirmar que se demostró el tipo del error histórico.

**Pendiente operativo:** probar una finalización autorizada desde el Project Coordinador tras el despliegue, con `preview` renovado; comprobar publicación, registro e índice, y respetar bloqueos remotos. Hasta esa validación las tres clases pendientes permanecen sin integración por este flujo. Ver [docs/31](31-operaciones-coordinador.md) y [issue #5](https://github.com/Ds0957/Academic-Manager/issues/5).

### Issue #5 — fallback transaccional para Windows (2026-10-12)

**Confirmado como implementación técnica:** el bloqueo Windows 5 informado por el Coordinador ocurre durante `attach_analysis`; las pruebas de I/O temporales en Diego demuestran que no existe una denegación general de escritura en los directorios afectados. Se añadió un fallback limitado a rechazo del renombrado del directorio de artefactos: copia original validada, diario de dos modos, reemplazo exclusivo de `analysis.md` y `metadata.json`, verificación SHA y recuperación, con subetapas sanitizadas. Esta es una **redefinición interna del intercambio atómico acordado** para mejorar compatibilidad Windows; no modifica autorización, fuentes académicas ni publicación/registro.

**Pendiente:** aprobación y nueva ejecución desde Coordinador sobre un job pendiente real, verificando incorporación, Drive, Excel y `health.fresh=true`. El error histórico no se identificó en una llamada a Windows concreta; no afirmar que el fallback ya lo solucionó en producción. Ver `docs/31-operaciones-coordinador.md`.

### Cierre del defecto #5 y continuidad del Coordinador (2026-10-12)

**Confirmado, aceptación real completada:** el flujo `academic_operations preview → complete` de la versión `46ac096` fue ejecutado desde el Project Coordinador con autorización específica para Sistemas Transaccionales del 8 de octubre. Se incorporó el análisis `reviewed`, se publicaron los cuatro artefactos, se registró `CLS-ST-20261008-01` sin duplicar la clase del 30 de septiembre y se actualizó el índice. Desarrollo contrastó en Diego `capture-jobs`, `classes --materia MAT-ST` (2 entradas) y `health` (`fresh=true`, sin advertencias). Esto **resuelve el criterio operativo del issue #5**; no se afirma una explicación definitiva del Win32 5 histórico.

**Pendiente fuera del issue #5:** el Coordinador debe completar de forma independiente las clases GBD del 8 de octubre y SO2 del 6 de octubre, cada una con análisis contrastado, `preview` vigente y aprobación específica. Sus jobs continúan `pending`, sin publicación ni registro. La automatización de mantenimiento del índice en otros flujos sigue siendo una mejora separada; no reabrir el defecto #5 sin una regresión reproducible.

### Projects de materia orientados a trabajos (2026-10-12)

**Decisión confirmada por solicitud del estudiante:** ampliar los Projects de materia desde tutoría y estudio hacia **desarrollo de trabajos, revisión de consignas y rúbricas, resolución de ejercicios, creación de entregables y preparación de evaluaciones**. Se implementó en `academic_projects/templates/materia.md`. No es autorización para manipular Excel, Canvas, registros o archivos canónicos desde los Projects de materia.

**Pendiente:** crear los cinco Projects manualmente, pegar la plantilla parametrizada y validar el acceso real a sus materiales. El Coordinador conserva la organización global y el estado canónico; no almacenar estados de actividades sujetos a cambios en instrucciones estáticas.

### Fase 1 de la integración Coordinador–materias (2026-10-25, issue #16)

**Decisión:** instrucciones cortas y estables y el contexto en la base; cada Project de materia empieza con `subject-brief`. **Fuera de alcance, sin cambios:** `--course-id` por defecto 82862, la comprobación de informe heredado en `routine.py:178` y las constantes de migración V1 de `canvas_sync/semester.py`. **Pendientes:** fase 2 (decisiones globales, bandeja de avances desde las materias con campo `origen`) y fase 3 (cierre de semestre y lista de comprobación de semestre en `system`).

### Material de estudio en Capture (2026-10-25, issue #17)

**Decisión del estudiante:** un video de YouTube o un audio/video local es **«Material de estudio (Recomendado)»**, no una clase; el texto sale de **«Subtítulos manuales, si no WhisperX (Recomendado)»**. yt-dlp queda fijado a una versión exacta (`yt-dlp[pin,pin-deno]==2026.10.21`); se actualiza según docs/05. **Fuera de alcance por ahora:** `music.youtube.com`, `youtube-nocookie.com`, `youtube.com/live/ID` y contenido que exige iniciar sesión.

### Academic Manager para personas no técnicas (2026-10-25, issues #20–#22)

**Decisiones del estudiante:** una instalación independiente por persona («Cada uno lleva lo suyo por separado»); el PC de la compañera es Windows sin tarjeta gráfica, así que el sistema comprueba el equipo antes de instalar WhisperX; usa ChatGPT Go, y como no está confirmado que admita conectores propios, el diseño no depende de que ChatGPT lea el PC (la app local es la interfaz principal y los Projects reciben contexto con «Copiar contexto»). **Decisiones técnicas:** el archivo de configuración se valida siempre y falla cerrado nombrando el archivo; los umbrales de la comprobación (4 GB de memoria de la tarjeta; 8 GB de RAM con margen de 0,6 GB y 4 núcleos; 12/5 GB de disco; «medium» desde 16 GB) son supuestos hasta tener mediciones reales. **Pendientes:** validar `--benchmark` con WhisperX real (la muestra sintética puede no tener segmentos de voz); los `.ps1` aún usan `C:\AcademicManagerData`.
