"""Canvas Sync V0.1: academic evidence. Canvas operations are ONLY authenticated GET.

No quiz questions, attempts, discussion entries, classmates, binary downloads,
external URLs, HTML execution, or modification of the canonical workbook.
"""
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlsplit

from capture.storage.job import job_lock
from .api import Client, ORIGIN
from .auth import valid
from .common import SyncError, atomic_json, safe_dir
from .sync import sha

MAX_ACTIVE_COURSES = 12
MAX_REQUESTS = 180
MAX_PAGE_DETAILS = 30
MAX_MODULES = 30
TEXT_LIMIT = 40000
URL_LIMIT = 120


class TextExtractor(HTMLParser):
    """Non-executable text extraction, retaining bounded URL references."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.links = []
        self.ignored = 0

    def handle_starttag(self, tag, attributes):
        if tag in ("script", "style", "noscript"):
            self.ignored += 1
        if self.ignored:
            return
        if tag in ("p", "li", "div", "br", "h1", "h2", "h3", "tr"):
            self.blocks.append("\n")
        for name, value in attributes:
            if name in ("href", "src") and isinstance(value, str):
                url = value.strip()
                parts = urlsplit(url)
                if (parts.scheme in ("http", "https") and parts.netloc
                        or (not parts.scheme and not parts.netloc and url.startswith("/"))):
                    if len(self.links) < URL_LIMIT and url not in self.links:
                        self.links.append(url)
            if tag == "img" and name == "alt" and value:
                self.blocks.append(value)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.ignored = max(0, self.ignored - 1)
        if not self.ignored and tag in ("p", "li", "div", "h1", "h2", "h3", "tr"):
            self.blocks.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.blocks.append(data)


def content_from_html(raw):
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise SyncError("Canvas devolvió una descripción con formato inválido.")
    if len(raw) > 1_000_000:
        raise SyncError("La descripción HTML supera el límite de seguridad.")
    parser = TextExtractor()
    parser.feed(raw)
    lines = [" ".join(line.split()) for line in "".join(parser.blocks).splitlines()]
    text = "\n".join(line for line in lines if line)
    return {"text": text[:TEXT_LIMIT], "truncated": len(text) > TEXT_LIMIT,
            "links": parser.links, "source_format": "canvas_html"}


def fields(value, keys):
    if not isinstance(value, dict):
        raise SyncError("Canvas devolvió un objeto de formato inesperado.")
    return {key: value.get(key) for key in keys}


def strict_ids(rows, name):
    if not isinstance(rows, list):
        raise SyncError(f"Canvas devolvió una lista inválida de {name}.")
    ids = [r.get("id") for r in rows if isinstance(r, dict)]
    if len(ids) != len(rows) or any(type(i) is not int for i in ids) or len(ids) != len(set(ids)):
        raise SyncError(f"IDs inválidos o duplicados en {name}.")
    return rows


def provisional_kind(item):
    """Only a hint for review; never authorizes an XLSX import."""
    title = (item.get("name") or "").strip().casefold()
    if title.startswith("nota final"):
        return "nota_agregada"
    if "class recordings" in title or "tutor & class link" in title:
        return "informativo"
    if "learning agreement" in title or "acta de inicio" in title:
        return "informativo_o_evaluable"
    if "actividad complementaria" in title:
        return "complementaria"
    if item.get("quiz_id"):
        return "cuestionario"
    if isinstance(item.get("discussion_topic"), dict):
        return "foro_evaluable"
    return "actividad_por_revisar"


ASSIGNMENT_KEYS = ("id", "name", "html_url", "due_at", "unlock_at", "lock_at",
                   "points_possible", "grading_type", "submission_types",
                   "published", "quiz_id", "assignment_group_id",
                   "has_submitted_submissions", "allowed_extensions", "peer_reviews",
                   "position", "muted", "workflow_state")
SUBMISSION_KEYS = ("workflow_state", "submitted_at", "graded_at", "score", "grade",
                   "attempt", "late", "missing", "excused")
COURSE_KEYS = ("id", "name", "course_code", "workflow_state", "start_at", "end_at",
               "time_zone", "default_view")
FILE_KEYS = ("id", "display_name", "filename", "size", "content-type", "locked", "hidden",
             "unlock_at", "lock_at", "folder_id", "updated_at", "html_url")
QUIZ_KEYS = ("id", "title", "html_url", "quiz_type", "time_limit", "allowed_attempts",
             "question_count", "points_possible", "due_at", "unlock_at",
             "lock_at", "published", "assignment_id", "one_question_at_a_time")
DISCUSSION_KEYS = ("id", "title", "html_url", "assignment_id", "posted_at",
                   "lock_at", "delayed_post_at", "discussion_type", "published", "read_state")


def assignment_row(raw):
    row = fields(raw, ASSIGNMENT_KEYS)
    row["kind_hint"] = provisional_kind(raw)
    row["classification_confirmed"] = False
    row["instructions"] = content_from_html(raw.get("description"))
    rubric = raw.get("rubric") or []
    if not isinstance(rubric, list):
        raise SyncError("Formato de rúbrica no válido.")
    row["rubric"] = []
    for c in rubric:
        if not isinstance(c, dict):
            raise SyncError("Criterio de rúbrica inválido.")
        row["rubric"].append({
            "id": c.get("id"), "description": c.get("description"),
            "long_description": content_from_html(c.get("long_description")),
            "points": c.get("points"),
            "ratings": [fields(x, ("id", "description", "points", "long_description"))
                        for x in (c.get("ratings") or [])]})
    row["discussion_id"] = (raw["discussion_topic"].get("id")
                            if isinstance(raw.get("discussion_topic"), dict) else None)
    row["submission"] = (fields(raw["submission"], SUBMISSION_KEYS)
                          if isinstance(raw.get("submission"), dict) else None)
    return row


def normalize(name, rows):
    if name == "pages":
        if not isinstance(rows, list) or any(not isinstance(x, dict)
                                             or not isinstance(x.get("url"), str)
                                             for x in rows):
            raise SyncError("Páginas Canvas inválidas.")
        slugs = [x["url"] for x in rows]
        if len(slugs) != len(set(slugs)):
            raise SyncError("Canvas devolvió páginas duplicadas.")
    else:
        strict_ids(rows, name)
    if name == "assignments":
        return sorted([assignment_row(x) for x in rows], key=lambda x: x["id"])
    if name == "assignment_groups":
        return sorted([{"id": r["id"], "name": r.get("name"),
                        "group_weight": r.get("group_weight"), "position": r.get("position"),
                        "assignment_ids": [x["id"] for x in (r.get("assignments") or [])
                                           if isinstance(x, dict) and type(x.get("id")) is int]}
                       for r in rows], key=lambda x: x["id"])
    if name == "files":
        return sorted([fields(x, FILE_KEYS) for x in rows], key=lambda x: x["id"])
    if name == "quizzes":
        return sorted([{**fields(x, QUIZ_KEYS),
                        "instructions": content_from_html(x.get("description"))}
                       for x in rows], key=lambda x: x["id"])
    if name == "discussion_topics":
        return sorted([{**fields(x, DISCUSSION_KEYS),
                        "instructions": content_from_html(x.get("message"))}
                       for x in rows], key=lambda x: x["id"])
    if name == "pages":
        return sorted([fields(x, ("url", "title", "published", "front_page",
                                  "updated_at", "html_url")) for x in rows],
                      key=lambda x: x["url"] or "")
    if name == "announcements":
        return sorted([{**fields(x, ("id", "title", "html_url", "posted_at",
                                     "delayed_post_at", "discussion_topic_id")),
                        "instructions": content_from_html(x.get("message"))}
                       for x in rows], key=lambda x: x["id"])
    raise SyncError("Categoría no reconocida.")


class BudgetClient(Client):
    def __init__(self, token, max_requests=MAX_REQUESTS, opener=None):
        super().__init__(token, opener=opener)
        self.requests = 0
        self.max_requests = max_requests

    def get(self, url):
        if self.requests >= self.max_requests:
            raise SyncError("Presupuesto de solicitudes GET agotado: no se publica un snapshot parcial.")
        self.requests += 1
        return super().get(url)


def optional_pages(client, path, params=None, *, name):
    try:
        return {"status": "ok", "data": normalize(name, client.pages(path, params))}
    except SyncError as exc:
        message = str(exc)
        if "HTTP 403" in message or "HTTP 404" in message:
            return {"status": "forbidden" if "403" in message else "not_found",
                    "data": None}
        raise


def optional_object(client, path, keys=None, html_key=None):
    try:
        raw, _ = client.get(ORIGIN + path)
    except SyncError as exc:
        message = str(exc)
        if "HTTP 403" in message or "HTTP 404" in message:
            return {"status": "forbidden" if "403" in message else "not_found",
                    "data": None}
        raise
    result = fields(raw, keys) if keys else {}
    if html_key:
        result["content"] = content_from_html(raw.get(html_key))
    return {"status": "ok", "data": result}


def build_course(client, course_id):
    course = client.course(course_id)  # Auth and course identity already checked by caller.
    base = f"/api/v1/courses/{course_id}"
    assignments = client.assignments(course_id)
    dataset = {"schema_version": 2, "source": "canvas_rest_get_only",
               "course": fields(course, COURSE_KEYS),
               "assignments": normalize("assignments", assignments),
               "sections": {}}
    opts = [
        ("assignment_groups", base + "/assignment_groups",
         {"per_page": 100, "include[]": ["assignments"]}),
        ("quizzes", base + "/quizzes", {"per_page": 100}),
        ("discussion_topics", base + "/discussion_topics", {"per_page": 100}),
        ("files", base + "/files", {"per_page": 100}),
        ("pages", base + "/pages", {"per_page": 100}),
        ("announcements", "/api/v1/announcements",
         {"per_page": 100, "context_codes[]": [f"course_{course_id}"]}),
    ]
    for name, path, params in opts:
        dataset["sections"][name] = optional_pages(client, path, params, name=name)

    module_rows = strict_ids(client.pages(base + "/modules", {"per_page": 100}), "modules")
    if len(module_rows) > MAX_MODULES:
        raise SyncError("Cantidad de módulos fuera del límite de extracción.")
    modules = []
    for m in module_rows:
        rows = strict_ids(client.pages(base + f"/modules/{m['id']}/items",
                                       {"per_page": 100}), "module_items")
        modules.append({
            **fields(m, ("id", "name", "position", "published", "items_count",
                         "require_sequential_progress", "state")),
            "items": sorted([fields(x, ("id", "title", "type", "content_id",
                                        "html_url", "page_url", "external_url",
                                        "completion_requirement", "published", "position"))
                             for x in rows], key=lambda x: x["id"])})
    dataset["sections"]["modules"] = {"status": "ok", "data": sorted(modules, key=lambda x: x["id"])}
    dataset["sections"]["front_page"] = optional_object(
        client, base + "/front_page",
        ("url", "title", "published", "updated_at", "html_url"), html_key="body")

    # Detail pages: only course-owned API GET. Never fetch iframe/external URLs.
    listed_pages = dataset["sections"]["pages"]
    if listed_pages["status"] == "ok":
        pages = listed_pages["data"]
        if len(pages) > MAX_PAGE_DETAILS:
            raise SyncError("Demasiadas páginas para inspeccionar sin revisión manual.")
        details = []
        for page in pages:
            slug = page.get("url")
            if not isinstance(slug, str) or not slug:
                continue
            detail = optional_object(client, base + "/pages/" + quote(slug, safe=""),
                                     ("url", "title", "published", "updated_at", "html_url"),
                                     html_key="body")
            details.append({"url": slug, **detail})
        dataset["sections"]["page_details"] = {"status": "ok", "data": details}
    else:
        dataset["sections"]["page_details"] = {"status": "not_available", "data": None}
    return dataset


def compact_user_item(raw, keys):
    row = fields(raw, keys)
    if "plannable_type" in row:
        plannable = raw.get("plannable")
        row["title"] = (plannable.get("title") or plannable.get("name")
                        if isinstance(plannable, dict) else None)
    return row


def build_user(client, course_ids, today=None):
    now = today or date.today()
    beginning, ending = now - timedelta(days=45), now + timedelta(days=150)
    result = {"schema_version": 2, "source": "canvas_rest_get_only",
              "planner_window": {"from": beginning.isoformat(), "to": ending.isoformat()},
              "sections": {}}
    configs = [
        ("enrollments", "/api/v1/users/self/enrollments", {"per_page": 100}),
        ("todo", "/api/v1/users/self/todo", {"per_page": 100}),
        ("upcoming", "/api/v1/users/self/upcoming_events", {"per_page": 100}),
        ("planner", "/api/v1/planner/items",
         {"per_page": 100, "start_date": beginning.isoformat(), "end_date": ending.isoformat()}),
    ]
    wanted = set(course_ids)
    for name, path, params in configs:
        try:
            rows = client.pages(path, params)
        except SyncError as exc:
            if "HTTP 403" in str(exc) or "HTTP 404" in str(exc):
                result["sections"][name] = {"status": "unavailable", "data": None}
                continue
            raise
        if name == "enrollments":
            filtered = []
            for r in rows:
                if not isinstance(r, dict) or r.get("course_id") not in wanted:
                    continue
                grades = r.get("grades") if isinstance(r.get("grades"), dict) else {}
                filtered.append({"course_id": r["course_id"], "type": r.get("type"),
                                 "enrollment_state": r.get("enrollment_state"),
                                 "grades": fields(grades, ("current_score", "final_score",
                                                          "current_grade", "final_grade"))})
        else:
            allowed = ("id", "course_id", "context_code", "assignment_id",
                       "html_url", "title", "type", "due_at", "plannable_id",
                       "plannable_type", "plannable_date")
            filtered = [compact_user_item(r, allowed) for r in rows if isinstance(r, dict)]
            # Planner/todo may contain other contexts; preserve context_code for review.
        result["sections"][name] = {"status": "ok", "data": filtered}
    return result


def snapshot(root, slot, data):
    """Content addressed, atomic and idempotent. Old Canvas Sync V0 untouched."""
    directory = safe_dir(root, "procesamiento", "canvas-sync", "v01", str(slot))
    with job_lock(directory):
        latest = directory / "latest.json"
        if latest.is_symlink():
            raise SyncError("Ruta local de snapshot redirigida.")
        digest = sha(data)
        if latest.is_file():
            from json import loads
            old = loads(latest.read_text(encoding="utf-8"))
            if old.get("sha256") == digest:
                return False
        instant = datetime.now(timezone.utc)
        record = {"captured_at_utc": instant.isoformat(), "sha256": digest, "data": data}
        history = safe_dir(directory, "snapshots")
        stamp = instant.strftime("%Y%m%dT%H%M%S%fZ")
        atomic_json(history / f"{stamp}-{digest[:12]}.json", record)
        atomic_json(latest, record)
        return True


def sync_rich(runtime, course_id=None, all_active=False, client_factory=BudgetClient,
              course_ids=None):
    cred, _ = valid(runtime)
    client = client_factory(cred["token"])
    if client.profile() != cred["account_id"]:
        raise SyncError("La cuenta de Canvas cambió; no se guardan datos.")
    del cred
    if course_ids is not None:
        if all_active or course_id is not None:
            raise SyncError("Seleccione un modo de cursos, no varios.")
        if not isinstance(course_ids, (list, tuple)) or not course_ids:
            raise SyncError("No hay cursos registrados visibles para sincronizar.")
        ids = list(course_ids)
        if (len(ids) > MAX_ACTIVE_COURSES or any(type(i) is not int or i <= 0 for i in ids)
                or len(set(ids)) != len(ids)):
            raise SyncError("Lista de cursos registrados inválida.")
        available = strict_ids(client.courses(), "cursos activos")
        if not set(ids).issubset({x["id"] for x in available}):
            raise SyncError("Uno o más cursos seleccionados no están visibles en Canvas.")
    elif all_active:
        available = client.courses()
        strict_ids(available, "cursos activos")
        ids = sorted(x["id"] for x in available)
        if not ids or len(ids) > MAX_ACTIVE_COURSES:
            raise SyncError("Cantidad de cursos activos inesperada; use --course-id.")
    else:
        if type(course_id) is not int or course_id <= 0:
            raise SyncError("Indique un course ID válido.")
        ids = [course_id]
    # Fetch/validate everything BEFORE writing any snapshot, so connectivity failures
    # or quota exhaustion do not publish partially refreshed courses.
    collected = [(cid, build_course(client, cid)) for cid in ids]
    user = build_user(client, ids)
    results = []
    for cid, data in collected:
        changed = snapshot(runtime, cid, data)
        row = {"course_id": cid, "assignments": len(data["assignments"]),
               "rubrics": sum(bool(a["rubric"]) for a in data["assignments"]),
               "graded": sum(bool(a.get("submission") and
                                  a["submission"]["workflow_state"] == "graded")
                             for a in data["assignments"]),
               "files": len(data["sections"]["files"]["data"] or []),
               "changed": changed}
        results.append(row)
        print(f"Curso {cid}: {row['assignments']} registros, rúbricas {row['rubrics']}, "
              f"calificadas {row['graded']}, archivos indexados {row['files']}, "
              f"{'nuevo snapshot' if changed else 'sin cambios'}.", flush=True)
    snapshot(runtime, "user", user)
    print(f"Canvas Sync V0.1: {len(results)} cursos procesados; "
          f"{getattr(client, 'requests', '?')} solicitudes GET. "
          "Solo evidencia local; Canvas y Excel no modificados por el programa.", flush=True)
    return results
