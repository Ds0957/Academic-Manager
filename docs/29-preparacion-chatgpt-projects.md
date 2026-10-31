# 29 — Etapa 3: preparación de ChatGPT Projects y escalamiento por Issues

**Fecha:** 2026-10-08. **Estado:** contratos y plantillas **implementados y probados localmente**. Los Projects de ChatGPT **no han sido creados**, y el acceso a los datos e incidencias desde sus propias conversaciones **no ha sido validado**. No hay un canal automático entre Projects.

## División confirmada de roles

- **Academic Manager — Desarrollo:** este espacio conserva la arquitectura, implementa cambios, pruebas, documentación, y resuelve GitHub Issues.
- **Academic Manager — Coordinador:** Project futuro, interlocutor de la vida universitaria. Consulta el núcleo verificado, ayuda a organizarse y, ante una limitación sistémica comprobada, busca y crea el Issue correspondiente.
- **Projects especializados por materia:** espacios de trabajo académico y estudio, uno por matrícula. Consultan exclusivamente su `materia_id` y sus fuentes, desarrollan trabajos, comprueban rúbricas y preparan evaluaciones, sin sustituir al Coordinador ni modificar el núcleo.

No son agentes que se envían mensajes internamente. Para mover contexto entre Projects se usan IDs del mismo sistema y el estudiante puede trasladar la referencia; no suponer lectura de chats de otro Project. Las fuentes persistentes continúan en Excel, Drive, Canvas, índices y `/docs`, **no en memorias exclusivas de ChatGPT**.

## Código y configuraciones preparadas

```text
academic_projects/
    __main__.py            # renderizador de instrucciones, solo lectura
    templates/
        coordinador.md     # propuesta para ajustes del Project Coordinador
        materia.md         # plantilla parametrizable según Excel canónico
scripts/
    project-instructions.cmd
.github/
    ISSUE_TEMPLATE/
        necesidad-o-defecto.md
```

Las plantillas no se guardan como instrucciones del Project de Desarrollo en `/docs` (separación acordada). Se conservan bajo `academic_projects/templates` como **plantillas reutilizables para Projects distintos**. El renderizador **no crea Projects**, no les asigna permisos ni escribe la base.

### Preparar instrucciones para copiar

En un terminal del PC autorizado, desde la raíz del repositorio:

```powershell
scripts\project-instructions.cmd coordinador
scripts\project-instructions.cmd materia --materia MAT-SER
scripts\project-instructions.cmd materia --materia MAT-GBD
scripts\project-instructions.cmd materia --materia MAT-ING3
scripts\project-instructions.cmd materia --materia MAT-SO2
scripts\project-instructions.cmd materia --materia MAT-ST
```

Para generar todo de una vez (Coordinador y todas las materias del semestre activo), listo para pegar:

```powershell
scripts\project-instructions.cmd all
scripts\project-instructions.cmd all --out C:\AcademicManagerData\temporales\projects-2027-1
```

`all` escribe un archivo por Project (`coordinador.md` y `materia-MAT-....md`) en `--out`, por defecto `C:\AcademicManagerData\temporales\instrucciones-projects`, sobrescribiendo los de una ejecución anterior, e imprime la ruta de cada archivo. Genera todo antes de escribir: si una materia no valida, no escribe nada (`NO_GENERADO`, salida `2`). `--out` solo vale con `all`, y `all` no admite `--materia`. `coordinador` y `materia --materia MAT-...` siguen imprimiendo el texto como antes.

Para materias, `academic_projects` consulta `academic_access.subjects`: no acepta un ID desconocido y extrae título, semestre y bloques de la **base canónica vigente**. Inglés III `MAT-ING3` aparece **una sola vez** con bloques `1 y 2`; no se fragmenta artificialmente en dos Projects.

Los textos son propuestas editables para **pegar en Ajustes del proyecto → Instrucciones del proyecto** de los Projects que el usuario cree. Si cambia la matrícula/semestre, se generará la plantilla de una nueva asignatura real y se actualizará manualmente el texto del Project pertinente. **No deben tratarse como instrucciones ya instaladas en ChatGPT.**

## Preparación práctica de conexiones (pendiente de hacer dentro de cada Project)

