"""Snapshots locales verificables con diff por ID, sin escritura al workbook."""
import hashlib
import json
from datetime import datetime, timezone

from .api import Client
from .auth import valid
from .common import SyncError, atomic_json, safe_dir

COURSE_FIELDS = ("id", "name", "course_code", "workflow_state", "start_at", "end_at")
ASSIGNMENT_FIELDS = ("id", "name", "html_url", "due_at", "unlock_at", "lock_at",
                     "points_possible", "grading_type", "submission_types",
                     "published", "quiz_id")
SUBMISSION_FIELDS = ("workflow_state", "submitted_at", "graded_at", "score", "grade", "attempt")


def slim_course(course):
    return {key: course.get(key) for key in COURSE_FIELDS}


def slim_assignment(assignment):
    row = {key: assignment.get(key) for key in ASSIGNMENT_FIELDS}
    discussion = assignment.get("discussion_topic")
    row["discussion_id"] = discussion.get("id") if isinstance(discussion, dict) else None
    submission = assignment.get("submission")
    row["submission"] = ({key: submission.get(key) for key in SUBMISSION_FIELDS}
                         if isinstance(submission, dict) else None)
    return row


def sha(data):
    serial = json.dumps(data, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()


def compare(old, new):
    previous = {x["id"]: x for x in old["assignments"]} if old else {}
    current = {x["id"]: x for x in new["assignments"]}
    return {"added": sorted(set(current) - set(previous)),
            "removed": sorted(set(previous) - set(current)),
            "modified": sorted(k for k in set(previous) & set(current)
                               if previous[k] != current[k]),
            "course_changed": bool(old and old["course"] != new["course"])}


def sync(runtime, course_id=82862, client_factory=Client):
    record, _ = valid(runtime)
    client = client_factory(record["token"])
    if client.profile() != record["account_id"]:
        raise SyncError("La cuenta Canvas no coincide; no se guarda información.")
    course = client.course(course_id)
    assignments = client.assignments(course_id)
    if any(not isinstance(x, dict) or not isinstance(x.get("id"), int) for x in assignments):
        raise SyncError("Canvas devolvió una actividad sin ID; no se guarda snapshot.")
    ids = [x["id"] for x in assignments]
    if len(ids) != len(set(ids)):
        raise SyncError("Canvas devolvió IDs duplicados; no se guarda snapshot.")
    data = {"schema_version": 1, "source": "canvas-api",
            "course": slim_course(course),
            "assignments": sorted((slim_assignment(x) for x in assignments),
                                  key=lambda x: x["id"])}
    digest = sha(data)
    location = safe_dir(runtime, "procesamiento", "canvas-sync", str(course_id))
    target = location / "latest.json"
    if target.is_symlink():
        raise SyncError("El snapshot es una ruta redirigida.")
    old = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
    if old and old.get("sha256") == digest:
        print(f"Curso {course_id}: sin cambios ({len(assignments)} actividades).")
        return {"changed": False, "count": len(assignments)}
    changes = compare(old["data"] if old else None, data)
    moment = datetime.now(timezone.utc)
    result = {"captured_at_utc": moment.isoformat(), "sha256": digest,
              "data": data, "changes": changes}
    revisions = safe_dir(location, "snapshots")
    atomic_json(revisions / f"{moment.strftime('%Y%m%dT%H%M%S%fZ')}-{digest[:12]}.json", result)
    atomic_json(target, result)
    print(f"Curso {course_id}: {len(assignments)} actividades; "
          f"nuevas {len(changes['added'])}, modificadas {len(changes['modified'])}, "
          f"retiradas {len(changes['removed'])}.")
    print("Evidencia local:", target)
    return {"changed": True, "count": len(assignments), "changes": changes}


def list_courses(runtime, client_factory=Client):
    record, _ = valid(runtime)
    client = client_factory(record["token"])
    if client.profile() != record["account_id"]:
        raise SyncError("La cuenta Canvas no coincide.")
    courses = client.courses()
    rows = sorted((slim_course(x) for x in courses if isinstance(x, dict)
                   and isinstance(x.get("id"), int)), key=lambda x: x["id"])
    location = safe_dir(runtime, "procesamiento", "canvas-sync")
    atomic_json(location / "courses.json", {
        "source": "canvas-api", "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "courses": rows})
    print(f"Cursos recuperados: {len(rows)}. Evidencia guardada localmente.")
    for c in rows:
        print(f"  {c['id']}: {c['name']}")
    return rows
