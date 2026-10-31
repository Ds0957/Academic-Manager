# 05 — Academic Manager Capture

**Estado:** Capture V0 implementado y validado con dos grabaciones reales completas (SER y GBD); la tecnología/interfaz definitiva todavía no está cerrada.

## Objetivo

Academic Manager Capture debe encargarse de convertir grabaciones, audio o video en información utilizable por Academic Manager, con una interfaz simple y guiada.

No debe limitarse exclusivamente a clases. También debe admitir otros tipos de contenido, por ejemplo:

- entrevistas;
- investigaciones;
- reuniones;
- material audiovisual de apoyo;
- otros registros que necesiten transcripción.

## Experiencia deseada de primer uso

En la primera ejecución, Capture debería permitir configurar de forma clara:

- permisos necesarios;
- cuenta o fuente de archivos;
- carpeta raíz de Academic Manager;
- semestre activo;
- materias disponibles;
- tipos de contenido;
- motor/modelo de transcripción;
- credenciales o tokens únicamente si son necesarios;
- preferencias de salida.

La intención es evitar pedir la misma configuración en cada uso diario.

## Flujo diario deseado

```text
1. Abrir Capture
2. Pegar enlace o seleccionar archivo
3. Detectar metadata automáticamente
4. Elegir tipo de contenido
5. Elegir materia/proyecto/destino
6. Transcribir
7. Guardar solo los archivos necesarios
8. Registrar el resultado en Academic Manager
```

### Detección automática

Cuando la fuente lo permita, Capture debería obtener automáticamente:

- nombre del archivo;
- tipo;
- fecha;
- duración;
- tamaño;
- origen.

Si no se puede recuperar un nombre fiable, se debe usar un nombre de fallback basado en la fecha y hora de procesamiento.

## Tipos de contenido

Como mínimo, el diseño debe contemplar:

- `Clase`
- `Material` (material de estudio de una materia, implementado 2026-10-25; ver más abajo)
- `Entrevista`
- `Investigación`
- `Otro`

El destino no debe forzar que todo pertenezca a una materia.

## Salidas y estructura confirmadas

Para contenido de tipo `Clase`, la estructura persistente confirmada es:

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

El nombre de la carpeta se deriva del nombre real de la grabación. Capture deberá sanear únicamente lo necesario para que sea válido en el sistema de archivos y conservar el nombre original en metadata.

Las salidas persistentes por defecto son:

```text
metadata.json
transcript.md
transcript.json
analysis.md
```

`analysis.md` puede crearse después de la transcripción si el análisis se ejecuta en una etapa posterior.

Formatos como SRT, VTT o TSV podrían existir como exportaciones opcionales, no como salida obligatoria.

### `transcript.md`

Versión legible para humanos e IA, idealmente con marcas de tiempo cuando aporten valor.

### `transcript.json`

Versión estructurada para automatizaciones futuras.

### `metadata.json`

Información sobre procedencia, fecha, fuente, tipo de contenido y otros datos operativos.

## Implementación V0

El código de Capture ya existe en el repositorio bajo:

```text
capture/
├── capture.py
├── config.py
├── models.py
├── sources/
├── media/
├── transcription/
├── storage/
├── analysis/
└── academic_manager/
```

Incluye soporte para:
- fuente Google Drive, archivo local o video de YouTube;
- material de estudio (`--tipo material`) además de clases;
- importación de transcripción existente como fallback;
- OAuth local de solo lectura para las grabaciones de Google Drive;
- recuperación mediante `job.json`;
- descarga temporal por chunks;
- validación de media;
- extracción WAV mono 16 kHz con FFmpeg;
- WhisperX `large-v3` con CUDA y fallbacks (o `medium`/`small` con `--model`);
- fragmentos de prueba con timestamps referidos al medio original;
- generación y validación de `metadata.json`, `transcript.json`, `transcript.md` y `analysis.md`;
- publicación de artefactos de texto a la estructura académica;
- limpieza segura de audiovisuales temporales después de validar hashes;
- integración con `03 - Clases` mediante el protocolo completo del workbook;
- publicación binaria del workbook por Google Drive API;
- registro idempotente para evitar duplicar una grabación ya registrada.

## Tratamiento de la grabación original

