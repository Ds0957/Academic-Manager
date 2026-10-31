"""Veredicto de aptitud para Capture con WhisperX y resumen en español.

Los umbrales son constantes con nombre y están documentados en docs/37-comprobacion-equipo.md.
"""
from . import hardware

MIN_GPU_VRAM_GB = 4        # GPU NVIDIA utilizable por WhisperX (float16/int8_float16)
MIN_DISK_GPU_GB = 12       # PyTorch con CUDA + modelo large-v3 + temporales de un job
MIN_DISK_CPU_GB = 5        # PyTorch para CPU + modelo ligero + temporales de un job
MIN_CPU_RAM_GB = 8         # transcribir con el procesador
MIN_CPU_CORES = 4
MEDIUM_MODEL_RAM_GB = 16   # sin GPU: «medium» con al menos 16 GB de RAM; si no, «small»
RAM_MARGIN_GB = 0.6        # Windows informa ~7.4–7.9 GB en un equipo vendido con 8 GB

GPU_MODEL = "large-v3"
UNKNOWN = hardware.UNKNOWN

ALWAYS_WORKS = [
    "Material de YouTube cuyo autor publicó subtítulos manuales: se importan sin WhisperX ni "
    "tarjeta gráfica (requiere yt-dlp).",
    "Importar transcripciones que ya tengas (archivos de texto o Markdown).",
]

HEADLINES = {
    "apto_gpu": "APTO CON TARJETA GRÁFICA — Capture puede transcribir con la tarjeta gráfica (lo más rápido).",
    "solo_cpu": "SOLO CPU — Capture puede transcribir con el procesador; tarda más que con tarjeta gráfica.",
    "no_apto": "NO APTO — este equipo no debería transcribir con WhisperX; lo de abajo funciona igual.",
}


def thresholds():
    return {"min_gpu_vram_gb": MIN_GPU_VRAM_GB, "min_disco_gpu_gb": MIN_DISK_GPU_GB,
            "min_disco_cpu_gb": MIN_DISK_CPU_GB, "min_ram_cpu_gb": MIN_CPU_RAM_GB,
            "min_nucleos_cpu": MIN_CPU_CORES, "ram_modelo_medium_gb": MEDIUM_MODEL_RAM_GB}


def _detected_gpus(equipo):
    """GPUs de nvidia-smi; si no las informa, las que vea PyTorch con CUDA."""
    smi, torch = equipo["gpu_nvidia"], equipo["torch_cuda"]
    gpus = smi["valor"] if isinstance(smi["valor"], list) else []
    if not gpus and torch["valor"] is True:
        gpus = torch.get("gpus") or []
    return gpus


def _gpus(equipo, motivos):
    smi = equipo["gpu_nvidia"]
    if smi["valor"] == UNKNOWN:
        motivos.append(f"No se pudo comprobar la tarjeta gráfica NVIDIA: {smi['motivo']}")
    gpus = _detected_gpus(equipo)
    if not gpus and smi["valor"] != UNKNOWN:
        motivos.append("No hay tarjeta gráfica NVIDIA: WhisperX usaría el procesador.")
    return gpus


def _below(item, minimum, unknown, low, margin=0):
    """Motivo si el valor no alcanza el mínimo o no se pudo medir; None si cumple."""
    if item["valor"] == UNKNOWN:
        return f"No se pudo comprobar {unknown}: {item['motivo']}"
    return low.format(item["valor"]) if item["valor"] + margin < minimum else None


