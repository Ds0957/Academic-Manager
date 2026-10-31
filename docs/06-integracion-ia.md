# 06 — Integración con IA y ChatGPT

**Estado:** separación de tres tipos de Project confirmada en diseño (2026-10-08). La continuidad del Project de Desarrollo se validó mediante uso real; el Coordinador y los Projects de materia todavía no se han creado ni validado.

## Principio

La IA es una capa de trabajo sobre Academic Manager, no el lugar donde se almacena de forma exclusiva la información del sistema.

```text
Datos y archivos de Academic Manager
            │
            ▼
    herramienta de acceso
            │
            ▼
       ChatGPT / IA
            │
            ▼
 análisis, consultas y actualizaciones
```

## Base de conocimiento consultable (Etapa 2, primer incremento)

**Ya implementado el 2026-10-08:** un índice SQLite FTS5 local derivado de Excel, Drive y Canvas Study (`academic_knowledge/`, `scripts/knowledge.cmd`). Permite buscar fragmentos textuales con procedencia por materia y encontrar fuentes ligadas a actividades del mismo eje, sin confundir orientación contextual con requisitos evaluativos. Sus datos son caché reconstruible, no una memoria exclusiva de ChatGPT. Consultas `build`, `status`, `search`, `activity` y `sources`. Ver `25-indice-conocimiento-academico-v1.md`. El índice es infraestructura: las consultas y la síntesis conversacional futura corresponden al Coordinador y a los Projects de materia, sin duplicar un tutor dentro del núcleo.

## Contrato de consultas de solo lectura (implementado, no conectado a Projects)

`academic_access/` y `scripts/academic-read.cmd` ofrecen JSON versionado de materias, semestres/bloques, actividades, clases, búsqueda y fragmentos verificados. Permiten a los futuros Projects usar el núcleo sin interpretar Excel ni ejecutar escrituras. `health` distingue la frescura del índice respecto de la copia local y el último resultado conocido de Canvas Sync. **No hay aún conector configurado en los Projects**, ni automatización de GitHub Issues, ni servicio HTTP público. Se preparó el renderizador local de instrucciones `academic_projects/` y una plantilla de issue en `.github/ISSUE_TEMPLATE/`; se probó la **búsqueda** de issues en el repositorio (no la creación). Ver `29-preparacion-chatgpt-projects.md`. El contrato técnico y errores están en `docs/27-contrato-consulta-academica.md`.

## ChatGPT Projects

**Decisión redefinida y confirmada (2026-10-08):** se utilizarán **tres tipos distintos de Project**, sin mezclar sus responsabilidades:

- **Desarrollo de Academic Manager (este Project):** construir, probar y mantener el sistema y el repositorio.
- **Coordinador académico (Project futuro):** gestionar conversacionalmente la vida universitaria usando datos reales del núcleo.
- **Un Project de ChatGPT por materia (futuro):** explicar, estudiar y apoyar académicamente cada asignatura.

El Coordinador puede identificar deficiencias funcionales y **crear issues en GitHub** para que el Project de Desarrollo las evalúe y resuelva. La especificación confirmada de este flujo está en `26-roles-coordinador-desarrollo-materias.md`; **la creación de los Projects y el flujo real de issues aún no están implementados**.

Las instrucciones de las materias deberían compartir una base común, de forma que un chat nuevo ya sepa cómo trabajar con el sistema.

Sin embargo, el Project no debe ser la única memoria de la materia ni del desarrollo de Academic Manager.

## Continuidad del desarrollo entre chats

El repositorio privado:

```text
Ds0957/Academic-Manager
```

actúa como memoria de continuidad del desarrollo. La documentación oficial vive en `/docs`.

Las instrucciones generales se configuran directamente en el Project de ChatGPT y remiten al repositorio como fuente de verdad. No se mantiene un archivo adicional de instrucciones dentro de `/docs`.

Al iniciar un chat nuevo en el **Project de Desarrollo de Academic Manager**, el flujo es:

1. leer `docs/README.md`;
2. leer `docs/07-estado-actual.md`;
3. leer `docs/08-decisiones-y-pendientes.md`;
4. consultar únicamente los documentos temáticos necesarios para la tarea actual;
5. continuar el trabajo sin depender del historial de chats anteriores.

Este mecanismo se validó mediante uso real el **2026-10-06**: un chat nuevo recuperó el estado desde esos documentos y pudo continuar el desarrollo correctamente.