La grabación original **no se conserva de forma permanente dentro de Academic Manager por defecto**.

El flujo confirmado es:

```text
fuente remota / archivo seleccionado
        ↓
descarga temporal
        ↓
validación
        ↓
extracción de audio / transcripción
        ↓
generación de archivos persistentes
        ↓
validación de resultados
        ↓
eliminación automática de la copia temporal
```

La eliminación temporal solo debe ejecutarse después de comprobar que los artefactos necesarios fueron generados correctamente. Si el procesamiento falla, Capture debe conservar suficiente información diagnóstica y no borrar a ciegas evidencia necesaria para reintentar.

Los temporales operativos deben vivir fuera de Google Drive, preferentemente bajo `C:\AcademicManagerData\temporales` o `C:\AcademicManagerData\procesamiento`, para evitar sincronizar archivos audiovisuales grandes innecesariamente.

## Limitación conocida: Google Drive

El error `403 cannotDownloadFile` no se resuelve con una interfaz distinta. Si Google Drive no permite descargar un archivo con la identidad autorizada, Capture debe manejar el caso de forma explícita.

Fallbacks deseados:

- importar una transcripción existente;
- seleccionar un archivo local;
- informar claramente que la fuente no permite descarga;
- evitar terminar con una traza técnica de Python como experiencia de usuario.

## Integración con Academic Manager

La primera grabación completa real procesada fue SER (BE-IT), 1 de octubre de 2026:

- nombre fuente: `SER  - 2026/10/01 19:50 GMT-05:00 - Recording`;
- tamaño: 777029390 bytes;
- duración: 3459.5 s (~57:39);
- WhisperX `large-v3`, CUDA, float16, batch 4;
- 111 segmentos;
- transcripción completa validada por SHA-256;
- análisis académico revisado globalmente;
- temporales audiovisual/WAV eliminados después de validar;
- artefactos persistentes publicados en `2026-2/SER (BE-IT)/Clases/<nombre saneado>/`.

La transcripción demostró calidad suficiente para reconstruir estructura, anuncios y temas principales. Para citas literales, nombres propios o decisiones sensibles se mantiene la regla de contrastar el audio.

La estructura de `analysis.md` se redefinió para priorizar la utilidad real para el estudiante. Debe responder principalmente: qué debo saber, qué debo recordar, qué debo hacer y qué cambió o quedó pendiente.

Estructura confirmada para clases:
1. Lo esencial de esta clase
2. Lo que tienes que saber
3. Lo que debes recordar
4. Qué tienes que hacer
5. Evaluaciones, entregas y notas
6. Decisiones y cambios importantes
7. Ejemplos y explicaciones del profesor
8. Materiales y recursos mencionados
9. Dudas, preguntas y puntos abiertos
10. Próxima clase
11. Importancia secundaria
12. Mapa rápido de la clase
13. Fiabilidad del análisis

Regla de prioridad: conceptos académicos, evaluaciones, obligaciones, fechas, cambios e instrucciones explícitas reciben detalle alto; ejemplos útiles, preguntas y recursos reciben detalle medio; anécdotas, repeticiones e incidencias sin consecuencias se comprimen fuertemente. El tamaño depende de la densidad académica, no de la duración de la grabación. `transcript.md` conserva lo dicho; `analysis.md` destila lo que el estudiante necesita comprender, retener y ejecutar.

El `analysis.md` de la primera clase de SER fue rehecho con esta estructura y publicado sustituyendo la versión anterior, con verificación SHA-256.

La primera clase ya quedó registrada en `03 - Clases` como `CLS-SER-20261001-01`. El módulo `capture/academic_manager/database.py` implementa lock, hash base, checkpoint, edición local, validación, publicación por Drive API, re-descarga, revalidación con Excel, realineación local y liberación del lock.

Capture permite registrar un job existente mediante `--resume ... --register-database`. La prueba sobre el job SER confirmó idempotencia: al encontrar la grabación ya registrada devolvió `CLS-SER-20261001-01` sin crear una segunda fila.

`metadata.json` deja de duplicar un `database_status` mutable; la base es la fuente persistente de verdad para saber si una clase está registrada.

### Segunda validación real: Gestión de Bases de Datos

