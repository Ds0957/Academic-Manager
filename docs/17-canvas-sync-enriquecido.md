# 17 — Canvas Sync V0.1: extracción académica enriquecida

**Estado al 2026-10-07:** implementación local y primera ejecución real completadas en el equipo `Diego`; se conserva el extractor V0 anterior sin cambios.

## Contrato de seguridad

**Decisión confirmada:** el conector de Canvas es estrictamente de **consulta**. El único cliente de red reutilizado es `canvas_sync.api.Client`, que emite exclusivamente peticiones HTTP `GET` sobre `https://areandina.instructure.com/api/v1/`, valida el host, rechaza redirecciones, corta paginación circular y tiene límites de respuesta. `BudgetClient` limita V0.1 a **180 solicitudes por ejecución**; ante cuota agotada, 429, red insegura o error estructural se aborta **sin publicar el conjunto parcial recuperado**. Los recursos opcionales HTTP 403/404 se representan expresamente como `forbidden`/`not_found`, nunca se interpretan como «no existen» ni se convierten silenciosamente en listas vacías.

El programa **no** contiene operaciones POST, PUT, PATCH ni DELETE hacia Canvas. **No** inicia exámenes, no responde cuestionarios, no modifica tareas, no cambia configuración, no enumera compañeros, no consulta sus entregas ni descarga binarios o URLs externas. **Cautela:** Canvas puede conservar trazas de acceso y algunos recursos institucionales podrían actualizar indicadores de lectura al consultarlos; `GET` por sí solo no garantiza ausencia de todo efecto indirecto del servidor. V0.1 no solicita respuestas de exámenes ni entradas individuales de foros.

La credencial personal sigue protegida por DPAPI en `C:\AcademicManagerData\control\canvas-sync\canvas-token.dpapi`; ni la credencial ni su texto plano se imprimen o guardan en JSON de evidencia. Las referencias HTML son texto no ejecutable: se extrae texto y enlaces desde HTML sin ejecutarlo, con límites de tamaño. Archivos Canvas: se guardan nombre, ID, tipo, tamaño, estado y URL de navegación cuando existe, **no URL de descarga firmada**. Todo se guarda solo en runtime local. GitHub conserva código y documentos, no contenido académico.

## Uso

En el equipo autorizado, desde la terminal:

```powershell
& "C:\AcademicManagerDev\Academic-Manager\scripts\canvas-sync.cmd" sync-rich --all-active
```

Para una sola materia:

```powershell
& "C:\AcademicManagerDev\Academic-Manager\scripts\canvas-sync.cmd" sync-rich --course-id 82862
```

`--all-active` consulta cursos activos expuestos por la cuenta; por seguridad rechaza más de 12 en una sola ejecución. Los cinco actuales quedaron resueltos como GBD (`82862`), Sistemas Transaccionales (`82892`), Inglés III (`91003`), SO2 (`91004`) y SER (`91005`).

### Datos locales persistidos

Sin sobrescribir `procesamiento/canvas-sync/<id>/latest.json` de V0:

```text
C:\AcademicManagerData\procesamiento\canvas-sync\v01\
├── 82862\
│   ├── latest.json
│   └── snapshots\<UTC>-<hash>.json
├── 82892\...
├── 91003\...
├── 91004\...
├── 91005\...
└── user\
    ├── latest.json
    └── snapshots\...
```

Cada snapshot conserva `sha256` SHA-256 del contenido JSON normalizado, `captured_at_utc`, esquema 2, procedencia y datos. Si el hash del contenido es idéntico, no crea otro snapshot. La escritura local es atómica; el lock de proceso protege actualizaciones por carpeta. Primero se recopilan todos los cursos y el planificador/inscripciones; solo después se publican los JSON, evitando lotes parciales por fallos de red. Esto **no** constituye una transacción ACID multiarchivo ante corte de energía durante la publicación.

### Categorías consultadas

