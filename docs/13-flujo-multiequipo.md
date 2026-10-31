# 13 — Flujo multi-equipo de Academic Manager

**Estado:** Confirmado. Ciclo completo validado en un PC el 2026-10-06; prueba entre dos PCs pendiente.

## Objetivo

Permitir trabajar con Academic Manager desde más de un computador sin editar simultáneamente copias distintas del workbook ni sobrescribir una versión más reciente publicada desde otro equipo.

El diseño asume **un solo escritor a la vez**.

## Arquitectura

Cada computador mantiene su propia copia local:

```text
PC trabajo                         PC casa
C:\AcademicManagerData\           C:\AcademicManagerData\
├── database.xlsx                 ├── database.xlsx
├── temporales\                  ├── temporales\
├── procesamiento\              ├── procesamiento\
└── control\                     └── control\
          \                         /
           \                       /
            ↓                     ↓
                  Google Drive
          Academic Manager - Database.xlsx
          database-work-state.json
```

Google Drive es el punto de publicación/intercambio. `C:\AcademicManagerData` no se sincroniza directamente entre computadores.

### Workspace de desarrollo por equipo

El repositorio de código no vive en el runtime. En cada PC usado para desarrollar se mantiene, cuando corresponda:

```text
C:\AcademicManagerDev\Academic-Manager
```

Este workspace se sincroniza con GitHub y puede recrearse mediante clone/pull. No contiene datos académicos canónicos ni credenciales que deban viajar con Git.

`C:\AcademicManagerData` continúa siendo el runtime local independiente por equipo.

## Archivos compartidos

### Workbook

```text
Academic Manager - Database.xlsx
```

Es la versión publicada/canónica compartida.

### Estado de trabajo

```text
database-work-state.json
```

No contiene datos académicos. Coordina el derecho de escritura y conserva metadata de la última versión publicada validada.

Esquema actual:

```json
{
  "schema_version": 1,
  "resource": "Academic Manager - Database.xlsx",
  "canonical_drive_file_id": "1SampleCanonicalDbFileId_00000001",
  "status": "free | editing",
  "lock": null,
  "published": {
    "sha256": "...",
    "size_bytes": 0,
    "drive_modified_time": "...",
    "validation": "..."
  }
}
```

Cuando `status = editing`, `lock` identifica al menos:

```json
{
  "machine": "nombre-del-equipo",
  "started_at": "timestamp",
  "base_sha256": "hash-de-la-version-publicada-de-la-que-se-partio"
}
```

## Estado publicado actual

**Último hito (2026-10-08):** publicación completa de 31 actividades Canvas y las columnas estructuradas de envío y referencias, por Drive API con checkpoint y re-descarga. Estado final `status=free`, `lock=null`.

```text
published.sha256 = ff41a86f40bc355ecf9685c9b86d48e9c993f344d19bb91d696f5a3b6f3cc54f
```

La base conserva las clases `CLS-SER-20261001-01` y `CLS-GBD-20261001-01`, los bloques y los 7 registros de actividad previos. El hash anterior a la importación masiva era `6c5a415933c4f62d434259a442565eb579c637885d7c33fcb7d7744e74eaf682`. **La prueba física de dos PCs queda diferida por petición expresa del estudiante**; por ahora solo PC Diego. Véanse `docs/22-publicacion-bloques-v21.md` y `docs/24-canvas-actividades-automatizacion-v1.md`.

## Inicio de una sesión de escritura

1. Leer `database-work-state.json` desde Drive.
2. Si `status = editing` y el lock pertenece a otra sesión/equipo, **no escribir**.
3. Si está `free`, obtener la última versión publicada del workbook.
4. Validar que es un XLSX correcto.
5. Calcular SHA-256 y comprobarlo contra `published.sha256`.
6. Alinear `C:\AcademicManagerData\database.xlsx` con esa versión cuando el trabajo se realice desde el PC.
7. Crear checkpoint en `temporales`.
8. Cambiar el estado compartido a `editing`, con equipo, hora y `base_sha256`.
9. Solo entonces modificar el workbook.

## Durante la sesión

Debe existir un solo flujo de escritura.

Normalmente se edita:

```text
C:\AcademicManagerData\database.xlsx
```

La unidad virtual de Google Drive Desktop (`G:`, `H:` u otra letra) no se utiliza como vía programática de publicación binaria. El workbook se actualiza por Google Drive API conservando el mismo file ID.

### Entornos aislados de ejecución

