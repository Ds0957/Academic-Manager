"""JSON CLI: python -m academic_operations preview|complete --job JOB_ID.

Escrituras desde el chat: track|note|task|task-status|class|batch|map|class-index (ver docs/33-escritura-desde-chat.md).
"""
import argparse
import json
from pathlib import Path
import sys

from academic_config import paths
from capture.models import CaptureError
from . import chat
from .actions import OperationError, preview, complete

# Opción de línea de comandos → campo de la petición de chat.
CHAT_OPTIONS = {"--actividad": "actividad_id", "--materia": "materia_id", "--tarea": "tarea_id",
                "--estado": "estado", "--tipo": "tipo", "--texto": "texto", "--titulo": "titulo",
                "--fecha": "fecha", "--fecha-limite": "fecha_limite", "--detalle": "detalle",
                "--comentario": "comentario", "--resumen": "resumen", "--nota": "nota",
                "--puntos": "puntos"}
# Clave del archivo --input → campo: el nombre de la opción sin `--` y con `-` → `_`.
INPUT_KEYS = {option[2:].replace("-", "_"): field for option, field in CHAT_OPTIONS.items()}
MAX_INPUT_BYTES = 64 * 1024
# Un mapa de la materia o un índice de clase (con sus citas) es más grande que una nota.
MAX_MAP_INPUT_BYTES = 256 * 1024


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        raise OperationError("INVALID_ARGUMENT", "Parámetros de la operación no válidos.")


RUNTIME_HELP = ("Carpeta de datos (por defecto ACADEMIC_MANAGER_DATA, luego el archivo de configuración "
                "del usuario; en Windows C:\\AcademicManagerData)")


def _runtime(explicit):
    """--runtime > ACADEMIC_MANAGER_DATA > archivo del usuario > C:\\AcademicManagerData solo en Windows."""
    try:
        return paths.runtime(explicit, env=("ACADEMIC_MANAGER_DATA",), default=paths.windows_default())
    except paths.ConfigError as exc:
        raise OperationError("INVALID_CONFIG", str(exc)) from None


def _requested_action(argv):
    """Acción pedida sin validar el resto; decide qué contrato analiza los argumentos."""
    probe = argparse.ArgumentParser(add_help=False)
    probe.add_argument("action", nargs="?")
    for option in ("--job", "--analysis", "--plan-id", "--runtime", "--input", *CHAT_OPTIONS):
        probe.add_argument(option)
    probe.add_argument("--approve", action="store_true")
    try:
        return probe.parse_known_args(argv)[0].action
    except SystemExit:
        return None


def _envelope(action, exc):
    error = {"code": exc.code, "message": str(exc)}
    if exc.stage is not None:
        error["stage"] = exc.stage
    error["retryable"] = exc.retryable
    # "no": nada llegó a escribirse; "incierto": falló con el lock compartido tomado.
    error["publicado"] = getattr(exc, "published", "no")
    error["suggested_action"] = getattr(exc, "suggested_action", None) or (
        "Generar una vista previa nueva y pedir aprobación otra vez." if exc.retryable or
        exc.code == "PLAN_CHANGED" else "Corregir la petición; no modificar la base, hashes ni locks.")
    return {"ok": False, "schema_version": 1, "action": action, "error": error}


def _invalid_input(message):
    return OperationError("INVALID_ARGUMENT", f"--input: {message}")


def _fields(content, where=""):
    """Claves del JSON → campos de la petición, con las mismas reglas que las opciones."""
    unknown = sorted(k for k in content if k not in INPUT_KEYS)
    if unknown:
        raise _invalid_input(where + chat._public_message(f"claves no admitidas: {', '.join(unknown)}")[:200] + ".")
    request = dict.fromkeys(CHAT_OPTIONS.values())
    request.update({INPUT_KEYS[k]: v for k, v in content.items()})
    return request