- Perfil mínimo solo para confirmar identidad (no se guarda el perfil).
- Curso: ID, nombre, código, zona horaria, fechas, estado.
- Assignments: IDs externos, apertura, vencimiento, cierre, puntos, tipo de entrega, grupo, estado, descripción HTML convertida a texto, enlaces y rúbrica (criterios, ratings), `discussion_id`/`quiz_id`, propia `submission` con `score`, `grade`, `submitted_at`, `graded_at`, `late/missing`.
- Grupos de actividades: pesos y referencias por ID.
- Módulos e ítems: títulos, tipos, vínculos a páginas/archivos/tareas y requisitos publicados.
- Páginas: inventario y detalle textual de páginas accesibles; front page. No se siguen enlaces externos/iframe.
- Ficheros: **metadatos** (no descarga), señalando rutas restringidas.
- Foros: títulos, descripción/consigna, fechas y relación con assignment; no entradas/respuestas de compañeros.
- Quizzes: título, instrucciones, fecha, tipo, número de preguntas/intentos, tiempo límite, relación con assignment; **no preguntas ni intentos**.
- Anuncios: títulos, textos, fechas.
- Agenda del usuario: inscripciones propias (resumen de nota), `todo`, `upcoming` y `planner` en ventana dinámica desde 45 días antes hasta 150 días después de la fecha local.

La clasificación `kind_hint` es **provisional** (`nota_agregada`, `informativo`, `informativo_o_evaluable`, `complementaria`, `cuestionario`, `foro_evaluable` o `actividad_por_revisar`). `classification_confirmed=false` en todas las actividades hasta revisión; **no se pueden importar a Excel automáticamente**. Especialmente `Nota final [P1]` no es una tarea ordinaria, y los enlaces de clase o grabaciones no son necesariamente obligaciones de entrega.

## Primera validación real

El 2026-10-07 se ejecutó `scripts/canvas-sync.cmd sync-rich --all-active` con éxito, código `0`, **66 peticiones GET**. Se guardaron por separado las cinco materias y el resumen de usuario.

| Course ID | Assignments | Rúbricas integradas | Registros calificados | Archivos accesibles (metadata) |
|---|---:|---:|---:|---:|
| 82862 GBD | 5 | 3 | 0 | 13 |
| 82892 Sistemas Transaccionales | 7 | 4 | 0 | 11 |
| 91003 Inglés III | 12 | 1 | 5 | 0 — `forbidden` |
| 91004 SO2 | 9 | 3 | 0 | 12 |
| 91005 SER | 5 | 3 | 0 | 15 |
| **Total** | **38** | **14** | **5** | **51** |

Además se verificaron **18 discusiones, 18 quizzes, 6 módulos y 42 ítems**, y **5 actividades con `submitted_at`**. El snapshot de usuario recuperó las 5 inscripciones, 5 `todo`, 5 `upcoming` y 25 registros de `planner` en su ventana dinámica (diferente del intervalo fijo de la auditoría anterior). La ruta `/files` de Inglés III se guardó como `forbidden`, `/pages` como `not_found`, y `page_details` como `not_available`. La integridad SHA-256 de los seis snapshots se validó leyendo los JSON locales; la versión anterior V0 permaneció intacta.

**Validación de pruebas:** `tests/test_canvas_rich.py` comprueba HTML no ejecutable, clasificación provisional, GET exclusivos (sin quizzes/questions ni submissions de terceros), identidad, snapshots, idempotencia, `403` diferenciado, no escritura en caso de `429` y ausencia de URLs firmadas. Ejecutar suite completa con `python -m unittest discover -s tests -q`.

## Fuera de alcance / próximos pasos

- **Ya implementado como extensión posterior** el comando `pdf` para descarga **selectiva** de archivos PDF del catálogo V0.1, almacenamiento local por SHA-256 y texto extraído, con GET y descarga binaria sin compartir token. La validación de 12 PDF, sus anomalías y límites se documenta en `docs/18-recursos-canvas-pdf.md`. Sigue pendiente clasificar y relacionar los documentos con actividades de la base.
- Vistas de contenido externo en iframes/LTI, enlaces a clases y grabaciones institucionales: la API puede proporcionar un vínculo, **no su contenido**.
- Paginación adicional si aparecen muchos ítems, clasificación humana de `Nota final [P1]`, actividades informativas/complementarias y revisión de enlace tarea–quiz/foro.
- Reconcilación con workbook V2.1, reglas para referencias externas múltiples y `submitted_at`. **No existe escritura al workbook** y cualquier futura importación conservará lock, checkpoint, validación, publicación por Drive API, re-descarga y comprobación.
- Alertas autónomas de vencimiento de token, reintentos/backoff de errores de cuota y opciones de configuración de ventana del planificador. La ejecución actual aborta de forma segura en HTTP 429.