Si una herramienta no puede montar `C:\AcademicManagerData`, puede operar sobre una copia temporal aislada **solo** cuando:

- el SHA-256 de la copia coincide con `published.sha256`/`base_sha256`;
- el lock ya fue adquirido;
- esa copia no se trata como fuente de verdad;
- antes de publicar se comprueba otra vez que Drive sigue en `base_sha256`;
- después de publicar se re-descarga/revalida y se realinea `C:\AcademicManagerData\database.xlsx`.

Esto permite usar herramientas de ejecución sin hacer que el núcleo de Academic Manager dependa de ellas.

## Cierre normal

1. Validar la versión modificada.
2. Calcular su SHA-256.
3. Comprobar que el workbook publicado en Drive sigue teniendo `base_sha256`.
4. Si cambió, **no sobrescribir**; detener y reconciliar.
5. Publicar la versión validada.
6. Volver a descargar la versión publicada.
7. Validar estructura y SHA-256 de ida y vuelta.
8. Alinear la copia local del PC con lo publicado.
9. Actualizar `database-work-state.json` con el nuevo hash/tamaño/fecha.
10. Poner `status = free` y `lock = null`.

La sesión no se considera terminada hasta liberar el lock.

## Primera validación real del protocolo

El 2026-10-06 se ejecutó el ciclo completo al importar `ACT-GBD-001` desde el PC `ApoyoTIC`.

Secuencia observada:

```text
published.sha256 inicial
= 6cba833c9cb19fd436526fc00991f23a844a1b81ae47861b2968555a4ecf382d

↓ comprobar copia/base
↓ abrir workbook con Excel
↓ crear checkpoint
↓ adquirir lock (ApoyoTIC)
↓ modificar/importar GBD E1
↓ validar XLSX
↓ comprobar que Drive seguía en el hash base
↓ publicar
↓ re-descargar
↓ comprobar SHA-256 = 7718a6bca4c91b3a9b28bd72831da1a07a2c88725eb871a80f60881287fa0dae
↓ abrir copia publicada con Microsoft Excel
↓ confirmar 7 hojas + ACT-GBD-001
↓ copiar versión publicada validada a C:\AcademicManagerData\database.xlsx
↓ liberar lock
```

Resultado: el protocolo funciona en una sesión real de un solo PC. Esto valida el mecanismo técnico de adquisición, protección de versión base, publicación, revalidación y liberación; **no valida todavía la coordinación entre dos equipos simultáneamente disponibles**.

### Revalidación con importación SER

El mismo ciclo se ejecutó de nuevo para importar SER E2/E3. Se partió del hash publicado `7718a6bca4c91b3a9b28bd72831da1a07a2c88725eb871a80f60881287fa0dae`, se creó checkpoint, se adquirió el lock de `ApoyoTIC`, se publicó, se re-descargó y Microsoft Excel confirmó 7 hojas y los nuevos registros. La copia local quedó alineada y el lock se liberó con el nuevo hash `4ac9b787f72dd0680714c8e15232c9291f93149191037f5bd4eac4c8e7253a58`.

Esto demuestra repetibilidad del protocolo en el mismo PC; la prueba entre dos computadores sigue pendiente.

### Revalidación con importación SO2

El ciclo se ejecutó una tercera vez para importar SO2 E2/E4. Se partió del hash `4ac9b787f72dd0680714c8e15232c9291f93149191037f5bd4eac4c8e7253a58`, se creó checkpoint, se adquirió el lock de `ApoyoTIC`, se comprobó que Drive seguía en la versión base y se publicó.

La versión publicada se re-descargó con SHA-256 `bfcadf6f5d511c1141ed2bfefae495bc520ca805e28a0bf53edff71f2f69d2a6` y tamaño `70158` bytes. Microsoft Excel confirmó las 7 hojas y los nuevos registros. Después se alineó `C:\AcademicManagerData\database.xlsx` y se liberó el lock.

### Revalidación con importación Inglés III

El ciclo se ejecutó una cuarta vez para importar un homework calificado y el oral con modalidades alternativas. Se partió del hash `bfcadf6f5d511c1141ed2bfefae495bc520ca805e28a0bf53edff71f2f69d2a6`, se verificó la copia local, se creó checkpoint y se adquirió el lock de `ApoyoTIC`.