def _batch_request(content):
    """{"operaciones": [{"accion": "note", ...}, ...]}: cada elemento usa las claves de su acción."""
    if set(content) != {"operaciones"} or not isinstance(content["operaciones"], list):
        raise _invalid_input('batch espera {"operaciones": [...]} y nada más.')
    items = []
    for number, item in enumerate(content["operaciones"], start=1):
        if not isinstance(item, dict) or "accion" not in item:
            raise _invalid_input(f"la operación {number} debe ser un objeto con accion.")
        fields = _fields({k: v for k, v in item.items() if k != "accion"}, f"operación {number}: ")
        items.append({"accion": item["accion"], **{k: v for k, v in fields.items() if v is not None}})
    return {"operaciones": items}


def _map_request(content):
    """{"materia": "MAT-…", "mapa": {...}}: el mapa va completo en el JSON."""
    unknown = sorted(k for k in content if k not in ("materia", "mapa"))
    if unknown:
        raise _invalid_input(chat._public_message(f"claves no admitidas: {', '.join(unknown)}")[:200] + ".")
    return {"materia_id": content.get("materia"), "mapa": content.get("mapa")}


def _index_request(content):
    """{"clase": "CLS-…", "indice": {...}}: el índice va completo en el JSON."""
    unknown = sorted(k for k in content if k not in ("clase", "indice"))
    if unknown:
        raise _invalid_input(chat._public_message(f"claves no admitidas: {', '.join(unknown)}")[:200] + ".")
    return {"clase_id": content.get("clase"), "indice": content.get("indice")}


