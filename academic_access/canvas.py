"""Datos de Canvas para el panel, leídos de las copias locales de la sincronización (solo lectura).

Fuente: `procesamiento/canvas-sync/v01/<curso|user>/latest.json` (docs/17) y el registro de cursos
`control/canvas-sync/course-registry.json` para saber a qué materia corresponde cada curso. No escribe
nada, no consulta Canvas y no toca la base: si aún no hay copias, devuelve datos vacíos.

- Nota actual por materia (`grades.current_score` de la inscripción, en porcentaje).
- Mensajes sin leer por materia (solo conteos; la sincronización nunca guarda asuntos ni textos).
- Comentarios del profesor y rúbrica de cada entrega (sin nombres de autores).
- Notas nuevas: actividades calificadas en los últimos `NEW_DAYS` días.
"""
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from canvas_sync.common import SyncError
from canvas_sync.reconcile import verify_snapshot
from canvas_sync.sync import sha

LOCAL_ZONE = ZoneInfo("America/Bogota")
NEW_DAYS = 7


def _load(path):
    if path.is_symlink():
        raise SyncError("Ruta local de Canvas redirigida.")
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _local_day(stamp):
    """Fecha local (Colombia) de una marca ISO de Canvas; None si no se puede leer."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.date()
    return moment.astimezone(LOCAL_ZONE).date()


def _courses(root):
    registry = _load(root / "control" / "canvas-sync" / "course-registry.json")
    if not isinstance(registry, dict):
        return {}
    term = registry.get("active_semester")
    return {row["course_id"]: row["subject_id"] for row in registry.get("courses") or []
            if isinstance(row, dict) and row.get("semester") == term
            and type(row.get("course_id")) is int and isinstance(row.get("subject_id"), str)}


def _user(root):
    raw = _load(root / "procesamiento" / "canvas-sync" / "v01" / "user" / "latest.json")
    if raw is None:
        return None, None
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict) or sha(data) != raw.get("sha256"):
        raise SyncError("Copia de Canvas del usuario no válida.")
    return data.get("sections") or {}, raw.get("captured_at_utc")


def _section(sections, name):
    part = sections.get(name) if isinstance(sections, dict) else None
    return part.get("data") if isinstance(part, dict) and part.get("status") == "ok" else None


def _rubric(assignment, assessed):
    criteria = {c.get("id"): c for c in assignment.get("rubric") or [] if isinstance(c, dict)}
    rows = []
    for criterion_id, value in assessed.items():
        criterion = criteria.get(criterion_id) or {}
        level = next((r.get("description") for r in criterion.get("ratings") or []
                      if isinstance(r, dict) and r.get("id") == value.get("rating_id")), None)
        rows.append({"criterio": criterion.get("description") or "Criterio", "puntos": value.get("points"),
                     "de": criterion.get("points"), "nivel": level, "comentario": value.get("comments")})
    return rows


def _course(root, course_id, subject, today):
    raw = _load(root / "procesamiento" / "canvas-sync" / "v01" / str(course_id) / "latest.json")
    if raw is None:
        return [], []
    by_id = {a["id"]: a for a in verify_snapshot(raw, course_id)}
    feedback = []
    for row in _section(raw["data"].get("sections"), "submissions") or []:
        assignment = by_id.get(row.get("assignment_id")) if isinstance(row, dict) else None
        if assignment is None:
            continue
        comments = [{"texto": c["text"], "fecha": _local_day(c.get("created_at"))}
                    for c in row.get("comments") or [] if isinstance(c, dict) and not c.get("from_me")
                    and isinstance(c.get("text"), str) and c["text"].strip()]
        rubric = _rubric(assignment, row.get("rubric") or {})
        if not comments and not rubric:
            continue
        submission = assignment.get("submission") or {}
        dates = [c["fecha"] for c in comments if c["fecha"]] + [_local_day(submission.get("graded_at"))]
        feedback.append({"materia_id": subject, "titulo": assignment.get("name") or "Actividad",
                         "nota": submission.get("score"), "de": assignment.get("points_possible"),
                         "comentarios": comments, "rubrica": rubric,
                         "fecha": max((d for d in dates if d), default=None)})
    fresh = []
    for assignment in by_id.values():
        submission = assignment.get("submission") or {}
        graded = _local_day(submission.get("graded_at"))
        if graded and submission.get("score") is not None and 0 <= (today - graded).days < NEW_DAYS:
            fresh.append({"materia_id": subject, "titulo": assignment.get("name") or "Actividad",
                          "nota": submission["score"], "de": assignment.get("points_possible"), "fecha": graded})
    return feedback, fresh


def canvas_extras(runtime, *, on=None):
    """Datos de Canvas por materia del semestre activo; vacío si todavía no hay sincronización."""
    root = Path(runtime)
    today = date.fromisoformat(on) if on else datetime.now(LOCAL_ZONE).date()
    courses = _courses(root)
    sections, captured = _user(root)
    subjects = {}
    grades = {e.get("course_id"): e.get("grades") or {} for e in _section(sections, "enrollments") or []
              if isinstance(e, dict)}
    unread = _section(sections, "unread_messages") or {}
    counts = unread.get("by_course") if isinstance(unread, dict) else None
    fresh_all = []
    for course_id, subject in sorted(courses.items()):
        score = grades.get(course_id, {}).get("current_score")
        feedback, fresh = _course(root, course_id, subject, today)
        fresh_all += fresh
        subjects[subject] = {
            "nota": score if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
            "sin_leer": counts.get(str(course_id)) if isinstance(counts, dict) else None,
            "retroalimentacion": sorted(feedback, key=lambda r: r["fecha"] or date.min, reverse=True)}
    fresh_all.sort(key=lambda r: (r["fecha"], r["titulo"]), reverse=True)
    return {"fecha": today, "actualizado": _local_day(captured), "materias": subjects, "notas_nuevas": fresh_all}
