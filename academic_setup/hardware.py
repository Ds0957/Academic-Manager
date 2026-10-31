"""Sondas de hardware y programas, de solo lectura y sin dependencias extra.

Cada sonda devuelve `{"valor": ...}` o, si falla, `{"valor": "desconocido", "motivo": ...}`:
un fallo nunca interrumpe la comprobación. Los subprocesos tienen tiempo máximo.
"""
import ctypes
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

UNKNOWN = "desconocido"
PROBE_TIMEOUT_SECONDS = 15
GIB = 1024 ** 3
NVIDIA_QUERY = ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]


def _known(value, **extra):
    return {"valor": value, **extra}


def _unknown(reason, **extra):
    return {"valor": UNKNOWN, "motivo": reason, **extra}


def _gb(value):
    return round(value / GIB, 1)


def _module_present(name):
    """True si el módulo se puede importar en este Python; no lo importa."""
    return importlib.util.find_spec(name) is not None


def _ram_bytes():
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx no respondió")
        return status.ullTotalPhys
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="ascii", errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


def system_info():
    try:
        return _known(f"{platform.system()} {platform.release()} ({platform.machine()})".strip())
    except Exception as exc:  # noqa: BLE001 — una sonda nunca interrumpe la comprobación
        return _unknown(f"No se pudo leer el sistema operativo ({type(exc).__name__}).")


def cpu_cores():
    try:
        count = os.cpu_count()
    except Exception as exc:  # noqa: BLE001
        return _unknown(f"No se pudo contar los núcleos ({type(exc).__name__}).")
    return _known(count) if count else _unknown("El sistema no informa el número de núcleos.")


def ram_total():
    try:
        return _known(_gb(_ram_bytes()))
    except Exception as exc:  # noqa: BLE001
        return _unknown(f"No se pudo leer la memoria RAM ({type(exc).__name__}).")


def _existing(path):
    """La carpeta de datos puede no existir aún: se mide la unidad de su ancestro existente."""
    path = Path(path).absolute()
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return Path(path.anchor or os.sep)


def disk_free(runtime):
    target = _existing(runtime)
    drive = Path(target).anchor or str(target)
    try:
        return _known(_gb(shutil.disk_usage(target).free), unidad=drive)
    except Exception as exc:  # noqa: BLE001
        return _unknown(f"No se pudo leer el espacio libre ({type(exc).__name__}).", unidad=drive)


def _parse_nvidia(stdout):
    gpus = []
    for line in stdout.strip().splitlines():
        name, _, memory = line.rpartition(",")
        if not name.strip():
            raise ValueError("línea sin nombre")
        gpus.append({"nombre": name.strip(), "vram_gb": round(float(memory) / 1024, 1)})
    return gpus


def nvidia_gpus():
    """GPUs NVIDIA según `nvidia-smi`; si no está instalado, no hay GPU NVIDIA utilizable."""
    try:
        result = subprocess.run(NVIDIA_QUERY, capture_output=True, text=True, errors="replace",
                                timeout=PROBE_TIMEOUT_SECONDS)
    except FileNotFoundError:
        return _known([], fuente="nvidia-smi")
    except subprocess.TimeoutExpired:
        return _unknown(f"nvidia-smi no respondió en {PROBE_TIMEOUT_SECONDS} s.", fuente="nvidia-smi")
    except Exception as exc:  # noqa: BLE001
        return _unknown(f"No se pudo ejecutar nvidia-smi ({type(exc).__name__}).", fuente="nvidia-smi")
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout or "").split())[:160]
        return _unknown(f"nvidia-smi falló (código {result.returncode})" + (f": {detail}" if detail else "."),
                        fuente="nvidia-smi")
    try:
        return _known(_parse_nvidia(result.stdout), fuente="nvidia-smi")
    except ValueError:
        return _unknown("nvidia-smi devolvió un formato desconocido.", fuente="nvidia-smi")


def torch_cuda():
    """CUDA según PyTorch solo si ya está instalado; nunca instala nada."""
    if not _module_present("torch"):
        return _known("no_instalado")
    try:
        import torch
        if not torch.cuda.is_available():
            return _known(False, gpus=[])
        gpus = []
        for index in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(index)
            gpus.append({"nombre": str(props.name), "vram_gb": _gb(props.total_memory)})
        return _known(True, gpus=gpus)
    except Exception as exc:  # noqa: BLE001 — PyTorch roto o sin driver no interrumpe la comprobación
        return _unknown(f"PyTorch no pudo consultar CUDA ({type(exc).__name__}).")


def _ffmpeg_found(*, windows=None, executable=None):
    """FFmpeg en el PATH o, en Windows, junto al python.exe (donde lo deja el instalador de WhisperX
    y donde Windows lo busca primero al lanzar «ffmpeg» sin ruta)."""
    if shutil.which("ffmpeg") is not None:
        return True
    windows = os.name == "nt" if windows is None else windows
    return windows and (Path(executable or sys.executable).parent / "ffmpeg.exe").is_file()


def programs():
    def module(name):
        try:
            return _known(_module_present(name))
        except Exception as exc:  # noqa: BLE001
            return _unknown(f"No se pudo comprobar ({type(exc).__name__}).")
    try:
        ffmpeg = _known(_ffmpeg_found())
    except Exception as exc:  # noqa: BLE001
        ffmpeg = _unknown(f"No se pudo comprobar ({type(exc).__name__}).")
    return {"whisperx": module("whisperx"), "ffmpeg": ffmpeg, "yt_dlp": module("yt_dlp")}


def collect(runtime):
    return {"sistema": system_info(), "python": _known(platform.python_version()),
            "nucleos_cpu": cpu_cores(), "ram_total_gb": ram_total(), "disco_libre_gb": disk_free(runtime),
            "gpu_nvidia": nvidia_gpus(), "torch_cuda": torch_cuda(), "programas": programs()}