def _read_input(path, action=None):
    """Petición de chat desde un archivo JSON (evita que cmd.exe altere % ^ & \")."""
    limit = MAX_MAP_INPUT_BYTES if action in ("map", "class-index") else MAX_INPUT_BYTES
    try:
        with open(path, "rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        raise _invalid_input("no se pudo leer el archivo indicado.") from None
    if len(raw) > limit:
        raise _invalid_input(f"el archivo supera {limit // 1024} KB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _invalid_input("el archivo no está en UTF-8.") from None

    def reject_constant(name):
        raise ValueError(name)

    try:
        content = json.loads(text, parse_constant=reject_constant)
    except ValueError:
        raise _invalid_input("el archivo no es JSON válido.") from None
    if not isinstance(content, dict):
        raise _invalid_input("el JSON debe ser un objeto con los campos de la operación.")
    if action == "batch":
        return _batch_request(content)
    if action == "map":
        return _map_request(content)
    if action == "class-index":
        return _index_request(content)
    return _fields(content)


def chat_main(argv):
    action = next((a for a in argv if a in chat.ACTIONS), "unknown")
    try:
        parser = JsonParser(description="Academic Manager: escrituras desde el chat")
        parser.add_argument("action", choices=chat.ACTIONS)
        for option, field in CHAT_OPTIONS.items():
            parser.add_argument(option, dest=field, type=float if field in ("nota", "puntos") else str)
        parser.add_argument("--job")
        parser.add_argument("--analysis")
        parser.add_argument("--plan-id")
        parser.add_argument("--approve", action="store_true")
        parser.add_argument("--runtime", default=None, help=RUNTIME_HELP)
        parser.add_argument("--input")
        args = parser.parse_args(argv)
        action = args.action
        if args.job or args.analysis:
            raise OperationError("INVALID_ARGUMENT", "--job y --analysis solo valen para preview/complete.")
        args.runtime = _runtime(args.runtime)
        if not args.runtime:
            raise OperationError("INVALID_ARGUMENT", "Indique --runtime.")
        request = {f: getattr(args, f) for f in CHAT_OPTIONS.values()}
        if args.input is not None:
            if any(v is not None for v in request.values()):
                raise _invalid_input("no se combina con opciones de contenido; ponga todos los campos en el JSON.")
            request = _read_input(args.input, action)
        elif action == "batch":
            raise OperationError("INVALID_ARGUMENT", 'batch requiere --input con {"operaciones": [...]}.')
        elif action == "map":
            raise OperationError("INVALID_ARGUMENT", 'map requiere --input con {"materia": ..., "mapa": {...}}.')
        elif action == "class-index":
            raise OperationError("INVALID_ARGUMENT",
                                 'class-index requiere --input con {"clase": ..., "indice": {...}}.')
        runtime = Path(args.runtime).resolve()
        if args.plan_id is None and not args.approve:
            result = chat.preview(runtime, action, request)
        else:
            result = chat.apply(runtime, action, request, plan_id=args.plan_id, approved=args.approve)
        payload, code = {"ok": True, "schema_version": 1, "action": action, "data": result}, 0
    except OperationError as exc:
        payload, code = _envelope(action, exc), 2
    except (OSError, ValueError, KeyError, TypeError):
        payload = {"ok": False, "schema_version": 1, "action": action,
                   "error": {"code": "OPERATION_UNAVAILABLE",
                             "message": "No se pudo validar o ejecutar la operación local.",
                             "suggested_action": "Revisar el estado local; no modificar la base, hashes ni locks."}}
        code = 2
    print(json.dumps(payload, ensure_ascii=True, allow_nan=False))
    return code


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if _requested_action(argv) in chat.ACTIONS:
        return chat_main(argv)
    parser = argparse.ArgumentParser(description="Academic Manager: operaciones académicas controladas")
    parser.add_argument("action", choices=("preview", "complete"))
    parser.add_argument("--job", required=True, help="ID exacto de Capture")
    parser.add_argument("--analysis", help="Solo nombre .md dentro de procesamiento/analysis-review")
    parser.add_argument("--plan-id", help="Hash exacto devuelto por preview")
    parser.add_argument("--approve", action="store_true", help="Confirmación del estudiante para escribir/publicar")
    parser.add_argument("--runtime", default=None, help=RUNTIME_HELP)
    args = parser.parse_args(argv)
    try:
        args.runtime = _runtime(args.runtime)
        if not args.runtime:
            raise OperationError("INVALID_ARGUMENT", "Indique --runtime.")
        runtime = Path(args.runtime).resolve()
        if args.action == "preview":
            if args.plan_id or args.approve:
                raise OperationError("INVALID_ARGUMENT", "preview no acepta aprobación ni plan-id.")
            result = preview(runtime, args.job, args.analysis)
        else:
            result = complete(runtime, args.job, filename=args.analysis,
                              plan_id=args.plan_id, approved=args.approve)
        payload = {"ok": True, "schema_version": 1, "action": args.action, "data": result}
        code = 0
    except OperationError as exc:
        error = {"code": exc.code, "message": str(exc)}
        if exc.stage is not None:
            error["stage"] = exc.stage
        if exc.substage is not None:
            error["substage"] = exc.substage
        if exc.os_error is not None:
            error["os_error"] = exc.os_error
        error["retryable"] = exc.retryable
        error["suggested_action"] = (
            "Consultar el estado del job y generar otro preview antes de reintentar."
            if exc.retryable else
            "Revisar el estado del job y la etapa indicada; no modificar hashes ni locks.")
        payload = {"ok": False, "schema_version": 1, "action": args.action,
                   "error": error}
        code = 2
    except CaptureError as exc:
        payload = {"ok": False, "schema_version": 1, "action": args.action,
                   "error": {"code": "CAPTURE_FAILED", "message": str(exc)[:300],
                             "suggested_action": "Volver a consultar preview; no manipular locks ni hashes."}}
        code = 2
    except (OSError, ValueError, KeyError, TypeError):
        payload = {"ok": False, "schema_version": 1, "action": args.action,
                   "error": {"code": "OPERATION_UNAVAILABLE",
                             "message": "No se pudo validar o ejecutar la operación local.",
                             "suggested_action": "Verificar el estado del job y el acceso autorizado."}}
        code = 2
    print(json.dumps(payload, ensure_ascii=True, allow_nan=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