El 2026-10-07 se procesó la grabación `4146_GESTION DE BASES DE DATOS - 2026/10/01 17:46 GMT-05:00 - Recording` (Drive file ID `1SampleRecordingFileId-Docs000001`). Job `d61e6976c98749e48d964b2115fef3bc`.

- Archivo MP4: **269179845 bytes**; duración **2616.33 s** (~43:36).
- WhisperX `large-v3`, GPU CUDA, `float16`, **90 segmentos** de transcripción; medio audiovisual temporal eliminado tras validar.
- `analysis.md` pasó de marcador automático `pending` a análisis académico completo **revisado contra los 90 segmentos**, orientado al estudiante, con 13 secciones y dudas explícitas para verificar en Canvas.
- Los cuatro artefactos se publicaron en `2026-2/Gestión de Bases de Datos/Clases/4146_GESTION DE BASES DE DATOS - 2026_10_01 17_46 GMT-05_00 - Recording/`, y se releyeron para validar SHA-256.
- Registro nuevo confirmado en `03 - Clases`: **`CLS-GBD-20261001-01`** (`MAT-GBD`), sin duplicación; `publication_path` y hashes persistidos en `job.json`.
- Publicación del workbook por Google Drive API con lock, checkpoint, re-descarga, SHA-256 y validación con Excel. Resultado: `status=free`, `lock=null`, SHA-256 `9b4686373de57881b36a5712b70df263f98272a5392508c662fd2a21189962c8` y tamaño `88059` bytes. La verificación integrada independiente confirmó el mismo hash desde Drive y la nueva fila.

**Incidencia de conectividad:** mientras el archivo se descargaba, Desktop Commander Remote perdió su conexión externa (`HTTP 525` en comunicación Cloudflare/Supabase y errores de presencia/conexiones). Capture **continuó ejecutándose localmente** y terminó antes de recuperar el canal. Se recuperó el mismo job, sin descargar/transcribir de nuevo. Esto valida en este caso la independencia de ejecución local, no la disponibilidad del servicio remoto. Para futuras sesiones largas, queda pendiente evaluar registros de consola locales y supervisión recuperable sin depender de la conexión MCP.

**Incidencia local separada:** al sustituir el análisis pendiente y actualizar `metadata.json`, dos intentos de `atomic_json` fallaron en Windows por `PermissionError: [WinError 5]` al reemplazar un archivo ya existente; la sustitución de archivos temporales entre sí funcionó. Se conservó respaldo local de `metadata.json` y `job.json` y se actualizaron sus contenidos con validación posterior. **La causa del bloqueo de reemplazo queda pendiente de investigar**; no debe generalizarse este procedimiento manual como mecanismo de producción.

### Progreso observable de Capture y tiempo orientativo (2026-10-08)

**Implementado en el código para nuevos jobs:** WhisperX transmite el porcentaje real de fragmentos de voz procesados mediante su `progress_callback` a `procesamiento/<job-id>/progress.json` (actualización local atómica y acotada). Si existe progreso suficiente se calcula una **ETA orientativa de la fase ASR**, no un plazo garantizado de análisis/publicación. El monitor ofrece `--status all`, `--status latest` y `--status latest --watch 15`; Ctrl+C cancela solo la pantalla de seguimiento. El Coordinador puede consultar `academic_access capture-jobs` en JSON, con estado de publicación y registro diferenciados.

**Caso real inspeccionado:** SO2, grabación 2026-09-29, job `83ac7042104d4ce6bb793543714f6fb5`, concluyó transcripción local y validación a las `2026-10-08T21:57:15Z`; en la inspección **no figuraba publicado ni registrado en Excel**. No se observó otra transcripción nueva activa en ese PC. El porcentaje/ETA nuevo aún requiere validación en una transcripción **posterior** a esta implementación. Ver `docs/30-capture-monitoreo-progreso.md`.

### Observabilidad local y recuperación ante desconexiones — implementado 2026-10-07

