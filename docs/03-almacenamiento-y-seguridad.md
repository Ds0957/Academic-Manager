# 03 — Almacenamiento y seguridad

**Estado:** Confirmado para la configuración actual.

## Carpeta raíz

Academic Manager persiste en Google Drive bajo:

```text
03 - Universidad/Academic Manager
```

En el PC `Diego` Google Drive Desktop lo muestra actualmente en `G:\Mi unidad\03 - Universidad\Academic Manager`. La letra de unidad no es contractual. El workbook canónico se publica mediante Google Drive API; Drive Desktop sirve como vista/sincronización de archivos, no como vía programática de escritura binaria.

## Acceso desde ChatGPT

El acceso se realiza mediante **Remote Desktop Commander**.

La configuración actual de acceso a archivos está restringida a:

```text
C:\AcademicManagerDev
C:\AcademicManagerData
G:\Mi unidad\03 - Universidad\Academic Manager
```

`C:\AcademicManagerDev` contiene código/herramientas de desarrollo; `C:\AcademicManagerData` contiene runtime y datos operativos.

La intención es que Academic Manager pueda trabajar sobre su propio espacio sin exponer innecesariamente el resto del computador o de Google Drive.

## Arranque del agente remoto en el PC Diego

**Configuración confirmada (2026-10-07):** Windows tiene una tarea programada `AcademicManager-DesktopCommanderRemote` con disparador **al iniciar sesión** del usuario Windows `diego`. Se ejecuta solo en su sesión interactiva, con privilegios limitados, sin contraseña adicional ni alteración de las políticas de PowerShell.

La tarea lanza en segundo plano `scripts/start-remote-agent.cmd` desde el workspace de desarrollo. El lanzador comprueba que existe la sesión de dispositivo previamente autorizada en el perfil de Windows y ejecuta `npx.cmd @wonderwhy-er/desktop-commander@latest remote`. No crea dispositivos ni autoriza cuentas automáticamente. Si el proceso termina, reintenta después de 45 segundos. Las instancias repetidas de la misma tarea se ignoran.

La sesión local del dispositivo es **privada**: no se copia a GitHub, Drive ni a otros computadores. Las restricciones de `allowedDirectories` siguen vigentes. La tarea no permite arrancar un PC apagado ni recuperar un PC sin conexión o sin sesión de Windows. El primer arranque efectivo mediante inicio de sesión de Windows sigue **pendiente de comprobar**; al configurarla se mantuvo la conexión manual ya activa, sin iniciar una segunda instancia.

Mantenimiento: en **Programador de tareas** de Windows, buscar `AcademicManager-DesktopCommanderRemote` para inspeccionar, deshabilitar o finalizar la tarea. En caso de revocar la sesión, reautorizar el dispositivo manualmente; no almacenar contraseñas en scripts.

## Separación entre desarrollo y runtime

```text
C:\AcademicManagerDev\Academic-Manager   → código, tests y /docs
C:\AcademicManagerData                   → runtime, temporales, procesamiento y control
Google Drive / Academic Manager           → persistencia académica compartida
```

No se usa `C:\AcademicManagerData\temporales` como workspace normal del repositorio. Las credenciales permanecen en `control/` y nunca se copian al repositorio de desarrollo.

El flujo detallado está en `14-flujo-desarrollo.md`.

## Ruta de datos y carpeta de Drive configurables

`C:\AcademicManagerData` y `G:\Mi unidad\03 - Universidad\Academic Manager` son los valores **por defecto**, no rutas obligatorias. Todos los módulos (`academic_access`, `academic_operations`, `academic_store`, `academic_knowledge`, `canvas_sync`, `capture`, `academic_projects`) las resuelven con el mismo código (`academic_config/paths.py`), en este orden:

1. `--runtime` explícito, en las CLI que lo tienen.
2. La variable de entorno `ACADEMIC_MANAGER_DATA` (la misma que usan los lanzadores `.cmd`). Capture mira antes `ACADEMIC_MANAGER_RUNTIME`, como hasta ahora.
3. El archivo de configuración del usuario.
4. El valor de siempre: `C:\AcademicManagerData`. Fuera de Windows, `canvas_sync`, `capture` y `academic_operations` siguen exigiendo `--runtime`, la variable o el archivo.

**Sin archivo de configuración ni variable nueva, todo funciona exactamente igual que antes.**

Archivo de configuración, uno por usuario:

```text
%APPDATA%\AcademicManager\config.json        (Windows)
~/.config/academic-manager/config.json        (otros sistemas)
```

`ACADEMIC_MANAGER_CONFIG` indica otra ubicación del archivo (pruebas y futuro instalador). Contenido, con ambas claves opcionales y rutas absolutas:

```json
{"runtime": "D:\\AcademicManagerData", "drive_root": "H:\\Mi unidad\\03 - Universidad\\Academic Manager"}
```

- `runtime` sustituye a `C:\AcademicManagerData` según el orden anterior.
- `drive_root` sustituye a la raíz de Drive por defecto del índice de conocimiento; `academic_access source` solo lee textos dentro de esa raíz o del runtime.
- Falla cerrado: si el archivo existe pero no se puede leer, no es JSON, no es un objeto, tiene una clave desconocida o una ruta que no es absoluta, el comando se detiene con `Configuración inválida en <archivo>: …`, aunque se haya indicado `--runtime`. Las CLI JSON (`academic_access`, `academic_operations`) devuelven el código `INVALID_CONFIG`; las demás escriben el error con su prefijo habitual.
- Los lanzadores `scripts\*.cmd` no pueden leer JSON: usan el Python de `%ACADEMIC_MANAGER_DATA%\procesamiento\capture-env` cuando esa variable existe y allí hay entorno; si no, el de `C:\AcademicManagerData`. `canvas-daily.ps1`, `install-canvas-daily-task.ps1` y `dev-check.ps1` siguen con las rutas de siempre.