def evaluate(equipo):
    motivos = []
    disk = equipo["disco_libre_gb"]
    gpus = _gpus(equipo, motivos)
    best = max(gpus, key=lambda g: g["vram_gb"], default=None)
    if best and best["vram_gb"] >= MIN_GPU_VRAM_GB:
        problem = _below(disk, MIN_DISK_GPU_GB, "el espacio libre en disco",
                         "Hay tarjeta gráfica, pero el espacio libre en disco ({} GB) no alcanza los "
                         f"{MIN_DISK_GPU_GB} GB que necesita la instalación con GPU.")
        if problem is None:
            motivos.append(f"Tarjeta gráfica {best['nombre']} con {best['vram_gb']} GB de memoria "
                           f"propia (mínimo {MIN_GPU_VRAM_GB} GB).")
            if equipo["torch_cuda"]["valor"] is False:
                motivos.append("Falta el componente que usa la tarjeta gráfica (PyTorch con CUDA); "
                               "mientras tanto Capture usará el procesador.")
            return "apto_gpu", motivos, {"modelo": GPU_MODEL, "dispositivo": "auto"}
        motivos.append(problem)
    elif best:
        motivos.append(f"La tarjeta gráfica {best['nombre']} tiene {best['vram_gb']} GB de memoria "
                       f"propia; para transcribir con ella hacen falta al menos {MIN_GPU_VRAM_GB} GB.")
    ram, cores = equipo["ram_total_gb"], equipo["nucleos_cpu"]
    problems = [p for p in (
        _below(ram, MIN_CPU_RAM_GB, "la memoria RAM",
               "Memoria RAM de {} GB: transcribir con el procesador necesita al menos "
               f"{MIN_CPU_RAM_GB} GB.", margin=RAM_MARGIN_GB),
        _below(cores, MIN_CPU_CORES, "los núcleos del procesador",
               "Procesador de {} núcleos: transcribir sin GPU necesita al menos "
               f"{MIN_CPU_CORES} núcleos."),
        _below(disk, MIN_DISK_CPU_GB, "el espacio libre en disco",
               "Espacio libre en disco de {} GB: se necesitan al menos "
               f"{MIN_DISK_CPU_GB} GB."),
    ) if p]
    if problems:
        return "no_apto", motivos + problems, None
    model = "medium" if ram["valor"] + RAM_MARGIN_GB >= MEDIUM_MODEL_RAM_GB else "small"
    motivos.append(f"El procesador y los {ram['valor']} GB de RAM permiten transcribir sin tarjeta "
                   f"gráfica con un modelo más ligero («{model}»).")
    return "solo_cpu", motivos, {"modelo": model, "dispositivo": "cpu"}


def _show(item, fmt="{}"):
    if item["valor"] == UNKNOWN:
        return f"desconocido ({item['motivo']})"
    return fmt.format(item["valor"])


def _gpu_line(equipo):
    smi = equipo["gpu_nvidia"]
    gpus = _detected_gpus(equipo)
    if gpus:
        return ", ".join(f"{g['nombre']} con {g['vram_gb']} GB de memoria propia" for g in gpus)
    return _show(smi) if smi["valor"] == UNKNOWN else "no tiene"


def _yes(item):
    if item["valor"] == UNKNOWN:
        return "desconocido"
    return {True: "instalado", False: "falta instalar"}.get(item["valor"], str(item["valor"]))


def text(data):
    equipo, programas = data["equipo"], data["programas"]
    disk = equipo["disco_libre_gb"]
    lines = [
        "Comprobación del equipo para Capture (solo lectura)", "",
        f"Sistema: {_show(equipo['sistema'])}",
        f"Procesador: {_show(equipo['nucleos_cpu'], '{} tareas en paralelo')}",
        f"Memoria RAM: {_show(equipo['ram_total_gb'], '{} GB')}",
        f"Espacio libre en disco ({disk.get('unidad')}): {_show(disk, '{} GB')}",
        f"Tarjeta gráfica compatible (NVIDIA): {_gpu_line(equipo)}",
        f"Programas: WhisperX, que transcribe ({_yes(programas['whisperx'])}) · FFmpeg, que lee audio "
        f"y video ({_yes(programas['ffmpeg'])}) · yt-dlp, para YouTube ({_yes(programas['yt_dlp'])})", "",
        f"Resultado: {HEADLINES[data['veredicto']]}", "Motivos:",
        *(f"  - {m}" for m in data["motivos"]),
    ]
    rec = data["recomendacion"]
    if rec:
        lines.append(f"Recomendación: transcribir con el modelo «{rec['modelo']}».")
        if programas["whisperx"]["valor"] is False:
            lines.append("Falta instalar WhisperX para transcribir en este equipo; lo instala el "
                         "instalador de Academic Manager.")
    lines += ["", "Funciona siempre, aunque el equipo no sea apto:",
              *(f"  - {item}" for item in data["siempre_funciona"])]
    bench = data.get("benchmark")
    if bench is not None:
        lines.append("")
        if bench["disponible"]:
            lines.append(f"Medición de velocidad (modelo {bench['modelo']}): una clase de 90 minutos "
                         f"tardaría unos {bench['estimacion_clase_90_min_min']} minutos en transcribirse. "
                         "Mide velocidad, no precisión.")
        else:
            lines.append(f"Medición de velocidad: no disponible — {bench['motivo']}")
    return "\n".join(lines)
