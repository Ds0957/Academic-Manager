# Academic Manager — Documentación

Esta carpeta contiene la documentación funcional y técnica de **Academic Manager**.

Academic Manager es un gestor personal de vida universitaria orientado a organizar materias, clases, recursos, actividades, evaluaciones, transcripciones, conocimiento y estado académico, manteniendo los datos fuera de los chats como fuente de verdad.

## Estado de la documentación

Se utilizan tres niveles:

- **Confirmado**: decisión ya adoptada en el sistema actual.
- **Planificado**: dirección acordada, pero todavía no implementada o validada completamente.
- **Pendiente**: tema abierto que no debe tratarse como diseño definitivo.

## Índice

1. [Visión y alcance](01-vision-y-alcance.md)
2. [Arquitectura](02-arquitectura.md)
3. [Almacenamiento y seguridad](03-almacenamiento-y-seguridad.md)
4. [Base de datos](04-base-de-datos.md)
5. [Academic Manager Capture](05-capture.md)
6. [Integración con IA y ChatGPT](06-integracion-ia.md)
7. [Estado actual](07-estado-actual.md)
8. [Decisiones y pendientes](08-decisiones-y-pendientes.md)
9. [Exploración real de Canvas: Gestión de Bases de Datos](09-exploracion-canvas.md)
10. [Exploración de SER (BE-IT) y contraste con GBD](10-exploracion-canvas-ser.md)
11. [Exploración de Sistemas Operativos II y contraste de tres materias](11-exploracion-canvas-so2.md)
12. [Exploración de Inglés III y propuesta de perfil de idiomas](12-exploracion-canvas-ingles3.md)
13. [Flujo multi-equipo y coordinación del workbook](13-flujo-multiequipo.md)
14. [Flujo de desarrollo eficiente](14-flujo-desarrollo.md)
15. [Canvas Sync V0 — autorización local y extracción API](15-canvas-sync.md)
16. [Auditoría real de cobertura de Canvas API en las cinco materias](16-cobertura-canvas-api.md)
17. [Canvas Sync V0.1 — extracción académica enriquecida, solo lectura](17-canvas-sync-enriquecido.md)
18. [Canvas Sync — recuperación selectiva de PDF y comprobación de enlaces externos](18-recursos-canvas-pdf.md)
19. [Materiales de estudio Canvas: referentes, lecturas y sincronización por eje](19-material-estudio-canvas.md)
20. [Etapa 1 V1: reconciliación Canvas Sync y V2 en solo lectura](20-reconciliacion-canvas-v2.md)
21. [Semestres, matrícula y continuidad de cursos Canvas](21-semestres-y-matricula.md)
22. [Publicación canónica de bloques y recuperación entre equipos](22-publicacion-bloques-v21.md)
23. [Alta canónica controlada de nuevas materias y semestres](23-alta-canonica-materias.md)
24. [Etapa 1 V1: actividades y sincronización cotidiana automatizada](24-canvas-actividades-automatizacion-v1.md)
25. [Etapa 2 V1: índice académico y relaciones de fuentes](25-indice-conocimiento-academico-v1.md)
26. [Separación entre desarrollo, coordinador y proyectos de materia](26-roles-coordinador-desarrollo-materias.md)
27. [Contrato JSON de consulta académica para Projects](27-contrato-consulta-academica.md)
28. [Canvas Study: sincronización incremental y revalidación periódica](28-actualizacion-incremental-materiales.md)
29. [Etapa 3: preparación de ChatGPT Projects y flujo de GitHub Issues](29-preparacion-chatgpt-projects.md)
30. [Capture: progreso de transcripción y consulta de trabajos](30-capture-monitoreo-progreso.md)
31. [Operaciones controladas del Coordinador](31-operaciones-coordinador.md)

## Principio central

> Los chats sirven para trabajar; los archivos y la base de datos conservan el estado y la información persistente de Academic Manager.

El repositorio y `/docs` funcionan además como capa de continuidad entre chats del Project de ChatGPT: un chat nuevo debe poder recuperar desde aquí el estado necesario para continuar el desarrollo.

La documentación debe actualizarse cuando una decisión pase de idea a implementación real o cuando cambie de forma relevante el estado del sistema.