## Identidad para publicar la base en Drive

La base se publica por Drive API con una de dos identidades, elegidas por `canvas_sync.workbook_blocks._drive_service` (lo usan la publicación de bloques, `enroll-publish`, las escrituras desde el chat, `academic_store cutover`/`unlock` y la comprobación del sistema):

1. **Cuenta de servicio** (`control\service-account-drive.json`, alcance `drive`), como hasta ahora. Si ese archivo existe, se usa siempre: mismo archivo, mismos alcances y mismas llamadas a Drive. Si existe pero es un enlace o una carpeta, se rechaza («No hay identidad técnica local válida para publicar.») sin probar la otra identidad.
2. **El inicio de sesión propio del asistente**, solo cuando no hay archivo de cuenta de servicio: `control\drive-oauth\drive-write-token.dpapi` (fuera de Windows `drive-write-token.json`, permisos `0600`). Se guarda con el mismo `store_token` (DPAPI del usuario de Windows) que la autorización de Capture y en la misma carpeta.

Diferencia entre los dos inicios de sesión de Google:

| | `capture --login-drive` (consola) | Paso 4 del asistente |
|---|---|---|
| Permisos pedidos | `drive.readonly` (ver y descargar) | `drive.readonly` y `drive` (ver, descargar y guardar) |
| Archivos | `drive-token.dpapi` | `drive-token.dpapi` (lo sigue usando Capture) y `drive-write-token.dpapi` (publicación) |
| Publica la base | no | sí, si no hay cuenta de servicio |

Un token de Capture que ya existía sigue valiendo igual (se comprueba solo `drive.readonly`); no hay que volver a iniciar sesión. Un token sin el permiso `drive` no sirve para publicar: el mensaje dice que a la sesión de Google le falta el permiso para guardar en Drive y que se arregla con «Volver a iniciar sesión» en el asistente; `academic_operations complete` lo comprueba con el mismo cargador antes de llamar a Capture y responde `CREDENTIAL_UNAVAILABLE`. Capture `--register-database` (y `academic_operations complete`, que lo usa) sigue igual con la cuenta de servicio o `--workbook-credentials` (ADC apunta a ese archivo durante el registro); sin ninguno de los dos y con el inicio de sesión propio, registra la clase con `drive_workbook.user_service` sin tocar `GOOGLE_APPLICATION_CREDENTIALS`. Un `--workbook-credentials` explícito que no existe sigue fallando con «No existe la credencial local del Drive personal.» sin probar otra identidad.

## Estructura actualmente creada

```text
Academic Manager/
├── Academic Manager - Database.xlsx
└── 2026-2/
    ├── Gestión de Bases de Datos/
    ├── Inglés III/
    ├── SER (BE-IT)/
    ├── Sistemas Operativos II/
    └── Sistemas Transaccionales/
```

## Regla sobre la estructura interna de las materias

La estructura interna sigue definiéndose de forma incremental. Ya está confirmado el primer bloque real para clases procesadas:

```text
Academic Manager/
└── <semestre>/
    └── <materia>/
        └── Clases/
            └── <nombre de la grabación>/
                ├── metadata.json
                ├── transcript.md
                ├── transcript.json
                └── analysis.md
```

La carpeta de cada clase toma como base el **nombre real de la grabación** obtenido desde la fuente. La normalización exacta de caracteres inválidos, duplicados y nombres excesivamente largos se resolverá en Capture sin perder el nombre original, que también debe conservarse en `metadata.json`.

No se crea por anticipado una jerarquía completa de Actividades, Recursos, Evaluaciones u otras categorías: se incorporarán solo cuando aparezca una necesidad real.

## Qué debe almacenarse como archivo

Los archivos son apropiados para información extensa o documental, por ejemplo:

- transcripciones;
- análisis;
- PDFs;
- presentaciones;
- rúbricas;
- instrucciones de trabajos;
- apuntes;
- recursos;
- archivos producidos en una materia;
- documentos originales.

## Qué no debe depender exclusivamente de un chat

Información como la siguiente debe persistirse fuera de la conversación cuando sea relevante:

- requisitos de un trabajo;
- fechas importantes;
- estado de una actividad;
- análisis de una clase;
- ubicación de un recurso;
- decisiones académicas importantes;
- progreso de una materia.

## Formatos preferidos

Cuando Academic Manager genere información destinada a ser reutilizada por IA, se prefieren formatos abiertos y fáciles de leer:

- `.md` para texto estructurado;
- `.json` para metadatos y datos de máquina;
- `.xlsx` para la base estructurada actual.

Los archivos originales pueden conservar su formato natural (`.pdf`, `.pptx`, `.docx`, audio, video, etc.).

## Consideración de seguridad

La restricción de `allowedDirectories` de Desktop Commander protege las operaciones normales de archivos, pero no debe tratarse conceptualmente como una sandbox total del sistema operativo.

Regla operativa actual:

> Usar Desktop Commander para Academic Manager únicamente dentro de la carpeta autorizada y ejecutar comandos locales solo cuando exista una necesidad concreta y controlada.

## Cambio futuro de carpeta

Si Academic Manager cambia de ubicación, deberán actualizarse simultáneamente:

1. la ruta física del sistema;
2. `allowedDirectories` en Desktop Commander;
3. las rutas registradas en la base de datos que dependan de la ubicación anterior.
