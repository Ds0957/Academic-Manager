# 26 — Separación entre desarrollo, coordinador y proyectos de materia

**Fecha:** 2026-10-08. **Estado:** **Confirmado como decisión de arquitectura y alcance**. La infraestructura del núcleo existe parcialmente; **el Project de ChatGPT Coordinador y los Projects de materia aún no están creados ni validados**. No confundir diseño aprobado con herramienta operativa.

## Tres espacios de trabajo distintos

| Espacio | Responsabilidad | No debe hacer |
|---|---|---|
| **Desarrollo de Academic Manager** (este Project/chat) | Diseñar, implementar, probar, reparar y documentar el sistema; resolver issues en `Ds0957/Academic-Manager`; mantener `/docs`. | Reemplazar al futuro coordinador en la gestión cotidiana del estudiante. |
| **Coordinador académico** (Project ChatGPT futuro) | Interfaz conversacional central de la vida universitaria; consultar el estado real, organizar prioridades, revisar clases/actividades/recursos, orientar qué estudiar y coordinar los Projects de materia. | Programar o rediseñar el núcleo por su cuenta, inventar datos, duplicar el estado de la base en memoria del chat o saltarse protocolos de escritura. |
| **Asistente de cada materia** (Projects ChatGPT) | Espacio de trabajo y aprendizaje de una asignatura: consultar pendientes y fuentes, desarrollar actividades y entregables, explicar, practicar y preparar evaluaciones. | Mantener una base paralela de obligaciones, modificar arbitrariamente el núcleo, registrar clases por cuenta propia o asumir coordinación global. |

El **núcleo independiente de ChatGPT** proporciona datos persistentes, sincronización automatizada, extracción/verificación, índices consultables y operaciones seguras. No necesita implementar por adelantado una lógica extensa de tutor, planificación conversacional o interpretación académica: esas capacidades corresponden a los Projects de ChatGPT, alimentados por **las fuentes reales** del sistema.

## Flujo esperado de uso y evolución

```text
Excel canónico / Drive / Canvas / Capture
                  │
          núcleo Academic Manager
        (datos, índices, operaciones seguras)
                  │
           lectura / consulta / acciones
                  ▼
     Project ChatGPT Coordinador
       ├── gestiona vida universitaria
       ├── deriva estudio especializado → Project por materia
       └── detecta limitación técnica real
                  │
       consulta /docs y GitHub Issues
                  │
       si no existe solución ni issue duplicado
                  ▼
       crea issue en Ds0957/Academic-Manager
                  │
                  ▼
         Project de DESARROLLO
   evalúa, decide, implementa, prueba, documenta
                  │
                  ▼
       núcleo actualizado y validado
                  │
                  └── vuelve a usarse desde Coordinador
```

No se presupone comunicación automática entre Projects: la continuidad real queda en el repositorio, issues, base y archivos. Las integraciones o conectores necesarios deben verificarse en cada Project y permiso; una función exclusiva de cierto plan no puede ser requisito del núcleo.

## Contrato de escalamiento a GitHub Issues

**Confirmado como comportamiento objetivo del futuro Coordinador**, no como función ya configurada:

1. Intentar la operación con las capacidades existentes; revisar estado y documentación actual antes de afirmar una limitación.
2. Distinguir **problema operativo puntual** (credencial vencida, fuente temporalmente inaccesible, falta de datos/permisos) de un **defecto o necesidad de producto**. No abrir issues para preguntas rutinarias o tareas académicas normales.
3. Si el núcleo requiere corrección, mejora de experiencia, nueva fuente/integración o cambio arquitectónico, buscar issues abiertos equivalentes para evitar duplicados. Registrar en el existente si procede.
4. Abrir un issue accionable en `Ds0957/Academic-Manager` usando la integración de GitHub conectada y autorizada. **No implementar cambios estructurales en ese Project**: el issue pasa al espacio de desarrollo.
5. El issue debe describir **problema observado, contexto funcional no sensible, comportamiento esperado, resultado actual, impacto para el estudiante, pasos/evidencia reproducible y posible solución como propuesta no aprobada**. Referenciar IDs/archivos del sistema sin volcar transcripciones privadas, notas personales, tokens o datos confidenciales.
6. El desarrollo evalúa el issue contra `/docs`, clasifica confirmado/planificado/pendiente, implementa y prueba si se aprueba. Al terminar, registra el resultado en `/docs` y enlaza/cierra el issue según corresponda.
7. El coordinador puede explicar al estudiante que creó un issue y continuar por otra vía **solo si la alternativa es segura y existe**; nunca declarar reparado algo por el solo hecho de crear el issue.

