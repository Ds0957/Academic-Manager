# 16 — Auditoría de cobertura real de Canvas API (2026-10-07)

**Estado:** exploración **confirmada** por peticiones reales autenticadas y de solo lectura desde el PC `Diego`. **No supone implementación de nuevas funciones de Sync**, ni aprobación de importación al workbook.

## Alcance y método

Cuenta Canvas del estudiante autenticada mediante la credencial **DPAPI local ya autorizada**; no se recuperó, imprimió, trasladó ni publicó el token. Se utilizó exclusivamente la API REST `https://areandina.instructure.com/api/v1` con **GET secuenciales**. No se ejecutaron POST/PUT/DELETE, intentos de examen, descarga binaria, lectura de respuestas a cuestionarios ni enumeración nominal de compañeros.

Se inspeccionaron, para las cinco materias activas identificadas, las respuestas de: cursos, tareas, grupos de tareas, módulos, ítems de módulo, páginas, archivos (solo metadata), discusiones (solo temas y consignas, no respuestas de compañeros), quizzes (metadata y descripción, no preguntas), anuncios, calendario, rúbricas, pestañas. Para GBD también se consultaron por ID las cinco tareas y su propia entrega, una página y cuatro quizzes.

Segunda batería: inscripciones del usuario, tareas pendientes, próximos eventos, planificador, grupos personales, calendario tipo tarea, progreso, carpetas raíz, conferencias, colaboraciones, grupos de curso, páginas iniciales y cobertura de calificación/entrega propia.

La auditoría guardó **solo metadatos de cobertura y algunos ejemplos académicos** (IDs/títulos, conteos, presencia de campos, longitud de descripciones y estados HTTP), no textos completos ni ficheros descargados:

```text
C:\AcademicManagerData\procesamiento\canvas-sync\
├── audit_coverage.py
├── audit-coverage.json
└── audit-coverage-secondary.json
```

Estos archivos son **locales**, no deben publicarse en GitHub ni Drive. La ejecución inicial se interrumpió antes de consultar Canvas por falta de `PYTHONPATH`; después se corrigió el entorno y la auditoría principal completó todas sus consultas. En una primera tentativa de la segunda batería hubo un error de presentación de respuesta sin `count`; se corrigió y se completó la segunda batería. No se tocaron el workbook ni su protocolo de publicación.

## Cinco cursos identificados

| Materia | Course ID | Tareas API | Ficheros listables (metadata) | Temas de discusión | Quizzes | Módulos/ítems |
|---|---:|---:|---:|---:|---:|---:|
| Gestión de Bases de Datos | 82862 | 5 | 13 | 2 | 4 | 1 / 9 |
| Sistemas Transaccionales | 82892 | 7 | 11 | 2 | 6 | 2 / 12 |
| Inglés III | 91003 | 12 | **403** | 1 | 0 | 1 / 3 |
| Sistemas Operativos II | 91004 | 9 | 12 | 9 | 4 | 1 / 9 |
| SER (BE-IT) | 91005 | 5 | 15 | 4 | 4 | 1 / 9 |
| **Total recuperado** | **5 cursos** | **38** | **51 accesibles** | **18** | **18** | **6 / 42** |

Los **38 registros de asignaciones** no equivalen a 38 obligaciones: incluyen `Nota final [P1]`, enlaces de clase, grabaciones y actividades complementarias. Se requiere un **clasificador por tipo/función antes de crear actividades internas**. La página API de `pages` devolvió 1 por materia salvo Inglés III (**404**), pero `front_page` sí respondió con objeto para las cinco materias; la discrepancia no debe tratarse como ausencia total de portada.

## Campos recuperables y limitaciones

| Recurso | Resultado real | Valor para Academic Manager |
|---|---|---|
| Assignments | 38, con IDs, tipos de entrega, due/unlock/lock, puntos y estado personal | Actividades, vencimientos, enlace, clasificación, estado |
| Descripciones HTML | 9/38 assignments tienen descripción no vacía; algunos quizzes y temas también llevan instrucciones | Consignas accesibles; HTML requiere saneamiento y vinculación de fuentes |
| Rúbricas embebidas | 14/38 assignments incluyen `rubric` con criterios; p. ej. GBD E2=5, E3=4, E4=5 | Criterios/rúbricas sin necesitar endpoint `/rubrics` |
| `GET /courses/:id/rubrics` | **403** en los 5 cursos | No se puede usar el listado general con este permiso; usar rúbrica adjunta a tarea |
| Archivos | 51 registros de metadata con tipo, tamaño, nombre, IDs y estado de bloqueo; Inglés III **403** | Inventario de guías, PDF y HTML; **ningún binario descargado** |
| Módulos | 6 módulos y 42 ítems, con `type`, `content_id`, `page_url` | Mapa de navegación y referencias cruzadas (no presume contenido de herramientas externas) |
| Discussions | 18 temas, con título, cuerpo de consigna cuando presente, fechas/ID de actividad | Foros y vínculo `assignment_id` ↔ `discussion_id` |
| Quizzes | 18, con instrucciones, número de preguntas, intentos y restricciones cuando publicados | Planificación de evaluaciones **sin abrir ni comenzar intentos ni extraer preguntas/respuestas** |
| Announcements | Listado general por curso: 1 en Inglés III, 0 en los otros cuatro | Avisos; podrían diferir del planificador o contenido embebido |
| Own submissions | GBD: 5 consultas puntuales GET `.../submissions/self` devolvieron 200 | Evidencia de entregas propias; no consultar entregas de otros estudiantes |
| Inglés III | 12 asignaciones, **5** con `submission.workflow_state=graded`, score y `submitted_at` | Calificación y hora real de entrega; prueba fuerte para V2.1 |
| Inscripciones / grades | 5 inscripciones personales recuperadas; estructura `grades` disponible en las cinco | Estado/nivel de calificación global, sujeto a permisos y valores |
| Planificador | 24 elementos entre 2026-09-01 y 2026-12-20: 16 assignment, 3 quiz, 4 announcement, 1 discussion_topic | Agenda unificada y recordatorios, con deduplicación frente a assignments |
| To-do / upcoming | 5 elementos en cada consulta | Alertas del usuario; se deben comparar con planificador para evitar duplicados |
| Calendario | 0 eventos en las consultas de calendario probadas, incluso `type=assignment` con ventana | **No** inferir que no existen vencimientos; sí existen fechas `due_at` en assignments |
| Grupos del usuario | 0 grupos personales; los listados de grupos de curso devuelven 0 | No inferir que no hay tareas grupales; son modelos distintos |
| Conferencias/progreso | Ambos endpoints consultados devolvieron **404** en las cinco materias | No concluir que no existen clases, grabaciones o avance en otros sistemas |
| Páginas iniciales | `front_page` devolvió 200 para las cinco; `pages` de Inglés III 404 | Portadas y enlaces accesibles; el contenido puede residir en `iframe` |
| Colaboraciones | Los cinco cursos respondieron 200 con 0 elementos | No concluir ausencia de documentos externos a Canvas |

