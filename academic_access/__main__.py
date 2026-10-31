"""Contrato JSON de lectura: futuro Coordinador y Projects de materia."""
import argparse
import json
import sys
from pathlib import Path

from academic_config import paths
from canvas_sync.common import SyncError
from . import anomalies, brief, core, summary, system, today


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        raise core.AccessError("INVALID_ARGUMENT", "Parámetros de consulta no válidos.")


def parser():
    p = JsonParser(description="Acceso académico JSON v1 (lectura solamente)", epilog=paths.DATA_HELP)
    p.add_argument("action", choices=("capabilities", "health", "subjects", "subject",
                                     "activities", "activity", "classes", "search", "source",
                                     "capture-jobs", "notes", "tasks", "tracking", "today",
                                     "summary", "subject-brief", "anomalies", "system"))
    p.add_argument("--materia", help="Identificador exacto MAT-...")
    p.add_argument("--actividad", help="Identificador exacto ACT-...")
    p.add_argument("--fuente", help="ID de fuente del índice")
    p.add_argument("--job", help="ID exacto de job de Capture o latest")
    p.add_argument("--query", help="Búsqueda literal por palabras")
    p.add_argument("--estado", help="Filtro de estado (tasks, tracking)")
    p.add_argument("--tipo", help="Filtro de tipo de nota (notes)")
    p.add_argument("--fecha", help="Fecha de referencia YYYY-MM-DD (today, summary, subject-brief)")
    p.add_argument("--dias", type=int, default=None, help="Horizonte en días, 1..60 (today)")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--length", type=int, default=3000)
    return p


FILTERS = {"estado": ("tasks", "tracking"), "tipo": ("notes",),
           "fecha": ("today", "summary", "subject-brief"), "dias": ("today",)}


def parse(argv):
    """Como antes: una opción que la acción no usa es un argumento no válido, no se ignora."""
    args = parser().parse_args(argv)
    if any(getattr(args, name) is not None and args.action not in actions
           for name, actions in FILTERS.items()):
        raise core.AccessError("INVALID_ARGUMENT", "Parámetros de consulta no válidos.")
    # subject-brief devuelve el resumen completo: no acepta paginación, búsqueda ni otros IDs.
    defaults = {"actividad": None, "fuente": None, "job": None, "query": None,
                "limit": None, "offset": 0, "length": 3000}
    if args.action == "subject-brief" and any(getattr(args, k) != v for k, v in defaults.items()):
        raise core.AccessError("INVALID_ARGUMENT", "Parámetros de consulta no válidos.")
    return args


def dispatch(runtime, a):
    if a.action == "capabilities":
        return core.capabilities()
    if a.action == "health":
        return core.health(runtime)
    if a.action == "capture-jobs":
        return core.capture_jobs(runtime, identity=a.job,
                                 limit=a.limit if a.limit is not None else 15)
    if a.action == "today":
        return today.today(runtime, on=a.fecha,
                           days=a.dias if a.dias is not None else today.DEFAULT_DAYS)
    if a.action == "summary":
        return summary.summary(runtime, on=a.fecha)
    if a.action == "subject-brief":
        if not a.materia:
            raise core.AccessError("INVALID_ARGUMENT", "Indique --materia MAT-...")
        return brief.subject_brief(runtime, a.materia, on=a.fecha)
    if a.action == "system":
        return system.system(runtime)
    if a.action == "anomalies":
        return anomalies.anomalies(runtime, subject=a.materia)
    if a.action == "subjects":
        return core.subjects(runtime)
    if a.action in ("notes", "tasks", "tracking"):
        page = {"limit": a.limit if a.limit is not None else 50, "offset": a.offset}
        if a.action == "notes":
            return core.notes(runtime, subject=a.materia, kind=a.tipo, **page)
        reader = core.tasks if a.action == "tasks" else core.tracking
        return reader(runtime, subject=a.materia, state=a.estado, **page)
    if a.action in ("subject", "activities", "classes", "search") and not a.materia:
        raise core.AccessError("INVALID_ARGUMENT", "Indique --materia MAT-...")
    if a.action == "subject":
        return core.subject_detail(runtime, a.materia)
    if a.action == "activities":
        return core.activities(runtime, subject=a.materia,
                               limit=a.limit if a.limit is not None else 50, offset=a.offset)
    if a.action == "classes":
        return core.classes(runtime, subject=a.materia,
                            limit=a.limit if a.limit is not None else 50, offset=a.offset)
    if a.action == "search":
        if not a.query:
            raise core.AccessError("INVALID_ARGUMENT", "Indique --query para buscar.")
        return core.search(runtime, a.query, subject=a.materia,
                           limit=a.limit if a.limit is not None else 8)
    if a.action == "activity":
        if not a.actividad:
            raise core.AccessError("INVALID_ARGUMENT", "Indique --actividad ACT-...")
        return core.activity(runtime, a.actividad, subject=a.materia)
    if a.action == "source":
        if not a.fuente:
            raise core.AccessError("INVALID_ARGUMENT", "Indique --fuente ID.")
        return core.source(runtime, a.fuente, subject=a.materia, offset=a.offset, length=a.length)
    raise core.AccessError("INVALID_ARGUMENT", "Operación no reconocida.")


def main(argv=None):
    action = "unknown"
    try:
        args = parse(argv)
        action = args.action
        runtime = Path(paths.runtime(env=("ACADEMIC_MANAGER_DATA",),
                                     default=paths.DEFAULT_RUNTIME)).resolve()
        payload = dispatch(runtime, args)
        print(json.dumps({"ok": True, "schema_version": core.SCHEMA_VERSION,
                          "action": action, "data": payload},
                         ensure_ascii=True, allow_nan=False))
        return 0
    except core.AccessError as exc:
        error = {"code": exc.code, "message": str(exc)}
        if exc.action:
            error["suggested_action"] = exc.action
    except paths.ConfigError as exc:
        error = {"code": "INVALID_CONFIG", "message": str(exc),
                 "suggested_action": "Corregir o borrar el archivo de configuración indicado; "
                                     "no crear datos sustitutivos."}
    except (SyncError, OSError, ValueError, TypeError, KeyError) as exc:
        error = {"code": "SOURCE_UNAVAILABLE",
                 "message": "La fuente académica no pudo validarse para esta consulta.",
                 "suggested_action": "Revisar salud e integridad; no crear datos sustitutivos."}
    print(json.dumps({"ok": False, "schema_version": core.SCHEMA_VERSION,
                      "action": action, "error": error}, ensure_ascii=True),
          file=sys.stdout)
    return 2


if __name__ == "__main__":
    sys.exit(main())
