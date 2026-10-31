"""CLI: python -m academic_setup check [--texto] [--benchmark] [--runtime RUTA]."""
import argparse
import json
import sys
from pathlib import Path

from academic_config import paths

from . import benchmark, check, hardware

SCHEMA_VERSION = 1


class SetupError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        raise SetupError("INVALID_ARGUMENT", "Parámetros no válidos; consulte --help.")


def parser():
    p = JsonParser(description="Comprobación del equipo para Capture (solo lectura)")
    p.add_argument("action", choices=("check",))
    p.add_argument("--runtime", help="Carpeta de datos cuyo disco se mide (por defecto "
                                     "ACADEMIC_MANAGER_DATA, el archivo de configuración o "
                                     "C:\\AcademicManagerData en Windows)")
    p.add_argument("--texto", action="store_true", help="Resumen en español en lugar de JSON")
    p.add_argument("--benchmark", action="store_true",
                   help="Medir la velocidad real de WhisperX con una muestra corta (si está instalado)")
    return p


def default_runtime():
    """Carpeta de datos con el resolvedor compartido; fuera de Windows, sin valor, la carpeta actual."""
    return Path(paths.runtime(env=("ACADEMIC_MANAGER_DATA",), default=paths.windows_default())
                or Path.cwd())


def run(args):
    runtime = Path(args.runtime) if args.runtime else default_runtime()
    info = hardware.collect(runtime)
    programas = info.pop("programas")
    verdict, motivos, recommendation = check.evaluate(info)
    data = {"equipo": info, "programas": programas, "veredicto": verdict, "motivos": motivos,
            "recomendacion": recommendation, "siempre_funciona": list(check.ALWAYS_WORKS),
            "umbrales": check.thresholds(),
            "nota": "Comprobación de solo lectura; no instala ni cambia nada."}
    if args.benchmark:
        data["benchmark"] = benchmark.run(runtime, recommendation)
        data["nota"] = ("No instala ni cambia nada; --benchmark solo guarda el modelo descargado en "
                        "la caché de Capture (procesamiento/model-cache).")
    return data


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        data = run(args)
        if args.texto:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(errors="replace")  # consolas Windows sin UTF-8
            print(check.text(data))
        else:
            print(json.dumps({"ok": True, "schema_version": SCHEMA_VERSION, "action": args.action,
                              "data": data}, ensure_ascii=True, allow_nan=False))
        return 0
    except SetupError as exc:
        error = {"code": exc.code, "message": str(exc)}
    except paths.ConfigError as exc:
        error = {"code": "INVALID_CONFIG", "message": str(exc)}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        error = {"code": "CHECK_FAILED", "message": f"La comprobación no pudo completarse ({type(exc).__name__})."}
    print(json.dumps({"ok": False, "schema_version": SCHEMA_VERSION, "action": "check", "error": error},
                     ensure_ascii=True))
    return 2


if __name__ == "__main__":
    sys.exit(main())