1. Crear manualmente el Project **Academic Manager — Coordinador** en ChatGPT; agregar solo las instrucciones rendereadas. Para cada materia activa que se decida desplegar, crear el Project correspondiente y pegar la plantilla `materia --materia MAT-...`.
2. Comprobar dentro de **cada Project** que existe acceso autorizado a la conexión GitHub y/o Remote Desktop Commander necesaria. Estar conectado en el Project de Desarrollo no demuestra que el mismo acceso esté disponible dentro de otro Project. Las capacidades/acciones de apps varían con permisos y entorno.
3. Desde Coordinador, solicitar explícitamente `academic-read.cmd capabilities`, `health`, `subjects` y luego `activities --materia MAT-ING3`. Validar 2026-2, bloque 2, Inglés III transversal y las nueve actividades existentes en la captura del 2026-10-08, **sin asumir que esa cifra seguirá igual**.
4. Desde un Project de materia, consultar `subject`, `classes`, `search` y `source` limitados a su `materia_id`. Pedir que cite el `fuente_id`, que no invente el texto ausente y que no confunda `contexto_del_mismo_eje` con consigna. Preguntar cómo nombrar un trabajo: debe aplicar la decisión de `notes --tipo decision` citando su `nota_id`, o declarar que no pudo consultarla.
5. Validar acceso de lectura a `Ds0957/Academic-Manager` y **búsqueda de issues existentes** antes de cualquier Issue real. La primera prueba del conector GitHub aquí permitió `search_issues` en este repositorio y no encontró issues abiertos en ese instante (2026-10-08). **No se ha probado permiso de escritura** ni creado un issue ficticio.
6. Validar después, cuando surja un defecto real, el ciclo de GitHub: consulta de `/docs` y duplicados → `create_issue` si corresponde → enlace verdadero → evaluación e implementación en Development → cierre y documentación. No validar el sistema contaminando el backlog con defectos inventados.

**Extensión de interfaz de consulta (2026-10-08):** `academic_access capture-jobs` permite al Coordinador informar del progreso de las grabaciones, con porcentaje **solo de ASR**, ETA orientativa y publicación/registro separados. Se añadieron instrucciones en `academic_projects/templates/coordinador.md`, pero las instrucciones que el estudiante ya pegó en el Project Coordinador **deben actualizarse manualmente**; no se propagan desde GitHub. Ver `30-capture-monitoreo-progreso.md`.

## Política de issues del Coordinador

**Solo crear Issues para problemas de producto o mejoras técnicas necesarias**, no para conversaciones, dudas de clase, complementarias, tareas pendientes del estudiante o fallos temporales aún sin diagnóstico. Antes: usar errores de `academic_access` como `INDEX_STALE`, `NO_TEXT`, `NOT_FOUND` y `SOURCE_CHANGED` y el documento `27-contrato-consulta-academica.md` para diagnosticar.

El issue debe incluir problema observado, comportamiento esperado, reproducción sin datos confidenciales, impacto, alternativas revisadas, propuesta **no aprobada** y criterio de aceptación. `.github/ISSUE_TEMPLATE/necesidad-o-defecto.md` guía su creación manual desde GitHub. El conector puede crear issues mediante título/cuerpo: el Project Coordinador debe seguir igualmente esa estructura. Prohibido adjuntar tokens, URLs firmadas, registros con calificaciones privadas, transcripciones completas y secretos.

El Project Coordinador no edita repositorio, no desbloquea el Excel y no «repara» al crear el issue. Si no tiene conexión/permisos, solo prepara borrador e informa. **Desarrollo es el único espacio responsable de aprobar soluciones, implementarlas, probarlas y actualizar `/docs`.**

## Qué se validó en el equipo Diego

- Renderizado real para `MAT-SER` con nombre, `2026-2` y bloque `2`; renderizado independiente de Coordinador que no necesita acceso al Excel.
- Catálogo real de cinco materias: MAT-GBD, MAT-SER, MAT-SO2, MAT-ST y MAT-ING3 con bloques 1/2; el renderizador no duplica una asignatura transversal.
- Siete pruebas nuevas de plantillas sin placeholders, IDs desconocidos, falta de materia, separación de responsabilidades, argumentos CLI y plantilla de issue con exclusión de evidencia privada; **suite total `scripts/dev-check.ps1`: 139 pruebas ejecutadas, 2 omitidas, sin fallos (DEV_CHECK_OK)**.
- Conector GitHub: **búsqueda de issues operativa**, no creación. No se ejecutaron operaciones de escritura GitHub Issues durante esta preparación.
- No se crearon Projects, sesiones, nuevos datos académicos, ni modificaciones del Excel/Drive/Canvas.

## Siguiente paso técnico/operativo

