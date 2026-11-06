"""Escrituras desde el chat: seguimiento de entregas, notas, tareas, clases sin Capture, mapas e índices.

Mismo contrato de dos pasos que `preview`/`complete`: sin aprobación solo se calcula el plan
y su `plan_id` (sha256 del JSON canónico de la operación); con `--plan-id` idéntico y
`--approve` se publica la base mediante `canvas_sync.workbook_blocks.publish`.
Los ids se fijan en el plan, así que repetir una operación ya aplicada no duplica filas.
"""
import contextlib
import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from academic_store import records, store
from canvas_sync.common import StoreBusy, SyncError
from capture.academic_manager import database as db
from capture.models import CaptureError
from capture.storage.job import job_lock
from .actions import OperationError, _phase_error

ACTIONS = ("track", "note", "task", "task-status", "class", "batch", "map", "class-index")
FIELDS = {
    "track": ("actividad_id", "estado", "nota", "puntos", "comentario"),
    "note": ("materia_id", "tipo", "texto"),
    "task": ("titulo", "materia_id", "fecha_limite", "detalle"),
    "task-status": ("tarea_id", "estado"),
    "class": ("materia_id", "fecha", "titulo", "resumen"),
    "batch": ("operaciones",),
    "map": ("materia_id", "mapa"),
    "class-index": ("clase_id", "indice"),
}
# `class` queda fuera de los lotes: cada clase reserva su fila libre de 03 - Clases.
BATCH_ACTIONS = ("track", "note", "task", "task-status")
MAX_BATCH = 20
# Una escritura del chat a la vez en este equipo; las demás esperan su turno (#9).
TURN_WAIT_SECONDS = 120.0
TURN_POLL_SECONDS = 1.0
# Una publicación normal dura segundos; un lock más viejo quedó para recuperación supervisada.
STALE_LOCK_SECONDS = 600
TRACK_STATES = records.TRACK_STATES
TASK_STATES = records.TASK_STATES
# `resumen_clase` solo lo crea la acción `class`; el chat no puede escribirlo como nota suelta.
NOTE_TYPES = tuple(t for t in records.NOTE_TYPES if t != "resumen_clase")
CLASS_ROWS = range(2, 501)  # filas de 03 - Clases admitidas por el contrato V2
_PATH = re.compile(r"(?<![\w/])(?:[A-Za-z]:[\\/]|/)[^\s'\"«»]+")


def _public_message(exc):
    """Texto de error apto para el chat remoto: sin rutas locales y acotado."""
    return _PATH.sub("<ruta>", str(exc))[:300]
MAX_TEXT = 4000
MAX_TITLE = 200
ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


class PlanConflict(SyncError):
    """La base publicada ya no admite el plan aprobado."""


