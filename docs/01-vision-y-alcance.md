# 01 — Visión y alcance

**Estado:** Confirmado en lo conceptual; el alcance funcional seguirá creciendo por uso real.

## Nombre

El proyecto se llama **Academic Manager**.

## Propósito

Academic Manager es un gestor personal de vida universitaria. Su objetivo es centralizar y relacionar la información necesaria para administrar el semestre sin depender de que un chat recuerde todo.

Debe ayudar a responder preguntas como:

- ¿Qué materias estoy cursando?
- ¿Qué tengo pendiente y para cuándo?
- ¿Qué clase fue la última procesada?
- ¿Qué recursos existen sobre un tema?
- ¿Qué dijo el profesor en una clase concreta?
- ¿Qué materia estoy descuidando?
- ¿Qué debería hacer primero?
- ¿Qué necesito estudiar para una evaluación?

## Principios de diseño

### 1. Los datos no viven en los chats

Los chats son una interfaz de trabajo y razonamiento. La información persistente debe quedar almacenada en archivos y en la base estructurada de Academic Manager.

### 2. Fuente de verdad accesible

La información académica debe mantenerse en formatos que sigan siendo utilizables aunque cambie el plan de ChatGPT, una integración o incluso el proveedor de IA.

### 3. Crear estructura según el uso

No se diseñará por adelantado una jerarquía compleja de carpetas para cada materia. La estructura interna se creará cuando aparezca una necesidad concreta y comprobada.

### 4. Separar contenido de índice

Los documentos extensos se guardan como archivos. La base de datos guarda principalmente estado, metadatos, relaciones y rutas hacia esos archivos.

### 5. Automatizar fricción real

Academic Manager no busca convertirse de inmediato en una aplicación universitaria completa. Primero se valida el flujo diario y luego se automatizan los puntos que realmente consumen tiempo.

**Criterio operativo V1 (confirmado 2026-10-08):** las operaciones frecuentes (sincronización académica, reconciliación, detección de novedades, consulta de estado) deben ejecutarse con mínima intervención recurrente y ser idempotentes. El sistema debe persistir las decisiones revisadas, no pedir confirmar lo mismo cada día y mostrar únicamente excepciones nuevas o conflictos. La automatización nunca implica copiar registros ambiguos ni sobrescribir datos confirmados sin controles; los flujos críticos de escritura mantienen las protecciones de integridad.

### 6. Separar núcleo, coordinador conversacional y desarrollo

**Decisión confirmada (2026-10-08):** el núcleo de Academic Manager conserva datos confiables y expone operaciones accesibles; el futuro Project **Coordinador** de ChatGPT administra la vida universitaria usando esas operaciones; los Projects de materia se centran en estudiar. **Este Project de Desarrollo** se encarga del diseño, implementación y mejora técnica. No se duplicará en el núcleo un coordinador conversacional completo ni se desarrollarán grandes estructuras de estudio sin uso comprobado. Las carencias detectadas por el futuro Coordinador se escalarán mediante GitHub Issues a este espacio de desarrollo. El contrato está en `docs/26-roles-coordinador-desarrollo-materias.md`.

## Alcance inicial

Academic Manager cubre inicialmente:

- semestres;
- materias;
- actividades y entregas;
- clases;
- recursos académicos;
- transcripciones;
- análisis de clases y otros contenidos;
- planificación y estado académico;
- conexión con IA para consultar y procesar la información.

## Fuera del alcance por ahora

No se considera cerrado todavía el diseño de:

- estructura interna definitiva de cada materia;
- sistema de notas/calificaciones;
- horarios;
- personas/profesores como entidad independiente;
- comandos finales de IA;
- interfaz final de Academic Manager;
- implementación definitiva de Capture.

Estos elementos se documentarán cuando se definan mediante uso real.