Cuando se alcance una decisión relevante, una implementación o un hito estable, `/docs` debe actualizarse para que el siguiente chat pueda recuperar el nuevo estado. El trabajo de código sigue `14-flujo-desarrollo.md`: contexto mínimo una vez, edición local agrupada, validación única por lote, prueba integrada relevante y documentación al final.

## Project por materia

Su función es servir como espacio de interacción especializado en una asignatura.

Debe poder trabajar con información como:

- estado de la materia;
- clases;
- recursos;
- trabajos;
- evaluaciones;
- conocimiento procesado.

Cuando el acceso a los archivos esté disponible mediante Desktop Commander u otro mecanismo, no será necesario duplicar toda esa información como archivos permanentes dentro del Project.

**Avance confirmado (2026-10-08):** ya existe una plantilla base y un generador que incorpora `materia_id`, nombre, semestre y bloques desde el Excel validado: `scripts/project-instructions.cmd materia --materia MAT-SER`. Estas instrucciones todavía **no están instaladas ni probadas** dentro de Projects reales. Ver `29-preparacion-chatgpt-projects.md`.

## Coordinador académico (Project independiente, todavía no creado)

Será la interfaz conversacional global de la vida universitaria **sin responsabilidad de desarrollo**. Este Project de Desarrollo se limita a construir, verificar y evolucionar la infraestructura mediante decisiones documentadas e issues. La separación de roles es una decisión **confirmada**; la configuración, conexión y validación del Coordinador siguen **planificadas**.

Ejemplos de preguntas objetivo sobre la vida universitaria:

- ¿Qué tengo pendiente esta semana?
- ¿Qué debería hacer primero?
- ¿Qué materia estoy descuidando?
- ¿Qué evaluaciones se aproximan?
- Tengo dos horas libres, ¿qué conviene avanzar?

Para responder, deberá apoyarse en la base y en el índice de conocimiento y profundizar en fuentes concretas cuando haga falta. Cuando detecte una carencia real del sistema, deberá revisar las capacidades documentadas y los issues existentes; si requiere un cambio de producto, abrirá un issue en `Ds0957/Academic-Manager` para trabajarlo **desde este Project de Desarrollo**, sin improvisar cambios estructurales por su cuenta. Ver `26-roles-coordinador-desarrollo-materias.md`.

## Acceso actual mediante Desktop Commander

Actualmente se trabaja desde el equipo autorizado `Diego` mediante Desktop Commander.

Las operaciones de archivos de Academic Manager están restringidas a:

```text
C:\AcademicManagerDev
C:\AcademicManagerData
G:\Mi unidad\03 - Universidad\Academic Manager
```

El repositorio local estable se usa para desarrollo y pruebas; el runtime y los datos reales permanecen separados.

Esto permite trabajar con la información del sistema sin tener que subir manualmente cada archivo al chat.

## Independencia de plan

Requisito de diseño:

> El núcleo de Academic Manager no debe depender de una función exclusiva de un plan concreto de ChatGPT.

Las integraciones pueden mejorar la experiencia, pero los datos deben seguir disponibles como archivos normales y Excel.

La disponibilidad concreta de plugins y capacidades debe validarse en cada plan/cuenta antes de tratarlos como dependencia obligatoria.

## Procesamiento de una clase

Flujo conceptual deseado:

```text
transcripción
     │
     ▼
IA analiza la clase
     │
     ├── crea análisis persistente
     ├── detecta actividades/fechas
     ├── identifica conocimiento importante
     └── actualiza estado/indexación
```

La evidencia original no debe perderse. El análisis es una interpretación derivada de esa fuente.

## Comandos conversacionales

Se han planteado comandos o intenciones como:

```text
/class
/study
/task
/exam
/status
/close
```

**Todavía no están aprobados como contrato definitivo.**

La idea es utilizarlos como atajos conversacionales, no necesariamente como comandos técnicos reales.

## Regla para chats nuevos de materias

Objetivo de las futuras instrucciones de cada materia:

1. identificar la materia;
2. consultar su estado actual;
3. consultar únicamente la información necesaria para la tarea;
4. no pedir al usuario datos que ya estén registrados;
5. persistir fuera del chat la información importante generada durante el trabajo.

El mecanismo específico se definirá cuando construyamos la estructura interna real de una materia.
