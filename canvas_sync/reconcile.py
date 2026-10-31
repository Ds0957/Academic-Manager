"""Reconciliación Canvas -> V2, estrictamente de solo lectura.

Genera propuestas revisables; nunca edita Excel, Canvas ni el estado compartido.
"""
import json
from pathlib import Path

from academic_store import store

from .common import SyncError
from .sync import sha

REVIEW_KINDS = {"nota_agregada", "informativo", "informativo_o_evaluable", "complementaria"}


def verify_snapshot(raw, course_id):
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict):
        raise SyncError("Snapshot enriquecido incompleto.")
    data = raw["data"]
    if data.get("schema_version") != 2 or data.get("source") != "canvas_rest_get_only":
        raise SyncError("Versión/procedencia del snapshot no compatible.")
    if sha(data) != raw.get("sha256"):
        raise SyncError("Snapshot Canvas no supera comprobación SHA-256.")
    if (data.get("course") or {}).get("id") != course_id:
        raise SyncError("El curso del snapshot no coincide.")
    rows = data.get("assignments")
    if not isinstance(rows, list) or any(type(r.get("id")) is not int for r in rows if isinstance(r, dict)):
        raise SyncError("Lista de assignments inválida.")
    if any(not isinstance(r, dict) for r in rows) or len({r["id"] for r in rows}) != len(rows):
        raise SyncError("Assignments duplicados o inválidos.")
    return rows


def normalize_id(value):
    if value is None or value == "":
        return None
    s = str(value).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def reconcile(snapshots, activities, mappings=None):
    """Pure function. No invented IDs, no automated imports or updates."""
    index = {}
    for row in activities:
        if not row.get("actividad_id") or not row.get("materia_id"):
            continue
        if str(row.get("lms") or "").casefold() != "canvas":
            continue
        key = (row["materia_id"], normalize_id(row.get("objeto_externo_id")))
        if key[1] is None:
            continue
        if key in index:
            raise SyncError("Identificador externo duplicado en V2.")
        index[key] = row
    # Las aulas vienen siempre del registro de semestres (`semester.active_mappings`):
    # sin ellas no hay respaldo a aulas fijas de un semestre concreto.
    if mappings is None:
        raise SyncError("Indique las aulas del semestre activo desde el registro de cursos.")
    if len(set(mappings.values())) != len(mappings):
        raise SyncError("Varias aulas están asociadas a la misma instancia de materia.")
    proposed = []
    for course_id, raw in sorted(snapshots.items()):
        if course_id not in mappings:
            raise SyncError("Curso no autorizado para reconciliación.")
        subject = mappings[course_id]
        for item in verify_snapshot(raw, course_id):
            key = (subject, str(item["id"]))
            existing = index.get(key)
            submission = item.get("submission") or {}
            differences = {}
            if existing:
                for target, source in (("titulo", "name"), ("puntos_posibles", "points_possible")):
                    remote = item.get(source)
                    local = existing.get(target)
                    if remote is not None and str(local or "").strip() != str(remote).strip():
                        try:
                            equal_number = remote is not None and float(remote) == float(local)
                        except (TypeError, ValueError):
                            equal_number = False
                        if not equal_number:
                            differences[target] = {"local": local, "canvas": remote}
                for target, source in (("puntos_obtenidos", "score"),):
                    remote = submission.get(source)
                    local = existing.get(target)
                    if remote is not None and local is not None and str(local) != str(remote):
                        differences[target] = {"local": local, "canvas": remote}
            kind = item.get("kind_hint")
            if existing:
                decision = "revisar_cambios" if differences else "coincidencia"
            else:
                decision = "revisar_clasificacion" if kind in REVIEW_KINDS else "candidato_nuevo"
            proposed.append({
                "course_id": course_id, "materia_id": subject,
                "assignment_id": item["id"], "actividad_id": existing.get("actividad_id") if existing else None,
                "titulo_canvas": item.get("name"), "tipo_provisional": kind,
                "clasificacion_confirmada": bool(item.get("classification_confirmed")),
                "decision": decision, "diferencias": differences,
                "referencias_externas": {k: item[k] for k in ("quiz_id", "discussion_id") if item.get(k) is not None},
                "submitted_at": submission.get("submitted_at"),
                "puntos_obtenidos_canvas": submission.get("score")
            })
    counts = {state: sum(x["decision"] == state for x in proposed)
              for state in ("coincidencia", "revisar_cambios", "candidato_nuevo", "revisar_clasificacion")}
    return {"schema_version": 1, "mode": "read_only_review", "counts": counts, "items": proposed}


ACTIVITY_REQUIRED = {"actividad_id", "materia_id", "lms", "objeto_externo_id", "titulo",
                     "puntos_posibles", "puntos_obtenidos"}


def read_v2_activities(workbook_path):
    """Actividades V2 desde una base SQLite; solo lectura."""
    try:
        headers = store.headers(workbook_path, "02 - Actividades")
        rows = store.read_catalog(workbook_path, ("02 - Actividades",))["02 - Actividades"]
    except store.StoreError as exc:
        raise SyncError(str(exc)) from exc
    if not ACTIVITY_REQUIRED.issubset(headers):
        raise SyncError("Columnas de 02 - Actividades no coinciden con V2.")
    return rows


def preview(runtime, workbook_path=None, course_ids=None, mappings=None):
    root = Path(runtime).resolve()
    workbook = Path(workbook_path) if workbook_path else root / store.DB_NAME
    if not workbook.is_file():
        raise SyncError("No existe base local V2 para comparación.")
    if mappings is None:
        from .semester import load_registry, active_mappings
        mappings = active_mappings(load_registry(root))
    course_ids = tuple(course_ids) if course_ids is not None else tuple(mappings)
    snapshots = {}
    for course in course_ids:
        if course not in mappings:
            raise SyncError("Curso no reconocido.")
        path = root / "procesamiento" / "canvas-sync" / "v01" / str(course) / "latest.json"
        if not path.is_file():
            raise SyncError(f"Falta snapshot local del curso {course}; ejecute sync-rich.")
        snapshots[course] = json.loads(path.read_text(encoding="utf-8"))
    result = reconcile(snapshots, read_v2_activities(workbook), mappings=mappings)
    result["snapshot_sha256"] = {str(i): snapshots[i]["sha256"] for i in course_ids}
    result["workbook_path"] = str(workbook)
    # No output file: this stage is observational only.
    return result