def _fail(code, message):
    raise OperationError(code, message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _text(request, key, *, required=False, limit=MAX_TEXT):
    value = request.get(key)
    if isinstance(value, str):
        value = value.strip()
    if value in (None, ""):
        if required:
            _fail("INVALID_ARGUMENT", f"Indique {key}.")
        return None
    if not isinstance(value, str):
        _fail("INVALID_ARGUMENT", f"{key} debe ser texto.")
    if len(value) > limit:
        _fail("INVALID_ARGUMENT", f"{key} supera {limit} caracteres.")
    if value.startswith("=") or "\x00" in value:
        _fail("INVALID_ARGUMENT", f"{key}: no se admiten fórmulas ni caracteres nulos.")
    return value


def _id(request, key, *, required=True):
    value = _text(request, key, required=required, limit=80)
    if value is not None and not ID.fullmatch(value):
        _fail("INVALID_ARGUMENT", f"{key} no tiene un formato de identificador válido.")
    return value


def _choice(request, key, options):
    value = request.get(key)
    if value not in options:
        _fail("INVALID_ARGUMENT", f"{key} debe ser uno de: {', '.join(options)}.")
    return value


def _date(request, key, *, required):
    value = _text(request, key, required=required, limit=10)
    if value is None:
        return None
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(value)
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        _fail("INVALID_ARGUMENT", f"{key} debe ser una fecha válida YYYY-MM-DD.")
    return value


def _number(request, key):
    value = request.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or value < 0:
        _fail("INVALID_ARGUMENT", f"{key} debe ser un número finito no negativo.")
    return float(value)


def _sheet(path, name):
    return store.read_catalog(path, (name,))[name]


def _subject(path, request, *, required=True):
    subject = _id(request, "materia_id", required=required)
    if subject is not None and subject not in {str(r["materia_id"]) for r in _sheet(path, "01 - Materias")}:
        _fail("NOT_FOUND", "materia_id no existe en la base.")
    return subject


def _note(materia_id, tipo, texto, clase_id=None):
    nota_id = "NOTA-" + _digest({"materia_id": materia_id, "tipo": tipo, "texto": texto,
                                 "clase_id": clase_id})[:16]
    return {"nota_id": nota_id, "materia_id": materia_id, "tipo": tipo, "texto": texto,
            "clase_id": clase_id}


def _item_key(operation):
    """Registro que toca un elemento del lote; dos elementos no pueden tocar el mismo."""
    action = operation["accion"]
    if action == "note":
        return ("notas", operation["notas"][0]["nota_id"])
    if action == "track":
        return ("seguimiento", operation["seguimiento"]["actividad_id"])
    return ("tareas", operation["tarea"]["tarea_id"])


def _batch(path, request):
    items = request.get("operaciones")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH:
        _fail("INVALID_ARGUMENT", f"operaciones debe ser una lista de 1 a {MAX_BATCH} elementos.")
    operations, seen = [], set()
    for number, item in enumerate(items, start=1):
        if not isinstance(item, dict) or item.get("accion") not in BATCH_ACTIONS:
            _fail("INVALID_ARGUMENT", f"Operación {number}: accion debe ser una de: {', '.join(BATCH_ACTIONS)}.")
        try:
            operation = _operation(path, item["accion"], {k: v for k, v in item.items() if k != "accion"})
        except OperationError as exc:
            raise OperationError(exc.code, f"Operación {number}: {exc}", stage=exc.stage,
                                 retryable=exc.retryable) from exc
        key = _item_key(operation)
        if key in seen:
            _fail("INVALID_ARGUMENT", f"Operación {number}: repite un registro de otra operación del lote.")
        seen.add(key)
        operations.append(operation)
    return {"accion": "batch", "operaciones": operations}


def _operation(path, action, request):
    """Valida la petición contra la base y devuelve la operación canónica con ids fijados."""
    if action not in ACTIONS:
        _fail("INVALID_ARGUMENT", "Acción de chat no reconocida.")
    extra = sorted(k for k, v in request.items() if v is not None and k not in FIELDS[action])
    if extra:
        _fail("INVALID_ARGUMENT", f"{action} no admite: {', '.join(extra)}.")
    if action == "batch":
        return _batch(path, request)
    if action == "map":
        from . import subject_map
        subject = _subject(path, request)
        content = subject_map.normalize(request.get("mapa"))
        return {"accion": action, "mapa": {"materia_id": subject, "contenido": subject_map.dump(content)}}
    if action == "class-index":
        from . import class_index
        class_id = _id(request, "clase_id")
        row = next((r for r in _sheet(path, "03 - Clases") if str(r.get("clase_id")) == class_id), None)
        if row is None:
            _fail("NOT_FOUND", "clase_id no existe en la base.")
        content = class_index.normalize(request.get("indice"))
        return {"accion": action, "indice": {"clase_id": class_id, "materia_id": str(row["materia_id"]),
                                             "contenido": class_index.dump(content)}}
    if action == "track":
        estado = _choice(request, "estado", TRACK_STATES)
        nota, puntos = _number(request, "nota"), _number(request, "puntos")
        comentario = _text(request, "comentario")
        activity = _id(request, "actividad_id")
        if activity not in {str(r["actividad_id"]) for r in _sheet(path, "02 - Actividades")}:
            _fail("NOT_FOUND", "actividad_id no existe en la base.")
        # Los campos opcionales omitidos conservan el valor ya registrado.
        current = records.find(path, "seguimiento", activity) or {}
        tracked = {"actividad_id": activity, "estado": estado,
                   "nota": nota if nota is not None else current.get("nota"),
                   "puntos": puntos if puntos is not None else current.get("puntos"),
                   "comentario": comentario if comentario is not None else current.get("comentario")}
        return {"accion": action, "seguimiento": tracked}
    if action == "note":
        tipo = _choice(request, "tipo", NOTE_TYPES)
        texto = _text(request, "texto", required=True)
        return {"accion": action, "notas": [_note(_subject(path, request), tipo, texto)]}
    if action == "task":
        titulo = _text(request, "titulo", required=True, limit=MAX_TITLE)
        fields = {"titulo": titulo, "materia_id": _subject(path, request, required=False),
                  "fecha_limite": _date(request, "fecha_limite", required=False),
                  "detalle": _text(request, "detalle")}
        return {"accion": action,
                "tarea": {"tarea_id": "TAR-" + _digest(fields)[:16], **fields, "estado": "pendiente"}}
    if action == "task-status":
        estado = _choice(request, "estado", TASK_STATES)
        task = _id(request, "tarea_id")
        if records.find(path, "tareas", task) is None:
            _fail("NOT_FOUND", "tarea_id no existe en la base.")
        return {"accion": action, "tarea": {"tarea_id": task, "estado": estado}}
    fecha = _date(request, "fecha", required=True)
    titulo = _text(request, "titulo", required=True, limit=MAX_TITLE)
    resumen = _text(request, "resumen")
    subject = _subject(path, request)
    if tuple(store.headers(path, "03 - Clases")) != db.CLASS_HEADERS:
        _fail("INVALID_SOURCE", "03 - Clases no coincide con el contrato V2.")
    classes = _sheet(path, "03 - Clases")
    same = [r for r in classes if r.get("materia_id") == subject and r.get("fuente") == "chat"
            and r.get("titulo") == titulo and db._date_text(r.get("fecha")) == fecha]
    try:
        class_id = (str(same[0]["clase_id"]) if same else
                    db.next_class_id([r["clase_id"] for r in classes], subject, fecha))
    except CaptureError as exc:
        raise OperationError("INVALID_ARGUMENT", _public_message(exc)) from exc
    day = datetime.strptime(fecha, "%Y-%m-%d")
    row = {"clase_id": class_id, "materia_id": subject, "fecha": float((day - db.EXCEL_EPOCH).days),
           "titulo": titulo, "estado_sesion": "realizada", "procesada": False, "fuente": "chat"}
    notes = [_note(subject, "resumen_clase", resumen, class_id)] if resumen else []
    return {"accion": action, "clase": row, "notas": notes}


def _same(current, wanted):
    return all(current.get(k) == v for k, v in wanted.items())


def _insert(path, table, wanted):
    current = records.find(path, table, wanted[records.KEYS[table]])
    if current is None:
        return [{"tabla": table, "accion": "insertar", "registro": wanted}]
    # El estado de una tarea cambia después con task-status: un reintento de `task` sobre una
    # tarea que avanzó sigue siendo la misma alta, no un conflicto.
    if not _same(current, {k: v for k, v in wanted.items() if k != "estado"}):
        raise PlanConflict(f"El identificador {wanted[records.KEYS[table]]} ya existe con otro contenido.")
    return []


def _pending(path, operation):
    """Cambios que faltan para que la base refleje la operación; [] si ya está aplicada."""
    action = operation["accion"]
    if action == "batch":
        return [change for item in operation["operaciones"] for change in _pending(path, item)]
    if action == "track":
        wanted = operation["seguimiento"]
        current = records.find(path, "seguimiento", wanted["actividad_id"])
        if current is not None and _same(current, wanted):
            return []
        return [{"tabla": "seguimiento", "accion": "guardar", "registro": wanted}]
    if action == "map":
        wanted = operation["mapa"]
        current = records.find(path, "mapas", wanted["materia_id"])
        if current is not None and current["contenido"] == wanted["contenido"]:
            return []
        return [{"tabla": "mapas", "accion": "guardar", "registro": wanted}]
    if action == "class-index":
        wanted = operation["indice"]
        current = records.find(path, "indices_clase", wanted["clase_id"])
        if current is not None and current["contenido"] == wanted["contenido"]:
            return []
        return [{"tabla": "indices_clase", "accion": "guardar", "registro": wanted}]
    if action == "task":
        return _insert(path, "tareas", operation["tarea"])
    if action == "task-status":
        wanted = operation["tarea"]
        current = records.find(path, "tareas", wanted["tarea_id"])
        if current is None:
            raise PlanConflict("La tarea ya no existe en la base publicada.")
        return [] if current["estado"] == wanted["estado"] else [
            {"tabla": "tareas", "accion": "estado", "registro": wanted}]
    changes = []
    if action == "class":
        row = operation["clase"]
        found = [r for r in _sheet(path, "03 - Clases") if r.get("clase_id") == row["clase_id"]]
        if found and not _same(found[0], row):
            raise PlanConflict(f"La clase {row['clase_id']} ya existe con otro contenido.")
        if not found:
            target = next((r for r in CLASS_ROWS if r not in store.rows_in_use(path, "03 - Clases")),
                          None)
            if target is None:
                raise PlanConflict("03 - Clases no tiene filas libres en el rango V2.")
            columns = store.column_map(path, "03 - Clases")
            changes.append({"tabla": "clases", "accion": "celdas", "celdas": [
                ["03 - Clases", target, columns[h], None, v] for h, v in row.items()]})
    for note in operation["notas"]:
        changes.extend(_insert(path, "notas", note))
    return changes


class _Busy(Exception):
    """Otro escritor tiene el turno o la base; nada se escribió y se puede esperar."""


def _turn_dir(runtime):
    return runtime / "procesamiento" / "chat-writes"


def _with_turn(runtime, step):
    """Ejecuta `step` con el turno de escritura del chat; espera, acotado, a otros escritores.

    Solo se espera ante ocupaciones detectadas antes de escribir nada (turno local, estado local
    en edición o lock compartido); cada intento vuelve a validar el plan desde cero.
    """
    deadline = time.monotonic() + TURN_WAIT_SECONDS
    while True:
        try:
            with contextlib.ExitStack() as held:
                try:
                    held.enter_context(job_lock(_turn_dir(runtime)))
                except CaptureError:
                    raise _Busy() from None
                return step()
        except _Busy:
            if time.monotonic() >= deadline:
                error = OperationError(
                    "STORE_BUSY", "Otra escritura está publicando la base; no se escribió nada.",
                    retryable=True)
                error.suggested_action = ("Repetir la misma orden con el mismo plan_id en unos "
                                          "minutos; no hace falta otra vista previa.")
                raise error from None
            time.sleep(TURN_POLL_SECONDS)


def _wait_or_stop(exc):
    """Ocupación pasajera → esperar el turno; lock sin datos o demasiado viejo → parar ya."""
    lock = exc.lock if isinstance(exc.lock, dict) else {}
    try:
        started = datetime.fromisoformat(str(lock.get("started_at")))
        age = (datetime.now(timezone.utc) - started).total_seconds()
    except (TypeError, ValueError):
        age = None
    if age is not None and age <= STALE_LOCK_SECONDS:
        return _Busy()
    error = OperationError("STORE_UNAVAILABLE", _public_message(exc))
    error.suggested_action = ("La base sigue bloqueada sin una publicación en curso: requiere "
                              "recuperación supervisada con scripts\\academic-store.cmd unlock "
                              "(simulación) y, con aprobación, --apply; no modificar locks a mano.")
    return error


def _locally_locked(runtime):
    """¿Quedó el estado local en edición? Antes de publicar estaba libre (lo exige la vista previa)."""
    try:
        state = json.loads((runtime / "control" / "database-work-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True  # no se puede comprobar: no afirmar que no se escribió nada
    return state.get("status") != "free" or state.get("lock") is not None


def _local_store(runtime):
    from canvas_sync.routine import workbook_version
    try:
        workbook_version(runtime)
    except StoreBusy as exc:
        raise _wait_or_stop(exc) from None
    except SyncError as exc:
        raise OperationError("STORE_UNAVAILABLE", _public_message(exc), retryable=True) from exc
    return runtime / store.DB_NAME


def preview(runtime, action, request):
    runtime = Path(runtime).resolve()
    return _with_turn(runtime, lambda: _preview(runtime, action, request))


def _preview(runtime, action, request):
    path = _local_store(runtime)
    try:
        operation = _operation(path, action, request)
        pending = _pending(path, operation)
        details = (_map_details(runtime, path, operation) if action == "map" else
                   _index_details(runtime, path, operation) if action == "class-index" else None)
    except PlanConflict as exc:
        raise OperationError("CONFLICT", _public_message(exc)) from exc
    except store.StoreError as exc:
        raise OperationError("STORE_UNAVAILABLE", _public_message(exc)) from exc
    result = {"operation": operation, "plan_id": _digest(operation), "already_applied": not pending,
              "requires_explicit_approval": True}
    if details is not None:
        result["detalle"] = details
    return result


def _moments(content):
    return sum(len(topic["momentos"]) for topic in content["temas"])


def _index_details(runtime, path, operation):
    """Comprueba los minutos del índice y resume lo que guardará la aprobación."""
    from . import class_index
    wanted = operation["indice"]
    content = json.loads(wanted["contenido"])
    subject, class_id = wanted["materia_id"], wanted["clase_id"]
    class_index.check_moments(class_index.transcript_of(runtime, subject, class_id), content)
    current = records.find(path, "indices_clase", class_id)
    return {"clase_id": class_id, "materia_id": subject, "temas": len(content["temas"]),
            "momentos": _moments(content), "reemplaza": current is not None,
            "momentos_anteriores": _moments(json.loads(current["contenido"])) if current else 0}


def _map_details(runtime, path, operation):
    """Comprueba las citas del mapa y resume lo que guardará la aprobación."""
    from . import subject_map
    wanted = operation["mapa"]
    content = json.loads(wanted["contenido"])
    subject_map.check_citations(runtime, wanted["materia_id"], content)
    current = records.find(path, "mapas", wanted["materia_id"])
    previous = len(json.loads(current["contenido"])["temas"]) if current else 0
    return {"materia_id": wanted["materia_id"], "temas": len(content["temas"]),
            "relaciones": len(content["relaciones"]), "diagramas": len(content["diagramas"]),
            "reemplaza": current is not None, "temas_anteriores": previous}


def _rebuild_index(runtime):
    from academic_knowledge.index import build
    build(runtime)


def apply(runtime, action, request, *, plan_id=None, approved=False, api=None, index_builder=None):
    """Publica la operación aprobada; un reintento con el mismo plan ya aplicado no escribe."""
    runtime = Path(runtime).resolve()
    if not approved:
        _fail("APPROVAL_REQUIRED", "La escritura requiere aprobación explícita.")
    if not isinstance(plan_id, str) or not re.fullmatch(r"[0-9a-f]{64}", plan_id):
        _fail("INVALID_ARGUMENT", "Indique el plan_id exacto de la vista previa.")
    return _with_turn(runtime, lambda: _apply(runtime, action, request, plan_id, api, index_builder))


def _apply(runtime, action, request, plan_id, api, index_builder):
    from canvas_sync.semester import load_registry
    from canvas_sync.workbook_blocks import publish
    current = _preview(runtime, action, request)
    if current["plan_id"] != plan_id:
        raise OperationError("PLAN_CHANGED", "La petición o la base cambió. Consulte una vista previa nueva.",
                             stage="validate_plan")
    operation = current["operation"]

    def planner(path, registry):
        try:
            again = _operation(path, action, request)
        except OperationError as exc:
            raise PlanConflict(str(exc)) from exc
        if again != operation:
            raise PlanConflict("La base publicada cambió desde la vista previa.")
        return _pending(path, operation)

    locked = []  # el escritor solo corre con el lock compartido tomado

    def writer(path, changes):
        locked.append(True)
        try:
            records.apply(path, changes)
        except store.StoreError as exc:
            raise SyncError(str(exc)) from exc

    try:
        try:
            outcome = publish(runtime, load_registry(runtime), dry_run=False, api=api,
                              planner=planner, writer=writer)
        except PlanConflict as exc:
            raise OperationError("PLAN_CHANGED", f"{exc} Consulte una vista previa nueva.",
                                 stage="publish") from exc
        except StoreBusy as exc:
            raise _wait_or_stop(exc) from None
        except (SyncError, CaptureError, store.StoreError) as exc:
            raise OperationError("PUBLISH_FAILED", _public_message(exc), stage="publish") from exc
        except OSError as exc:
            raise _phase_error(exc, "publish") from exc
    except OperationError as exc:
        if locked or _locally_locked(runtime):
            # Tras tomar el lock (publish lo copia al estado local en cuanto lo confirma),
            # el protocolo lo conserva para recuperación supervisada.
            exc.published = "incierto"
            exc.suggested_action = ("Consultar academic-read.cmd summary (sistema) antes de "
                                    "reintentar; no modificar la base, hashes ni locks.")
        raise
    warning = None
    try:
        (index_builder or _rebuild_index)(runtime)
    except Exception as exc:  # la escritura ya está publicada: el índice es derivado
        warning = ("El índice local no pudo reconstruirse; la escritura sí quedó publicada. "
                   f"Causa: {type(exc).__name__}: {_public_message(exc)}")
    return {"plan_id": plan_id, "operation": operation,
            "published": outcome["mode"] == "published",
            "already_applied": outcome["mode"] == "unchanged",
            "sha256": outcome["sha256"], "index_refreshed": warning is None, "warning": warning}
