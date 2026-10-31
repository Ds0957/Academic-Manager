# 37 — Comprobación del equipo para Capture

**Estado:** implementado y probado localmente (WSL); pendiente de validar en un PC Windows sin tarjeta gráfica.

## Para qué sirve

Antes de instalar WhisperX, `academic_setup check` dice si un PC puede transcribir clases con Capture y cómo. Está pensado para una persona no técnica: el resultado se lee en español con `--texto`, y el JSON sirve al futuro instalador (fase 1, S5) para decidir si instala WhisperX.

Es de **solo lectura**: no instala nada, no cambia el sistema y no escribe en la carpeta de datos. La única excepción es `--benchmark`, que guarda el modelo descargado en la caché de Capture.

## Uso

Desde la carpeta del repositorio, con cualquier Python 3 (solo usa la biblioteca estándar):

```powershell
python -m academic_setup check --texto
python -m academic_setup check
python -m academic_setup check --benchmark --texto
python -m academic_setup check --runtime D:\AcademicManagerData
```

- Sin `--texto` devuelve JSON con la misma forma que `academic_access`: `{"ok": true, "schema_version": 1, "action": "check", "data": {...}}`. Un parámetro desconocido devuelve `{"ok": false, "error": {"code": "INVALID_ARGUMENT", ...}}` y código de salida 2.
- `--runtime` indica la carpeta de datos cuyo disco se mide. Por defecto: la variable `ACADEMIC_MANAGER_DATA`; si no existe, `runtime` del archivo de configuración del usuario ([03-almacenamiento-y-seguridad.md](03-almacenamiento-y-seguridad.md)); si tampoco, `C:\AcademicManagerData` en Windows o la carpeta actual en otros sistemas. Si la carpeta aún no existe se mide la unidad donde quedaría.
- Un veredicto `no_apto` no es un error: la comprobación sale con código 0.

## Qué comprueba

| Dato | Cómo se obtiene |
|---|---|
| Sistema operativo | `platform` |
| Núcleos del procesador | `os.cpu_count()` |
| RAM total | Windows: `GlobalMemoryStatusEx` (`ctypes`); otros: `/proc/meminfo` o `os.sysconf` |
| Espacio libre | `shutil.disk_usage` sobre la carpeta de datos |
| GPU NVIDIA y VRAM | `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits` (máximo 15 s). Si `nvidia-smi` no existe, no hay GPU NVIDIA utilizable |
| CUDA en PyTorch | `torch.cuda` solo si PyTorch ya está instalado; si `nvidia-smi` no informa GPU pero PyTorch sí, se usa la VRAM de PyTorch |
| WhisperX, FFmpeg, yt-dlp | si el módulo se puede importar (sin importarlo) y si `ffmpeg` está en el `PATH` |

Cada dato es `{"valor": ...}`. Si una sonda falla (tiempo agotado, permiso, formato raro) el dato es `{"valor": "desconocido", "motivo": "..."}` y la comprobación sigue: nunca termina con un error por un fallo de sonda.

## Umbrales

Constantes con nombre en `academic_setup/check.py`:

| Constante | Valor | Significado |
|---|---|---|
| `MIN_GPU_VRAM_GB` | 4 | VRAM mínima de la GPU NVIDIA |
| `MIN_DISK_GPU_GB` | 12 | disco libre con GPU (PyTorch con CUDA, `large-v3`, temporales) |
| `MIN_CPU_RAM_GB` | 8 | RAM mínima para transcribir con el procesador |
| `MIN_CPU_CORES` | 4 | núcleos mínimos sin GPU |
| `MIN_DISK_CPU_GB` | 5 | disco libre sin GPU (PyTorch para CPU, modelo ligero, temporales) |
| `MEDIUM_MODEL_RAM_GB` | 16 | sin GPU, RAM desde la que se recomienda `medium` en lugar de `small` |

Son supuestos razonables, no medidas: se ajustan cuando haya datos reales de equipos.

## Veredictos

1. **`apto_gpu`**: una GPU NVIDIA con al menos 4 GB de VRAM y al menos 12 GB libres. Recomienda `large-v3` con `--device auto`. Si PyTorch ya está instalado sin CUDA, lo avisa en los motivos.
2. **`solo_cpu`**: sin GPU apta (o con GPU pero sin 12 GB libres), con al menos 8 GB de RAM, 4 núcleos y 5 GB libres. Recomienda `--device cpu` y un modelo más ligero: `medium` con 16 GB de RAM o más, `small` en otro caso.
3. **`no_apto`**: no cumple lo anterior. Sin recomendación de modelo.