**Ejemplos verificados:** GBD Eje 1 expone descripción HTML de 3698 caracteres y quiz `228076` con `question_count=15`; GBD E2/E3/E4 devuelven rúbricas de 5/4/5 criterios. Inglés III incluye asignaciones de información docente/enlace de clase y grabaciones, que **no se pueden tratar como entregas**. SO2 tiene un tema de foro vinculado con `assignment_id`, útil para múltiples referencias externas.

## Consumo API y límites

En la **primera batería instrumentada**:
- **82 solicitudes GET** y **82 categorías/consultas**, sin paginación más allá de una página en esos casos;
- HTTP **200: 75**, HTTP **403: 6**, HTTP **404: 1**;
- **0 respuestas 429**, sin evidencia de throttling durante la batería;
- suma observada de cabeceras `X-Request-Cost`: aproximadamente **18,55 unidades**;
- cabecera `X-Rate-Limit-Remaining` observada al final: **700,0** (mínimo registrado también 700,0).

No interpretar 700 como número fijo de solicitudes al día, ni como un límite mensual. **Canvas aplica una cuota dinámica por coste que se regenera con el tiempo**. Esta batería se realizó secuencialmente y sin paralelismo; la documentación oficial recomienda controlar `429` y emplear las cabeceras de coste/cuota.

Referencia: [Throttling oficial de Canvas](https://developerdocs.instructure.com/services/canvas/basics/file.throttling). La **segunda batería no instrumentó el coste/cuota por cabeceras**, por lo que no se afirma una cifra global combinada de solicitudes o consumo.

## Consecuencias para diseño — no implementadas

**Confirmado por evidencia:** Canvas API cubre una parte sustancial de las cinco materias; no debemos limitar Sync a nombre/fecha de tareas. Rúbricas adjuntas y envíos reales de Inglés III se recuperan mediante GET, y el planificador reúne recursos heterogéneos.

**Planificado para una propuesta de Sync V0.1, pendiente de aprobación e implementación:**
1. Perfil de curso completo: syllabus cuando exista, módulos/ítems, referencias a pages/files, debates y anuncios; conservar solo campos necesarios y fuente/verificación.
2. Actividades: descripción HTML saneada, rúbricas, pesos/grupos, quiz/discussion IDs, status propio, score y `submitted_at` en campos separados de apertura/vencimiento/cierre.
3. Recursos: inventario por IDs/tipos/tamaños, sin descargar indiscriminadamente archivos binarios; descarga selectiva conforme a acceso permitido y reglas de persistencia.
4. Agenda: usar `assignments` + `planner` + eventos propios, con deduplicación mediante identidad externa.
5. Clasificación: separar actividad evaluativa, elemento informativo, `Nota final [P1]`, recursos y anuncios; **nunca agregar todo como actividad pendiente**.
6. Captura de evidencia local, hashing/versiones/diffs por recurso; protección de datos, cuotas, fallos parciales y reintentos `429`; no utilizar los GET de Canvas como prueba de completitud de contenido incrustado.
7. Tras validar un caso real por materia, proponer mapeo mínimo a la base V2.1; **sin escritura automática ni modificación de `database.xlsx` hasta aprobar reglas de reconciliación y conservar el protocolo de publicación**.

**Pendientes exploratorios:** comportamiento de archivos descargables, PDFs, contenido HTML incrustado vía herramientas externas/LTI, calendarios, conferencias y grabaciones institucionales, soporte de anuncios del planificador frente al endpoint global; comprobación de cambios reales a través del tiempo y qué endpoints activan estados de lectura. Las consultas de cuentas/admin, datos personales de compañeros, respuestas de exámenes y APIs de escritura quedan **fuera de alcance**, aunque existan en el catálogo general de Instructure.