**Confirmado e implementado:** los nuevos jobs Capture escriben en su carpeta local `C:\AcademicManagerData\procesamiento\<job-id>\events.jsonl` un historial **append-only** con fecha, ID y etapa. `job.json` sigue siendo la fuente de verdad del estado recuperable; el historial es evidencia auxiliar. Se registran los checkpoints persistidos y los inicios de etapas largas (`downloading`, `extracting_audio`, `transcribing`, `publishing`, `registering_database`). Los eventos se fuerzan a disco (`flush` + `fsync`) y **no guardan credenciales ni tokens**.

Inspección segura, de solo lectura y sin ejecutar de nuevo el pipeline:

```powershell
cd C:\AcademicManagerDev\Academic-Manager
C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe -m capture --runtime C:\AcademicManagerData --status latest
# O seleccionar explícitamente un ID conocido:
C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe -m capture --runtime C:\AcademicManagerData --status d61e6976c98749e48d964b2115fef3bc
```

El comando muestra estado, actualización, publicación, registro de clase y últimas ocho etapas, **sin tomar el lock ni reanudar el job**. Es útil tras recuperar Remote Desktop Commander. Los jobs creados antes de esta implementación no disponen de historial antiguo; su estado sigue disponible en `job.json`.

**Protección Windows:** `atomic_json` conserva la estrategia de archivo temporal y `os.replace`, con hasta cuatro reintentos y pausas acotadas cuando el destino existente devuelve `PermissionError`. Si el bloqueo persiste, la operación **falla sin truncar ni reemplazar** el JSON anterior. En una prueba integrada aislada sobre Windows se abrió un destino sin permiso compartido de borrado (sin `FILE_SHARE_DELETE`), se observaron denegaciones de reemplazo y se comprobó que la actualización funciona al liberar el handle. Esto **demuestra un mecanismo compatible con el error previo, no identifica retrospectivamente qué programa mantenía abierto `metadata.json`**.

**Límites:** no se implementó un heartbeat periódico, captura íntegra de stdout/stderr de dependencias externas, ejecución como servicio ni recuperación automática ante reinicio del PC. Una desconexión del canal remoto no equivale necesariamente a un fallo local; para comprobar si un job sigue vivo debe complementarse el historial con el estado del proceso en el PC. Ninguno de estos cambios vuelve a descargar, transcribir o modificar clases existentes.

## Fuente YouTube y material de estudio (2026-10-25)

Issue #17. Capture extrae la transcripción de **un video de YouTube** por URL o de **un video/audio local** del PC, y puede guardarlo como **material de estudio** de una materia.

```bat
scripts\capture.cmd --source "https://www.youtube.com/watch?v=ID" --tipo material --subject-id MAT-SER --semester 2026-2 --subject "SER (BE-IT)" --publish --drive-root "G:\Mi unidad\03 - Universidad\Academic Manager"
scripts\capture.cmd --source "C:/downloads/video apoyo.mp4" --tipo material --subject-id MAT-SER --semester 2026-2 --subject "SER (BE-IT)"
```

`scripts\capture.cmd` ejecuta `python -m capture` con el Python de `C:\AcademicManagerData\procesamiento\capture-env` y añade su carpeta `Scripts` al `PATH` (allí queda `deno`). Acepta las mismas opciones que `python -m capture --help`.

### YouTube

- URLs admitidas: `youtube.com/watch?v=`, `m.youtube.com/watch?v=`, `youtu.be/` y `youtube.com/shorts/`. Se reconocen **antes** que la regla de Drive; las URLs y los ID de Drive y los archivos locales funcionan igual que antes.
- Solo un video público o no listado. Se rechazan con un error claro: listas de reproducción (también `watch?v=...&list=`), directos o programados, videos privados, solo para miembros, de pago o con restricción de edad, y videos de más de **4 horas**. Capture **no usa cookies ni cuentas**.
- Texto: si el autor publicó **subtítulos manuales** en `--language` (por ejemplo `es`, o una variante regional como `es-419`), se descargan y entran por el mismo importador que `--transcript`, sin GPU ni WhisperX. Si no existen, se descarga **solo el audio** (`bestaudio`) y se transcribe con WhisperX como una grabación local. Los subtítulos **automáticos** de YouTube nunca se usan.
- Los subtítulos manuales no se recortan: `--start`/`--duration` solo valen cuando se transcribe el audio.
- `metadata.json` guarda en `source`: `video_id`, `title`, `channel`, `duration_seconds`, `upload_date`, `url`, `subtitle_language` y `text_source` (`manual_subtitles` o `audio_whisperx`).
- El audio y los subtítulos descargados quedan fijados por SHA-256 en `job.json`: `--resume` no los descarga de nuevo y, si hay que volver a descargarlos, rechaza contenido distinto.