**Seguridad:** el coordinador no altera código, desbloquea manualmente locks dudosos ni sobrescribe el Excel compartido; toda escritura debe pasar por los contratos seguros vigentes. Las integraciones externas requieren los permisos correspondientes.

## Implicación sobre la hoja de ruta

- **Etapa 2 — núcleo de conocimiento:** mantener **fuentes organizadas, verificadas y recuperables por materia**, búsquedas, trazabilidad y actualización. El índice local ya existe. **No** crear aquí un segundo tutor/gestor ni resúmenes enormes por materia solo porque sea técnicamente posible.
- **Etapa 3 — ChatGPT:** definir, crear y validar el **Project Coordinador**, los **Projects de materia** y su forma de consultar Academic Manager y registrar issues. La conversación y la síntesis contextual serán trabajo de esos Projects sobre evidencia; el repositorio aporta contratos/instrucciones y operaciones base.
- **Etapas 4–6:** funcionalidades concretas (dashboard, accesibilidad del núcleo, tests y estabilización) se priorizarán por las carencias observadas en el uso del Coordinador, no como una aplicación paralela completa.

## Interfaz del núcleo disponible para los futuros Projects (implementación posterior)

El módulo `academic_access/` y `scripts/academic-read.cmd` ya exponen **consultas de solo lectura en JSON** sobre estado local, materias, actividades, clases, fuentes y búsqueda; documentado en `27-contrato-consulta-academica.md`. El futuro Coordinador debe comenzar por `health` y `capabilities`, consultar solo la materia requerida y distinguir errores operativos de necesidades de producto antes de abrir un issue. **Esta interfaz no crea Projects, no modifica la base y no abre issues**. Su integración real con ChatGPT aún está pendiente.

## Próximos pendientes

- Determinar el **mínimo suficiente para cerrar Etapa 2**: estabilidad de acceso al índice, trazabilidad e ingestión incremental de fuentes nuevas, sin sobreconstruir síntesis.
- **Instrucciones base y plantilla de Issue preparadas (2026-10-08)** en `academic_projects/` y `.github/ISSUE_TEMPLATE/`. Se validó renderizado local y búsqueda de issues en el repositorio; **falta crear Projects y validar sus conexiones y permisos reales**. Ver `29-preparacion-chatgpt-projects.md`.
- Validar en uso real un ciclo completo: limitación detectada por Coordinador → issue creado → solución evaluada/desarrollada aquí → nuevo comportamiento disponible. **No se abre issue ficticio ahora.**

## Operaciones académicas autorizadas (incremento 2026-10-12)

Se añade `academic_operations` como contrato **restringido**, independiente de `academic_access` de solo lectura. El Coordinador puede solicitar completar una clase transcrita con vista previa y autorización explícita del estudiante. El núcleo ejecuta únicamente las fases autorizadas por Capture y el protocolo del workbook; no se conceden accesos generales a comandos, Excel o Drive. La interfaz no elude los bloqueos del conector. Su disponibilidad desde el Project Coordinador sigue **pendiente de validar**. Ver `31-operaciones-coordinador.md`.

## Actualización del rol especializado (2026-10-12)

**Redefinición de alcance solicitada por el estudiante:** además de tutoría, cada Project de materia será un entorno de producción académica para desarrollar trabajos, resolver ejercicios, revisar consignas y rúbricas, y avanzar en pendientes reales. El Project consulta exclusivamente el `materia_id` pertinente; no crea una segunda base de actividades ni modifica estados canónicos.

La coordinación entre materias, publicación de clases y registro de bases siguen a cargo del Coordinador y del núcleo autorizado. El estudiante puede trasladar referencias entre Projects: no existe comunicación automática garantizada. La plantilla de `academic_projects/templates/materia.md` implementa esta redefinición; debe instalarse manualmente en ChatGPT.