Antes de publicar se confirmó que Drive seguía exactamente en el hash base. La nueva versión fue publicada y re-descargada con SHA-256 `25dd3b77ba6cb683ad786cf03b4ea7b83dd2a856f839869703ac772e91a15c4c`, tamaño `71595` bytes. `artifact_tool` confirmó la estructura y Microsoft Excel abrió las 7 hojas y los registros `ACT-ING3-HW2`, `ACT-ING3-ORAL`, `ENT-ING3-ORAL-VIDEO` y `ENT-ING3-ORAL-LIVE`. Después se alineó `C:\AcademicManagerData\database.xlsx` y se liberó el lock.


### Quinta validación — primera clase SER y publicación por API

El 2026-10-07 se registró `CLS-SER-20261001-01` desde el PC `Diego`. El ciclo partió del hash `25dd3b77ba6cb683ad786cf03b4ea7b83dd2a856f839869703ac772e91a15c4c`, adquirió el lock, creó checkpoint, modificó la copia local y publicó el XLSX por Google Drive API.

La versión publicada fue re-descargada y validada con SHA-256 `54fd7b5f545d020491b9cc9bd6bf188e4ec784120cdfd9f103125a5d41223a38`, tamaño `72195` bytes. Microsoft Excel confirmó las 7 hojas y la fila `CLS-SER-20261001-01`; después se alineó `C:\AcademicManagerData\database.xlsx` y se liberó el lock.

Capture volvió a consultar esa misma clase mediante el flujo automático y detectó que ya estaba registrada, devolviendo el mismo `clase_id` sin duplicarla ni modificar el workbook. Esto valida la idempotencia del registro sobre un caso real.

### Sexta validación — segunda clase real GBD

El 2026-10-07 se registró una segunda clase de otra materia mediante Capture, `CLS-GBD-20261001-01`. La publicación previa se protegió con lock y checkpoint local. La versión final se descargó nuevamente de Google Drive API y se comprobó con SHA-256 `9b4686373de57881b36a5712b70df263f98272a5392508c662fd2a21189962c8`, tamaño `88059` bytes. La verificación integrada abrió la copia con Excel y confirmó el nuevo ID; el estado compartido quedó `free` y sin lock. Esto sigue siendo una prueba en **un solo equipo** y no reemplaza la validación entre dos PCs.

## Cambio de computador

### Antes de abandonar el PC actual

Debe quedar:

```text
validado
→ publicado
→ re-descargado
→ revalidado
→ copia local alineada
→ estado compartido = free
```

### Al llegar al otro PC

No se continúa desde una copia local antigua:

```text
leer estado compartido
→ comprobar free
→ descargar última publicada
→ validar/hash
→ reemplazar/alinear copia local
→ checkpoint
→ adquirir lock
→ trabajar
```

## Bloqueo abandonado

No existe timeout automático.

Si aparece un `editing` antiguo:

1. comprobar si el equipo indicado sigue disponible;
2. comprobar cambios locales/checkpoints si es posible;
3. comparar versión publicada con `base_sha256`;
4. confirmar que no existe trabajo válido pendiente;
5. solo entonces liberar manualmente el lock.

## Preparación de un PC nuevo

1. Crear el runtime:

```text
C:\AcademicManagerData\
├── temporales\
├── procesamiento\
└── control\
```

2. Si el PC se usará para desarrollo, clonar `Ds0957/Academic-Manager` en `C:\AcademicManagerDev\Academic-Manager` y validar el repo antes de trabajar.
3. Configurar localmente la identidad de publicación del Drive personal y comprobar que tiene acceso Editor únicamente a la carpeta `Academic Manager`.
4. Obtener el workbook publicado como `database.xlsx`.
5. Leer/descargar `database-work-state.json`.
6. Validar el workbook con un lector independiente.
7. Si se usa RDC, autorizar únicamente las rutas necesarias.
8. No asumir que Google Drive Desktop usa la misma letra de unidad que otro PC.
9. Las credenciales locales de cada equipo no se sincronizan mediante Drive ni se guardan en GitHub.

## Responsabilidades

- **Google Drive:** versión publicada, intercambio y lock compartido.
- **C:\AcademicManagerData:** espacio de trabajo local por computador.
- **database-work-state.json:** exclusión de escritura y versión base/publicada.
- **GitHub `/docs`:** reglas persistentes; no participa en el lock operativo.
- **RDC:** filesystem/terminal/procesos del equipo autorizado.

## Regla central

> Un computador no empieza a escribir hasta adquirir el lock sobre la última versión publicada, y no entrega el turno hasta publicar, revalidar, alinear su copia local y liberar el lock.

No se implementan merges automáticos ni edición multiusuario del XLSX por ahora.