**Primer despliegue real del Coordinador en ChatGPT:** creación manual del Project, conexión y pruebas iniciales con el contrato de lectura y permisos, sin tratar el Proyecto de Desarrollo como sustituto. Luego desplegar un Project piloto de materia (preferiblemente una con clase procesada) para validar estudio con citas, antes de crear más Projects. Priorizar correcciones por uso real vía GitHub Issues. No abrir un servicio HTTP público ni hacer depender el núcleo de una suscripción de ChatGPT.

## Actualización de contrato del Coordinador (2026-10-12)

`academic_operations` añade `preview` y `complete` para clases de Capture mediante `scripts/academic-act.cmd`. La plantilla del Coordinador incluye lectura, autorización explícita, cierre controlado y la obligación de respetar rechazos de herramientas. **No actualiza automáticamente las instrucciones ya pegadas en el Project Coordinador**. Las pruebas sintéticas de código no prueban que este Project disponga de permisos para ejecutar la acción. Pendiente conexión/prueba efectiva desde allí. Ver `31-operaciones-coordinador.md`.

## Revisión de la plantilla de materias (2026-10-12)

A petición del estudiante, la plantilla `academic_projects/templates/materia.md` pasa de asistente principalmente tutorial a **espacio especializado para desarrollar trabajos y pendientes reales**, además de estudiar. Incluye consulta selectiva de actividades, criterios oficiales, investigación, generación y revisión de entregables, y traslado de avances mediante referencias al Coordinador. No conserva listas paralelas de obligaciones ni implica nuevas autorizaciones de escritura.

Cada Project de materia continúa generado con el `materia_id` canónico a través de `scripts/project-instructions.cmd materia --materia MAT-...`. Inglés III sigue siendo un Project único para bloques 1 y 2. **Pendiente operativo:** crear los Projects en ChatGPT, pegar las instrucciones y comprobar las conexiones y permisos individualmente; la actualización del repositorio no configura Projects automáticamente.

## Decisiones registradas en Projects de materia (2026-10-25, issue #13)

El Project de SO2 propuso una nomenclatura propia aunque el Coordinador había guardado una decisión para las cinco materias: la plantilla de materia no incluía `notes`. Ahora `materia.md` consulta `notes --materia MAT-... --tipo decision` antes de proponer nombres, formatos o criterios; aplica la decisión vigente citando su `nota_id`, ante contradicciones toma la de `creado_utc` más reciente y lo señala, declara cuando no pudo consultar y presenta cualquier alternativa como propuesta, no como acordada. Distingue el **título académico** del trabajo del **nombre del archivo** (códigos, versión, extensión), que sin decisión registrada es solo sugerencia.

No hay alcance global: cada nota pertenece a una materia, así que el Coordinador guarda una decisión transversal en cada materia afectada. **Pendiente operativo:** regenerar las instrucciones con `scripts\project-instructions.cmd` y pegarlas de nuevo en el Coordinador y en los cinco Projects de materia.

## Integración Coordinador–materias, fase 1 (2026-10-25, issue #16)

- **`subject-brief`:** `scripts\academic-read.cmd subject-brief --materia MAT-...` resume en un solo JSON una materia: pendientes de `today`, estados de entrega, tareas abiertas, decisiones vigentes y resúmenes de clase recientes ([27-contrato-consulta-academica.md](27-contrato-consulta-academica.md)). La plantilla de materia indica empezar cada chat con esa orden; conserva las reglas de decisiones del issue #13.
- **`project-instructions all`:** una sola orden escribe los archivos del Coordinador y de todas las materias activas (ver «Preparar instrucciones para copiar»). Al empezar un semestre basta con regenerarlos y pegarlos.
- **Semestres nuevos:** el renderizador acepta semestres `-1`, `-2` y `-3`, como el registro de semestres, y la plantilla del Coordinador ya no nombra materias concretas (una materia transversal a ambos bloques aparece una sola vez). **Pendiente operativo:** regenerar con `scripts\project-instructions.cmd all` y pegar de nuevo las instrucciones del Coordinador y de cada materia.

## Capture y GitHub en los Projects de materia (2026-10-25, comentario en #17)

El Project de Gestión de Bases de Datos necesitaba el texto de un video de YouTube, revisó por su cuenta una versión antigua de Capture, concluyó que no era compatible y lo publicó como comentario en el issue #17. La plantilla de materia ahora indica pedir al Coordinador (o al estudiante) procesar videos y audios con Capture como material de estudio de la materia y buscarlos después con `search`/`source`, sin revisar el código de Capture, y prohíbe publicar en GitHub: el diagnóstico se entrega al Coordinador. **Pendiente operativo:** regenerar con `scripts\project-instructions.cmd all` y pegar de nuevo las instrucciones de cada Project de materia.