### Material de estudio (`--tipo material`)

- `--tipo clase` es el valor por defecto; sin la opción, el flujo de clases no cambia.
- `--tipo material` exige `--subject-id MAT-...`, no admite `--class-date`, no genera ID `CLS-` y no se registra en `03 - Clases` (`--register-database` se rechaza).
- Se publica en `<semestre>/<materia>/Material/<título>/` con los mismos cuatro artefactos; `metadata.json` lleva `content_type: "Material"` y `subject_id`. `--subject` debe ser el nombre de la materia tal como figura en `01 - Materias`.
- Tras `scripts\knowledge.cmd build`, el material aparece en `academic-read search` y `academic-read source` de esa materia (ver `25-indice-conocimiento-academico-v1.md`).
- Rutas locales de Windows con `/` o `\` (`C:/downloads/...`, `C:\downloads\...`) funcionan con ambos tipos.

### Dependencia yt-dlp

`requirements-capture.txt` fija `yt-dlp[pin,pin-deno]==2026.10.21` (versión estable publicada en PyPI el 2026-10-21). El extra `pin` fija sus dependencias, incluido `yt-dlp-ejs`, y `pin-deno` instala el runtime JavaScript `deno` que YouTube exige. Capture importa yt-dlp solo al usar una URL de YouTube; si falta, el job falla con un mensaje que pide instalar `requirements-capture.txt`.

Para actualizarlo, cuando YouTube deje de funcionar o salga una versión nueva:

1. Consultar la versión estable en https://github.com/yt-dlp/yt-dlp/releases (no las `.dev`).
2. Cambiar la versión exacta en `requirements-capture.txt` en una rama y PR propios.
3. Instalar en el entorno de Capture: `C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe -m pip install -r requirements-capture.txt`.
4. Probar un video público corto con y sin subtítulos manuales y ejecutar `python -m unittest tests.test_capture_youtube`.

### Modelo WhisperX (`--model`, 2026-10-25)

`--model {large-v3,medium,small}` elige el modelo al crear un job con `--source`. Sin la opción se usa `large-v3`, como hasta ahora.

- Se guarda en `job.json` (`request.model`) y en el bloque `transcription` de `metadata.json` (`model`), igual que antes.
- Un job reanudado conserva su modelo; los jobs creados antes de esta opción no tienen `request.model` y se reanudan con `large-v3`.
- `--model` junto a `--resume`, `--status`, `--transcript` o `--login-drive` se rechaza: no se ignora en silencio.
- `--device` no cambia: `auto` (por defecto), `cuda` o `cpu`, con los mismos intentos CUDA y la caída a CPU `int8`.
- `medium` y `small` son más rápidos sin GPU y menos precisos. Para saber cuál conviene en un PC concreto: `python -m academic_setup check --texto` (ver `docs/37-comprobacion-equipo.md`).

```powershell
scripts\capture.cmd --source "C:/clases/clase.mp4" --semester 2026-2 --subject "SER (BE-IT)" --device cpu --model small
```

## Tecnología final

**Pendiente.**

Se ha considerado reutilizar el backend existente y añadir una interfaz amigable, pero todavía no se ha fijado si la versión definitiva será:

- aplicación local;
- interfaz web local;
- Colab con UI;
- otra alternativa.

La prioridad es validar primero el flujo funcional.


## Incorporación de análisis revisado antes de publicar (2026-10-12)

**Implementado en el núcleo; validado con jobs y fuentes sintéticas.** El issue [#2](https://github.com/Ds0957/Academic-Manager/issues/2) se resuelve mediante una operación separada para un job con artefactos transcritos y validados, todavía **no publicado ni registrado**. El análisis sigue siendo redactado y revisado por un humano (p. ej., desde el Coordinador). Capture **no produce ni verifica semánticamente** el contenido académico: comprueba requisitos básicos de Markdown, rechaza el marcador pendiente y exige confirmación explícita de revisión.

El flujo es:

1. Consultar \`python -m capture --runtime C:\AcademicManagerData --status JOB_ID\`. Allí se muestra la huella SHA-256 exacta de \`transcript.json\` y el estado del análisis. No iniciar \`--resume\` solo para consultar el progreso.
2. Preparar fuera del job un Markdown UTF-8 revisado, con título, secciones y contenido sustantivo (máximo 1 MiB). Contrastar sus afirmaciones con la transcripción correspondiente.
3. Adjuntar el documento explícitamente:

\`\`\`powershell
python -m capture --runtime C:\AcademicManagerData --resume JOB_ID --attach-analysis "C:\ruta-autorizada\analisis-revisado.md" --analysis-transcript-sha256 SHA256_DE_TRANSCRIPT_JSON --analysis-reviewed
\`\`\`

4. Volver a consultar \`--status JOB_ID\`, comprobar \`Análisis: reviewed\` y reanudar **en una ejecución posterior** con los parámetros habituales \`--publish --drive-root ...\`. Luego usar el protocolo existente de \`--register-database\` para registrar la clase en Excel. No se permite adjuntar y publicar/registrar en la misma invocación.

### Garantías

- Se exige \`--resume\` con ID existente y una huella SHA-256 igual al \`transcript.json\` ya validado. No se modifica la transcripción.
- El análisis se copia dentro de un conjunto candidato de los **cuatro** artefactos, se actualiza \`metadata.analysis_status=reviewed\` con \`job_id\`, SHA de la transcripción, SHA del análisis, fecha y constancia \`human_confirmed\`. \`validate()\` exige concordancia entre procedencia, contenido y metadatos.
- Se mantiene un diario local con los hashes anteriores y nuevos para recuperar una interrupción durante el intercambio de directorios: se completa el cambio o se restaura el conjunto original al reanudar con lock exclusivo.
- La misma incorporación es idempotente; un nuevo informe revisado puede reemplazar al anterior **solo mientras el job no esté publicado**. Nunca se sobreescribe una clase publicada o registrada. No se altera automáticamente Canvas, Drive ni Excel.
- Un informe inválido, fuente equivocada o estado ya publicado provoca rechazo sin escribir artefactos nuevos. Una transacción interrumpida no recuperable conserva los respaldos para revisión humana; no se fuerza borrado de evidencia.
- Jobs históricos con \`analysis_status=pending\` continúan siendo válidos como transcripciones; **no** se consideran análisis terminados por el solo hecho de estar publicados.

**Validación inicial:** 13 pruebas sintéticas nuevas (aprobación, fuente, conflicto, idempotencia, revisión posterior, procedencia, recuperación/rollback y publicación/registro simulado), además de regresión Capture existente. **Pendiente operativo:** usar la operación sobre un job real local con informe efectivamente revisado y confirmar después la publicación/registro efectivos contra Google Drive y Excel sin alterar el protocolo compartido.

## Validación operativa de análisis revisados y registro (2026-10-12)

**Confirmado en PC Diego:** se incorporaron mediante `--attach-analysis` dos informes académicos preparados y revisados por el Coordinador, sin retranscribir:
- SO2, sesión 2026-09-29, job `83ac7042104d4ce6bb793543714f6fb5`: `analysis_status=reviewed`, publicado y registrado como `CLS-SO2-20260929-01`.
- Sistemas Transaccionales, sesión 2026-09-30, job `e0eb990002ab423b91d06ebac3dec9c0`: `analysis_status=reviewed`, publicado y registrado como `CLS-ST-20260930-01`.

Los cuatro artefactos de cada clase se publicaron en su carpeta `2026-2/<materia>/Clases/<grabación>/` a través del publicador existente, con verificación local por lectura y SHA-256. Se ejecutó el protocolo existente del workbook con registro de clases, re-descarga de Drive API, verificación SHA-256 y validación de Excel. Al final, el estado compartido quedó libre (`status=free`, `lock=null`), con SHA-256 canónico y local coincidentes: `4505ff3be59ad3a366243ec41afbcff5018d705e3a27701ac4e8750b733ad369`.

**Corrección detectada en primera integración:** el registrador heredado esperaba `job.analysis_status` que empezara por `completed`, mientras el nuevo flujo persistía `metadata.analysis_status=reviewed`. Se corrigió `capture/capture.py` para transmitir el estado `completed_reviewed` al registrador **solo después** de validar los artefactos y leer la metadata revisada. Se añadió aserción de regresión y pasaron 13 pruebas específicas. No se editaron a mano hashes ni metadatos de los jobs. El rechazo inicial de SO2 ocurrió antes de adquirir el lock del workbook.

**Alcance de la validación:** estructura, procedencia, hashes, publicación y registro confirmados. La revisión académica fue declarada en los informes por el Coordinador; Capture no certifica automáticamente la corrección semántica de cada afirmación ni el audio original.

### Robustez Windows en incorporación de análisis revisado (2026-10-12)

Al investigar el [issue #5](https://github.com/Ds0957/Academic-Manager/issues/5), la preparación de `analysis.md` alcanzó sin errores el punto previo al intercambio de directorios en un job real, sin modificarlo. Se incorporaron reintentos limitados a `PermissionError` en `stage.rename(previous)`, `next_dir.rename(stage)` y la restauración del respaldo, usando las protecciones actuales de diario, validación y rollback. No se reintenta automáticamente el registro de Excel ni la publicación remota. Se añadieron pruebas de denegación de acceso transitoria y persistente en Windows. El origen preciso del error antiguo no pudo verificarse porque el CLI lo había ocultado; la observabilidad nueva permite identificar la etapa.

### Fallback transaccional de Windows para el análisis revisado (2026-10-12)

**Redefinición incremental del mecanismo interno, sin modificar la interfaz pública de Capture:** una vez preparados y validados los cuatro artefactos candidatos, se conserva la ruta normal de intercambio de directorios. Si Windows rechaza de forma persistente **el renombrado del directorio original** con `PermissionError`, Capture intenta una alternativa limitada: realiza una copia validada de los artefactos originales, actualiza el diario de transacción a `mode=files` y reemplaza exclusivamente `analysis.md` y `metadata.json` mediante archivos temporales en el directorio original y `os.replace`, con reintentos acotados. **`transcript.json` y `transcript.md` nunca se reemplazan en esta alternativa.** El `job.json` se actualiza solamente después de validar el conjunto nuevo.

Si se produce una interrupción, `recover` reconoce el modo `files`: acepta y consolida el conjunto nuevo válido, descarta una operación que dejó el conjunto antiguo válido o restaura únicamente los archivos modificados desde la copia antigua comprobada, manteniendo el diario si no puede repararse con seguridad. No elimina respaldos inconsistentes ni fuerza autorizaciones del sistema operativo. Los campos sanitizados `error.stage` y opcionalmente `error.substage` permiten distinguir, por ejemplo, `rename_original`, `backup_artifacts`, `replace_analysis` y `replace_metadata` sin exponer rutas de archivos.

**Pruebas:** bloqueo persistente del directorio original, sustitución de dos archivos, inmutabilidad de las dos transcripciones, fallo persistente de sustitución y rollback, interrupción entre dos sustituciones y recuperación al reanudar. Se conservaron las protecciones de hash y procedencia anteriores. **Pendiente:** aceptación mediante `complete` autorizado de una clase real desde el Project Coordinador; el error Win32 5 histórico estaba localizado en la fase `attach_analysis`, no en una operación de sistema específica.

### Aceptación real del fallback Windows (2026-10-12)

Después de fusionar el PR #7, el Project Coordinador realizó una integración real autorizada de la clase de Sistemas Transaccionales del 8 de octubre. `complete` terminó con `ok=true`; el análisis quedó `reviewed`, los cuatro artefactos publicados y verificados por SHA-256, y el registro canónico creado una sola vez como `CLS-ST-20261008-01`. La consulta independiente en Diego confirmó `capture-jobs` correcto, dos clases en `MAT-ST` e índice `fresh=true`. **Esto valida la funcionalidad corregida en uso real**, pero no permite afirmar con certeza si en esa ejecución se activó la rama de fallback, ni cuál suboperación exacta provocó el bloqueo Win32 5 anterior. Ver `docs/31-operaciones-coordinador.md` y [issue #5](https://github.com/Ds0957/Academic-Manager/issues/5).
