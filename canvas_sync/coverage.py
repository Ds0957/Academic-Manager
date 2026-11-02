"""Auditoría de cobertura extra de Canvas (`canvas_sync audit-extra`), solo GET y solo metadatos.

Comprueba con la cuenta del estudiante qué devuelven recursos que aún no usa Academic Manager
(docs/16 auditó los primeros): entregas propias con comentarios y rúbrica, inscripciones con
nota, periodos de calificación, mensajes sin leer, novedades del planificador, objetos
multimedia con subtítulos y búsqueda inteligente.

Guarda solo conteos, estados HTTP y nombres de campos: nunca comentarios, asuntos, cuerpos,
nombres de personas, notas ni títulos de videos. No abre mensajes (abrir uno lo marca como
leído); usa solo el listado de no leídos y su conteo. Nada de esto se publica ni toca la base.
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .api import ORIGIN
from .auth import valid
from .common import SyncError, atomic_json, safe_dir
from .enriched import MAX_ACTIVE_COURSES, BudgetClient

SCHEMA = 1
PLANNER_BACK, PLANNER_AHEAD = 45, 150
LABELS = {"ok": "ok", "forbidden": "sin permiso (HTTP 403)", "not_found": "no existe (HTTP 404)",
          "error": "error"}


def _failure(exc):
    message = str(exc)
    if "HTTP 403" in message:
        return {"status": "forbidden"}
    if "HTTP 404" in message:
        return {"status": "not_found"}
    return {"status": "error", "detalle": message[:200]}


def _probe(fn):
    try:
        return {"status": "ok", **fn()}
    except SyncError as exc:
        return _failure(exc)


def _fields(rows):
    """Campos con algún valor no vacío en las filas (solo los nombres)."""
    keys = set()
    for row in rows:
        if isinstance(row, dict):
            keys.update(k for k, v in row.items() if v not in (None, "", [], {}))
    return sorted(keys)


def _submissions(client, course_id):
    rows = client.pages(f"/api/v1/courses/{course_id}/students/submissions",
                        {"per_page": 100, "include[]": ["submission_comments", "rubric_assessment"]})
    comments = [len(r.get("submission_comments") or []) for r in rows]
    return {"total": len(rows), "con_comentarios": sum(1 for c in comments if c), "comentarios": sum(comments),
            "con_rubrica": sum(1 for r in rows if r.get("rubric_assessment")),
            "missing": sum(1 for r in rows if r.get("missing") is True),
            "late": sum(1 for r in rows if r.get("late") is True),
            "excused": sum(1 for r in rows if r.get("excused") is True),
            "con_graded_at": sum(1 for r in rows if r.get("graded_at")),
            "campos": _fields(rows)}


def _grading_periods(client, course_id):
    data, _ = client.get(ORIGIN + f"/api/v1/courses/{course_id}/grading_periods")
    rows = data.get("grading_periods") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise SyncError("Respuesta de periodos de calificación no válida.")
    # Los títulos son institucionales («Bloque 1»): sirven para ver si coinciden con los bloques.
    return {"total": len(rows), "titulos": [str(r.get("title") or "")[:60] for r in rows],
            "campos": _fields(rows)}


def _media(client, course_id):
    rows = client.pages(f"/api/v1/courses/{course_id}/media_objects", {"per_page": 50})
    tracks = Counter(f"{t.get('kind')}:{t.get('locale')}" for r in rows for t in (r.get("media_tracks") or [])
                     if isinstance(t, dict))
    return {"total": len(rows), "con_pistas": sum(1 for r in rows if r.get("media_tracks")),
            "pistas": sorted(tracks), "campos": _fields(rows)}


def _smartsearch(client, course_id):
    rows = client.pages(f"/api/v1/courses/{course_id}/smartsearch", {"q": "evaluación", "per_page": 5})
    return {"resultados": len(rows), "campos": _fields(rows)}


def _enrollments(client):
    rows = client.pages("/api/v1/users/self/enrollments",
                        {"type[]": ["StudentEnrollment"], "state[]": ["active"], "include[]": ["current_points"],
                         "per_page": 100})
    grades = [r.get("grades") for r in rows if isinstance(r.get("grades"), dict)]
    return {"total": len(rows), "con_nota": sum(1 for g in grades if g.get("current_score") is not None),
            "campos_nota": _fields(grades), "campos": _fields(rows)}


def _conversations(client):
    count, _ = client.get(ORIGIN + "/api/v1/conversations/unread_count")
    try:
        unread = int((count or {}).get("unread_count"))
    except (TypeError, ValueError):
        raise SyncError("Conteo de mensajes no válido.") from None
    rows = client.pages("/api/v1/conversations", {"scope": "unread", "per_page": 50})
    return {"sin_leer": unread, "listado": len(rows), "campos": _fields(rows)}


def _planner(client, today):
    rows = client.pages("/api/v1/planner/items",
                        {"filter": "new_activity", "per_page": 100,
                         "start_date": (today - timedelta(days=PLANNER_BACK)).isoformat(),
                         "end_date": (today + timedelta(days=PLANNER_AHEAD)).isoformat()})
    flags = Counter(k for r in rows if isinstance(r.get("submissions"), dict)
                    for k, v in r["submissions"].items() if v is True)
    return {"total": len(rows), "por_tipo": dict(sorted(Counter(str(r.get("plannable_type")) for r in rows).items())),
            "with_feedback": flags["with_feedback"], "missing": flags["missing"], "graded": flags["graded"],
            "late": flags["late"], "campos": _fields(rows)}


def audit(runtime, *, client_factory=BudgetClient, today=None):
    cred, _ = valid(runtime)
    client = client_factory(cred["token"])
    if client.profile() != cred["account_id"]:
        raise SyncError("La cuenta de Canvas cambió; no se guardan datos.")
    del cred
    day = date.fromisoformat(today) if today else date.today()
    courses = client.courses()
    ids = sorted({c["id"] for c in courses if isinstance(c, dict) and type(c.get("id")) is int})
    if not ids or len(ids) > MAX_ACTIVE_COURSES:
        raise SyncError("Cantidad de cursos activos inesperada.")
    report = {"esquema": SCHEMA, "generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "cursos": {}, "usuario": {}}
    for course_id in ids:
        report["cursos"][str(course_id)] = {
            "submissions": _probe(lambda: _submissions(client, course_id)),
            "grading_periods": _probe(lambda: _grading_periods(client, course_id)),
            "media_objects": _probe(lambda: _media(client, course_id)),
            "smartsearch": _probe(lambda: _smartsearch(client, course_id))}
    report["usuario"] = {"enrollments": _probe(lambda: _enrollments(client)),
                         "conversations": _probe(lambda: _conversations(client)),
                         "planner_new_activity": _probe(lambda: _planner(client, day))}
    report["solicitudes"] = getattr(client, "requests", None)
    folder = safe_dir(Path(runtime), "procesamiento", "canvas-sync", "coverage")
    stamp = report["generado_utc"].replace("-", "").replace(":", "")
    atomic_json(folder / f"extra-{stamp}.json", report)
    atomic_json(folder / "extra-latest.json", report)
    return report


def _line(name, probe, keys):
    status = LABELS.get(probe.get("status"), probe.get("status"))
    extra = ", ".join(f"{k}={probe[k]}" for k in keys if k in probe)
    fields = f"; campos: {', '.join(probe['campos'])}" if probe.get("campos") else ""
    detail = f" ({probe['detalle']})" if probe.get("detalle") else ""
    return f"  {name}: {status}{detail}" + (f" — {extra}" if extra else "") + fields


def summary(report):
    """Resumen en texto para pegar en el chat: solo conteos, estados y nombres de campos."""
    lines = [f"Auditoría extra de Canvas ({report.get('generado_utc')}), solicitudes GET: {report.get('solicitudes')}"]
    user = report["usuario"]
    lines.append("Usuario:")
    lines.append(_line("enrollments", user["enrollments"], ("total", "con_nota", "campos_nota")))
    lines.append(_line("conversations", user["conversations"], ("sin_leer", "listado")))
    lines.append(_line("planner new_activity", user["planner_new_activity"],
                       ("total", "por_tipo", "with_feedback", "missing", "graded", "late")))
    for course_id, probes in report["cursos"].items():
        lines.append(f"Curso {course_id}:")
        lines.append(_line("submissions", probes["submissions"],
                           ("total", "con_comentarios", "comentarios", "con_rubrica", "missing", "late",
                            "excused", "con_graded_at")))
        lines.append(_line("grading_periods", probes["grading_periods"], ("total", "titulos")))
        lines.append(_line("media_objects", probes["media_objects"], ("total", "con_pistas", "pistas")))
        lines.append(_line("smartsearch", probes["smartsearch"], ("resultados",)))
    return "\n".join(lines)
