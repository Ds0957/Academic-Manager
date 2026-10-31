"""`summary`: resumen funcional y corto para empezar un chat (solo lectura).

`avisos` va primero: fallos del sistema (Canvas Sync, token, índice) que el chat debe decir
antes que nada. Se genera de los datos reales de la base publicada y se valida contra `workbook_version`,
no contra el índice. Las fuentes opcionales (índice, log de Canvas Sync, vencimiento del
token, metadatos de Drive) que falten se degradan a `null`/`unknown`, nunca a un error.
"""
import json
from datetime import datetime
from pathlib import Path

from academic_store import records, store
from canvas_sync import auth
from canvas_sync.common import SyncError

from . import core, today as today_module

URGENT_ITEMS = 5
RECENT_NOTES = 5
OPEN_TASKS = 10
NOTE_TEXT = 200
SHORT_SHA = 12


def _semester(catalog):
    """Semestre activo (misma regla única que `today`) y su bloque confirmado, o None."""
    subjects = today_module._active_subjects(catalog)
    row = next(r for r in catalog["05 - Semestres"]
               if str(r.get("estado") or "").strip().casefold() == "activo")
    raw = row.get("bloque_activo")
    block = (int(float(raw)) if not isinstance(raw, bool) and str(raw).strip() in ("1", "1.0", "2", "2.0")
             else None)
    return {"semestre_id": str(row["semestre_id"]), "nombre": today_module._text(row.get("nombre")),
            "bloque_activo": block}, subjects


def _subjects(catalog, subjects, day):
    current = set(subjects)
    _, last = today_module._classes(
        [r for r in catalog["03 - Clases"] if today_module._text(r.get("materia_id")) in current], day)
    names = {str(r["materia_id"]): today_module._text(r.get("nombre"))
             for r in catalog["01 - Materias"] if r.get("materia_id")}
    return [{"materia_id": mid, "nombre": names.get(mid),
             "ultima_clase": last[mid].isoformat() if mid in last else None} for mid in subjects]


def _recent(path, subjects):
    # Como en `today`: solo el semestre activo; las tareas sin materia siempre cuentan.
    current = set(subjects)
    notes = core.note_rows([r for r in records.read(path, "notas") if r["materia_id"] in current])
    tasks = core.task_rows([r for r in records.read(path, "tareas")
                            if r["materia_id"] is None or r["materia_id"] in current],
                           states=today_module.OPEN_TASKS)
    return {"notas": [{"nota_id": r["nota_id"], "materia_id": r["materia_id"], "tipo": r["tipo"],
                       "texto": r["texto"][:NOTE_TEXT],
                       "fecha": today_module._utc_local_date(r["creado_utc"]).isoformat()}
                      for r in notes[:RECENT_NOTES]],
            "tareas": [{key: r[key] for key in ("tarea_id", "titulo", "materia_id", "fecha_limite", "estado")}
                       for r in tasks[:OPEN_TASKS]]}


def _index(runtime, actual):
    try:
        manifest, _ = core.knowledge.load(runtime)
        expected = manifest["data"].get("workbook_sha256")
        return {"fresh": actual == expected, "generated_at_utc": manifest.get("generado_utc")}
    except (SyncError, OSError, KeyError, ValueError, TypeError, AttributeError):
        return {"fresh": "unknown", "generated_at_utc": None}


def _token(runtime, day):
    """Vencimiento desde los metadatos no secretos de Canvas Sync; nunca descifra la credencial."""
    try:
        # Con la fecha de hoy, la hora cuenta: un token vencido esta mañana ya está vencido.
        now = datetime.now().astimezone() if day == today_module.local_today() else None
        block = auth.notice(auth.read_meta(runtime), day, now=now)
    except SyncError as exc:
        path = Path(runtime) / "control" / "canvas-sync" / auth.FILE
        try:
            present = path.is_file()
        except OSError:
            present = False
        reason = str(exc) if present else "No hay autorización local de Canvas en este equipo."
        return {"vence": None, "dias_restantes": None, "aviso": None, "motivo": reason}
    return {**block, "motivo": None}


def _published(runtime, path, actual):
    try:
        state = json.loads((Path(runtime) / "control" / "database-work-state.json")
                           .read_text(encoding="utf-8"))
        modified = (state.get("published") or {}).get("drive_modified_time")
    except (OSError, ValueError, AttributeError):
        modified = None
    try:
        revision = store.info(path)["revision"]
    except (store.StoreError, OSError, KeyError, ValueError):
        revision = None
    return {"sha256": actual[:SHORT_SHA],
            "drive_modified_time": modified if isinstance(modified, str) else None,
            "revision": revision}


def _warnings(history, index, token, day):
    """Lo que el chat debe decir antes que nada: fallos del sistema que no se ven solos."""
    warnings = [core.sync_warning(history, day), token.get("aviso")]
    if index["fresh"] is False:
        warnings.append("El índice local está desactualizado: las búsquedas pueden no reflejar "
                        "la última versión; ejecutar scripts\\knowledge.cmd build.")
    return [w for w in warnings if w]


def summary(runtime, *, on=None):
    day = today_module.parse_date(on) if on is not None else today_module.local_today()
    try:
        actual = core.workbook_version(runtime)
    except core.SyncError as exc:
        raise core.AccessError("SOURCE_UNAVAILABLE", "La base publicada no está disponible o no valida.",
                               action="Revisar la tarea diaria y la integridad de la base local.") from exc
    path = Path(runtime) / store.DB_NAME
    catalog = store.read_catalog(path, ("01 - Materias", "03 - Clases", "05 - Semestres"))
    semester, subjects = _semester(catalog)
    urgent = today_module.today(Path(runtime), on=day.isoformat())
    history = core.sync_history(runtime)
    index, token = _index(runtime, actual), _token(runtime, day)
    return {"avisos": _warnings(history, index, token, day),
            "fecha": day.isoformat(), "fechas_zona": "America/Bogota",
            "semestre": semester,
            "materias": _subjects(catalog, subjects, day),
            "urgente": {"items": urgent["items"][:URGENT_ITEMS], "resumen": urgent["resumen"]},
            "reciente": _recent(path, subjects),
            "sistema": {"ultimo_canvas_sync": core._sync_last_run(runtime),
                        "canvas_sync_historial": history,
                        "indice": index,
                        "token_canvas": token,
                        "publicado": _published(runtime, path, actual)}}
