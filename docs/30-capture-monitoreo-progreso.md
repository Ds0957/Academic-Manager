# 30 — Capture: progreso real, tiempos orientativos y estado para el Coordinador

**Fecha:** 2026-10-08. **Estado:** observabilidad implementada en código y probada con datos sintéticos; consulta de jobs anteriores reales validada; **el porcentaje/ETA del nuevo callback todavía no se ha probado en una nueva transcripción real**. No se modificaron Excel, Canvas ni Drive.

## Necesidad detectada en uso

El Coordinador recibió dos grabaciones para procesar y el estudiante no podía saber cuándo terminarían. `events.jsonl` solo señalaba comienzos y finales de fases largas; `job.json` no cambia mientras WhisperX está ejecutándose.

**Comprobación real en Diego el 2026-10-08:** un job de **Sistemas Operativos II del 29/09/2026**, ID `83ac7042104d4ce6bb793543714f6fb5`, descargó 651.560.068 bytes, preparó 3.346 s de audio y se transcribió. Terminó `completed` local a las `2026-10-08T21:57:15Z` (transcribing desde `21:52:32Z`, ~4 min 43 s). **No se observó otro job nuevo activo en este PC**; los demás eran los trabajos SER y GBD históricos. El job SO2 aún **no figuraba publicado ni registrado en Excel** en esa comprobación; no es correcto declarar que la clase completa ya está disponible para el Coordinador.

## Solución confirmada

- `capture/transcription/whisperx_engine.py` utiliza el **`progress_callback` de la versión WhisperX instalada**, que reporta porcentaje de fragmentos VAD transcritos. El modelo sigue siendo `large-v3` con la misma selección de dispositivo y tamaño de lote; no se segmenta manualmente el audio ni se altera la inferencia.
- `capture/storage/progress.py` guarda atomicamente `progress.json` junto a `job.json`; incluye `job_id`, `stage=transcribing`, `percent_phase`, timestamps, tiempo transcurrido y `estimated_remaining_seconds` **solo desde 10% y 15 s de observación real**. Se limita la frecuencia de escrituras. Un problema aislado al guardar el monitor no invalida la transcripción.
- La ETA extrapola el ritmo de fragmentos **solo de la fase ASR**, por lo que puede fluctuar. **No representa el tiempo total hasta analizar, publicar o registrar la clase**. Puede ser desconocida mientras se carga el modelo, al comienzo o si no hay señal reciente.
- `capture/storage/monitor.py` permite inspeccionar varios jobs en `--status all`, `--status latest` o con ID; lee `progress.json` y eventos sin adquirir lock ni reanudar la grabación. Un archivo de progreso antiguo no demuestra que siga ejecutándose: si la última actualización no es reciente, la ETA se oculta. Un job `completed` ignora cualquier progreso anterior y muestra publicación/registro por separado.
- `python -m capture --status latest --watch 15` muestra un **contador local actualizado cada 15 s** y se detiene con Ctrl+C **sin cancelar la transcripción**. `--watch` acepta de 5 a 300 segundos, nunca inicia ni modifica jobs.
- `academic_access capture-jobs` (o `--job latest`) añade lectura **JSON** para el futuro Coordinador; no expone credenciales, contenido íntegro de transcripciones ni requiere una escritura. El Project Coordinador tiene una instrucción nueva en `academic_projects/templates/coordinador.md` para consultar trabajos en vez de inferir su finalización.

## Consulta cotidiana

Desde el equipo Diego (repositorio en `C:\AcademicManagerDev\Academic-Manager`):

```powershell
# Todas las grabaciones conocidas, con su última fase observada:
C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe -m capture --runtime C:\AcademicManagerData --status all

# Detalle de la más reciente:
C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe -m capture --runtime C:\AcademicManagerData --status latest

# Contador de consola actualizado sin tocar al proceso real:
C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe -m capture --runtime C:\AcademicManagerData --status latest --watch 15

# Datos JSON para el Coordinador:
scripts\academic-read.cmd capture-jobs
scripts\academic-read.cmd capture-jobs --job latest
```

**Advertencia:** `--status` y `academic_access capture-jobs` no necesitan lanzar el trabajo de nuevo. Nunca iniciar `--resume` para saber cuánto falta; podría entrar en conflicto con el job original.

## Fases y fronteras

Etapas: `created → metadata_ready → downloading → downloaded → extracting_audio → audio_ready → transcribing (porcentaje) → transcribed → validated → published → database_registered → completed`. Los pasos de publicación y registro dependen de los flags concretos de la ejecución: `completed` con `published=false` y `database_registered=false` **no equivale** a clase disponible en Academic Manager. Capture puede terminar generando artefactos locales y requerir análisis y publicación posteriores.

La fase `downloading` sigue identificada por eventos, pero este incremento **no implementó porcentajes persistentes de descarga**: su barra fiable dentro de `progress.json` es para ASR. Tampoco existe porcentaje absoluto del análisis académico, que requiere interpretación contextual; no se fabricará.

## Validación y límites

- Pruebas sintéticas: callback WhisperX llega al modelo sin modificar la inferencia; a 25% y 60 s observados se calcula ETA ASR 180 s; terminal `completed` descarta ETA antigua; señal desactualizada oculta tiempo restante; listado múltiple e interfaz JSON son de solo lectura. **Regresión completa de desarrollo: 144 tests, 2 omitidos, `DEV_CHECK_OK`**.
- Prueba real de lectura: el Coordinador puede ver SO2 `completed`, `published=false`, `database_registered=false`; GBD y SER históricos `completed`, publicados y registrados. No se inventó otro job de las dos grabaciones solicitadas.
- No hay un dashboard visual dedicado ni notificaciones push; el contador está disponible al consultar o abrir un monitor local, sin depender de ChatGPT abierto. **Validación pendiente:** una nueva ejecución de WhisperX con este código que produzca progresos reales intermedios y comprobar su ETA con tiempos observados.
- La versión de instrucciones ya instalada manualmente en el futuro Project Coordinador **no se actualiza automáticamente** cuando cambia la plantilla del repositorio: el usuario debe trasladar esta función a sus instrucciones o consultar el documento. No modificar Projects desde el repositorio.

**Siguiente mejora solo si el uso la exige:** porcentaje de transferencia por bytes descargados, notificaciones o visualización más amigable desde una interfaz local; no son condiciones para observar el avance real de WhisperX.