Un dato desconocido que el veredicto necesita cuenta como no cumplido y aparece en los motivos como «No se pudo comprobar ...»: la comprobación nunca declara apto un equipo que no pudo medir.

En los tres casos `siempre_funciona` recuerda lo que no necesita WhisperX: material de YouTube con subtítulos manuales del autor (requiere yt-dlp) e importar transcripciones existentes con `--transcript`.

Para aplicar la recomendación en Capture: `--device cpu --model small` (ver `docs/05-capture.md`, «Modelo WhisperX»).

## Medición de velocidad (`--benchmark`)

No se adivina la velocidad. Con `--benchmark`:

- Si WhisperX no está instalado, `benchmark` dice `"disponible": false` y el motivo.
- Si está instalado, genera en una carpeta temporal una muestra sintética de 60 s con forma de habla (vocales con formantes y sílabas, sin palabras), carga el modelo recomendado con una muestra de calentamiento de 1 s (la primera vez lo descarga a `<datos>\procesamiento\model-cache`, la caché de Capture) y mide la transcripción real de la muestra con el mismo motor que Capture.
- Informa `factor_tiempo_real` (segundos de proceso por segundo de audio) y `estimacion_clase_90_min_min`. **Mide velocidad, no precisión.**
- Si el detector de voz no encuentra voz en la muestra, o WhisperX falla, informa que la medición no está disponible en lugar de inventar una cifra.
- La carpeta temporal se borra siempre; lo que WhisperX escribe en pantalla va a stderr para no romper el JSON.

## Ejemplo de `--texto`

PC con Windows sin tarjeta gráfica, vendido con 8 GB de RAM (Windows informa 7.8 GB) y 4 núcleos:

```text
Comprobación del equipo para Capture (solo lectura)

Sistema: Windows 11 (AMD64)
Procesador: 4 tareas en paralelo
Memoria RAM: 7.8 GB
Espacio libre en disco (C:\): 40.0 GB
Tarjeta gráfica compatible (NVIDIA): no tiene
Programas: WhisperX, que transcribe (falta instalar) · FFmpeg, que lee audio y video (instalado) · yt-dlp, para YouTube (instalado)

Resultado: SOLO CPU — Capture puede transcribir con el procesador; tarda más que con tarjeta gráfica.
Motivos:
  - No hay tarjeta gráfica NVIDIA: WhisperX usaría el procesador.
  - El procesador y los 7.8 GB de RAM permiten transcribir sin tarjeta gráfica con un modelo más ligero («small»).
Recomendación: transcribir con el modelo «small».
Falta instalar WhisperX para transcribir en este equipo; lo instala el instalador de Academic Manager.

Funciona siempre, aunque el equipo no sea apto:
  - Material de YouTube cuyo autor publicó subtítulos manuales: se importan sin WhisperX ni tarjeta gráfica (requiere yt-dlp).
  - Importar transcripciones que ya tengas (archivos de texto o Markdown).

Medición de velocidad: no disponible — WhisperX no está instalado; la velocidad no se adivina.
```

Con GPU apta la línea de resultado es `APTO CON TARJETA GRÁFICA — ...` y la recomendación `--device auto --model large-v3`; un equipo `no_apto` muestra `NO APTO — ...` y los motivos (por ejemplo, «Memoria RAM de 4.0 GB: transcribir con el procesador necesita al menos 8 GB.»).

## Pruebas

```bash
python3 -m unittest tests.test_academic_setup
```

Simulan `nvidia-smi` (presente, ausente, con error y con tiempo agotado), RAM, núcleos, disco, PyTorch y WhisperX ausentes o presentes.

**Margen de RAM:** Windows informa algo menos memoria de la instalada (un equipo vendido con 8 GB suele mostrar 7,4–7,9 GB), así que la RAM medida se compara con los mínimos sumando `RAM_MARGIN_GB` = 0,6 GB (también para elegir «medium» con 16 GB). El resumen `--texto` evita términos técnicos para que lo entienda una persona no técnica; los datos técnicos (`--device`, `--model`, umbrales) siguen en la salida JSON.
