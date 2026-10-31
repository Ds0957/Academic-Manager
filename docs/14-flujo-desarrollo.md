# 14 — Flujo de desarrollo eficiente

**Estado:** Confirmado para el desarrollo de Academic Manager desde el 2026-10-07.

## Objetivo

Mantener la misma calidad de validación reduciendo latencia operativa, llamadas redundantes a herramientas y sincronizaciones innecesarias.

La optimización **no elimina** controles de integridad. El protocolo del workbook —lock, SHA-256, checkpoint, validación, publicación por Drive API, re-descarga, apertura con Excel, alineación local y liberación— permanece obligatorio cuando se modifica la base real.

## Separación de espacios

Código y documentación:

```text
C:\AcademicManagerDev\
└── Academic-Manager\
    ├── capture\
    ├── tests\
    ├── scripts\
    └── docs\
```

Runtime y datos locales:

```text
C:\AcademicManagerData\
├── database.xlsx
├── temporales\
├── procesamiento\
└── control\
```

Persistencia académica compartida:

```text
Google Drive / 03 - Universidad / Academic Manager
```

`C:\AcademicManagerData\temporales` no es un workspace de desarrollo. Las copias temporales de código utilizadas durante la validación inicial de Capture quedan fuera del flujo normal.

GitHub `Ds0957/Academic-Manager` sigue siendo la fuente persistente de verdad para código y documentación una vez los cambios han sido confirmados y publicados.

## Flujo estándar de una sesión de desarrollo

### 1. Recuperar contexto una sola vez

Al comenzar: `docs/README.md`, `docs/07-estado-actual.md`, `docs/08-decisiones-y-pendientes.md` y únicamente los documentos temáticos necesarios.

No volver a recuperar los mismos documentos durante la sesión salvo que hayan cambiado o exista una contradicción concreta.

### 2. Sincronizar el workspace local

```powershell
git fetch --prune
git pull --ff-only
```

El workspace esperado es `C:\AcademicManagerDev\Academic-Manager`. Si Git no está disponible globalmente, puede usarse una instalación local/portable; lo contractual es mantener el repo alineado con `origin/main`.

### 3. Diseñar un lote coherente antes de editar

Evitar `editar → probar → editar → volver a probar todo → documentar provisionalmente`. Preferir `entender → planificar el lote → editar archivos relacionados → validar una vez`.

### 4. Editar localmente

Las modificaciones de código y `/docs` se realizan sobre el repositorio local estable. No se usa como flujo normal copiar archivos individualmente entre GitHub y `temporales`, mantener un mirror manual bajo `C:\AcademicManagerData` ni editar remotamente archivo por archivo cuando el workspace local está disponible.

### 5. Ejecutar una batería única de validación

Comando de referencia:

```powershell
.\scripts\dev-check.ps1
```

Comprueba compilación, suite de tests y problemas de diff. La suite completa de Capture es suficientemente rápida para ejecutarse al final de cada lote coherente. Si la política de ejecución local de PowerShell impide lanzar `dev-check.ps1`, no es necesario cambiar la política global: ejecutar sus tres comprobaciones equivalentes por separado (`python -m compileall -q capture tests`, `python -m unittest discover -s tests -v`, `git diff --check`).

Validación adicional según riesgo: cambios documentales requieren diff; código de Capture requiere suite completa y prueba dirigida si aplica; Drive/autenticación requiere prueba no destructiva y solo después una prueba real si es necesaria; workbook/datos reales conserva el protocolo completo sin atajos.

### 6. Hacer una sola prueba integrada relevante

No repetir el mismo hecho mediante varias rutas equivalentes salvo que una validación falle, dos herramientas discrepen o el cambio afecte una frontera crítica distinta.

### 7. Revisar y persistir

```powershell
git status
git diff --check
git diff
```

Después se persiste el lote como un cambio coherente en GitHub. Git local es preferente cuando está autenticado. El conector de GitHub es fallback para la persistencia final, no para sincronizaciones intermedias archivo por archivo.

### 8. Documentar al final del hito

Actualizar el documento temático y, cuando corresponda, `07-estado-actual.md` y `08-decisiones-y-pendientes.md` después de que la implementación haya pasado validación. No documentar intentos fallidos ni ideas provisionales.

### 9. Cierre corto

Verificar únicamente: `tests/validación = OK`, `git diff/status = esperado`, estado real afectado coherente y `/docs` actualizado cuando aplica. No repetir pruebas si nada cambió después.

## Reglas para reducir latencia sin perder calidad

1. Preferir operaciones por lote frente a llamadas pequeñas repetidas.
2. Leer contexto una vez y reutilizarlo.
3. Ejecutar la suite completa una vez por lote coherente, no después de cada archivo.
4. Ejecutar pruebas integradas solo en las fronteras afectadas.
5. No mantener dos workspaces de código activos.
6. No sincronizar manualmente cada archivo con GitHub durante la implementación.
7. No usar el workbook real para probar lógica que puede comprobarse con tests aislados.
8. Cuando sí se modifica el workbook real, conservar todas las validaciones de integridad.
9. Resolver primero la implementación y después actualizar `/docs`.
10. Si una herramienta falla por una limitación conocida, cambiar de estrategia una vez; no encadenar múltiples intentos equivalentes.

## Objetivo operativo

No existe un SLA rígido. Una implementación pequeña o mediana de código + tests + documentación debería resolverse con un número reducido de operaciones agrupadas, no decenas de llamadas seriales.

Procesos intrínsecamente pesados —transcripción, descarga de videos grandes, modelos GPU— conservan su tiempo natural. La optimización elimina tiempo de orquestación, no trabajo técnico.

## Flujo resumido

```text
/docs mínimo necesario
        ↓
git pull --ff-only
        ↓
plan del lote
        ↓
edición local agrupada
        ↓
scripts/dev-check.ps1
        ↓
prueba integrada relevante
        ↓
revisión de diff
        ↓
persistencia en GitHub
        ↓
actualización final de /docs
        ↓
cierre corto
```
