# 02 — Arquitectura

**Estado:** Confirmado para la arquitectura actual; algunos componentes siguen en evolución.

## Vista general

```text
Google Drive / Academic Manager
        │
        ├── Academic Manager - Database.xlsx
        ├── database-work-state.json
        └── 2026-2\...
        │
        ▼
C:\AcademicManagerData\
(runtime y copia local)
        │
        ▼
ChatGPT / IA
```

El código y la documentación se desarrollan por separado del runtime:

```text
GitHub: Ds0957/Academic-Manager
             ↕
C:\AcademicManagerDev\Academic-Manager
├── capture/
├── tests/
├── scripts/
└── docs/
```

GitHub conserva de forma persistente el código y `/docs`; el workspace local permite editar y validar por lotes antes de publicar.

## Componentes

### 1. Carpeta raíz de Academic Manager

La raíz persistente está en Google Drive bajo `03 - Universidad/Academic Manager`. En el PC `Diego` se visualiza actualmente mediante `G:\Mi unidad\03 - Universidad\Academic Manager`, pero la letra de unidad puede cambiar entre equipos.

Google Drive es el almacenamiento compartido; `C:\AcademicManagerData` es el runtime local y `C:\AcademicManagerDev\Academic-Manager` es el workspace de desarrollo.

### 2. Base de datos Excel

Archivo:

```text
Academic Manager - Database.xlsx
```

Funciona como índice estructurado del sistema: materias, actividades, clases, recursos, semestres y una futura vista tipo dashboard.

### 3. Remote Desktop Commander

Actúa como puente entre ChatGPT y la carpeta local/sincronizada de Academic Manager.

Permite, cuando está conectado:

- listar carpetas;
- leer archivos;
- crear archivos;
- modificar archivos;
- buscar contenido;
- trabajar con Excel;
- ejecutar procesos locales cuando sea necesario.

### 4. ChatGPT / IA

La IA es una capa de interacción y razonamiento, no la fuente de verdad.

Su función puede incluir:

- analizar clases;
- consultar transcripciones;
- actualizar estado;
- localizar recursos;
- registrar actividades;
- preparar evaluaciones;
- coordinar prioridades.

### 5. GitHub

Repositorio actual:

```text
Ds0957/Academic-Manager
```

Conserva el código y la documentación oficial de Academic Manager. El desarrollo se valida primero en el workspace local estable y después se persiste en el repositorio.

No es actualmente la fuente principal de los datos académicos personales.

### 6. Academic Manager Capture

Componente implementado en V0 para capturar/transcribir grabaciones, generar artefactos y registrar clases en Academic Manager.

Se documenta por separado en `05-capture.md`.

## Flujo conceptual de información

Ejemplo de una clase:

```text
Grabación / audio / video
        │
        ▼
Academic Manager Capture
        │
        ├── transcripción
        └── metadatos
        │
        ▼
Carpeta de la materia
        │
        ▼
IA procesa el contenido
        │
        ├── análisis
        ├── actividades detectadas
        └── actualización de estado
        │
        ▼
Academic Manager - Database.xlsx
```

## Fuente de verdad

La arquitectura actual separa responsabilidades:

- **Archivos:** contenido completo y evidencia.
- **Excel:** estado, relaciones, metadatos y rutas.
- **ChatGPT:** procesamiento, consulta y asistencia.
- **GitHub:** código y documentación persistentes del sistema.

Ningún chat individual debe convertirse en la única ubicación de información importante.
