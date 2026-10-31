"""Matrícula multisemestre: catálogo Canvas y correspondencias persistentes.

La correspondencia aprobada vive localmente en estado PREPARATORIO y no crea
filas en el workbook. La fuente definitiva futura es la base publicada.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from academic_store import store

from .auth import valid
from .common import SyncError, atomic_json, safe_dir
from .enriched import BudgetClient, strict_ids
from .sync import sha

SCHEMA = 2
# Migración acotada a hechos confirmados de 2026-2; no predice inglés futuro.
CURRENT_2026_BLOCKS = {"MAT-ING3": [1, 2], "MAT-GBD": [2], "MAT-SO2": [2],
                       "MAT-SER": [2], "MAT-ST": [2]}
CURRENT_EXCEPTION = ("MAT-ST", 82892, "2026-2")  # Migración documentada, nunca usada en otros semestres.


def _valid_blocks(blocks, *, empty_ok=False):
    if not isinstance(blocks, list) or any(type(b) is not int or b not in (1, 2) for b in blocks):
        raise SyncError("La materia requiere una lista de bloques 1/2.")
    if len(blocks) != len(set(blocks)) or (not blocks and not empty_ok):
        raise SyncError("Bloques vacíos o duplicados.")
    return sorted(blocks)


def upgrade_registry(old):
    """Migración unidireccional de V1; solo 2026-2 tiene bloques confirmados."""
    if old.get("schema_version") == SCHEMA:
        return _validate(old)
    if old.get("schema_version") != 1:
        raise SyncError("Versión del registro de materias no soportada.")
    registry = json.loads(json.dumps(old))
    registry["schema_version"] = SCHEMA
    registry["active_block"] = 2 if registry.get("active_semester") == "2026-2" else None
    for row in registry.get("courses", []):
        confirmed = (CURRENT_2026_BLOCKS.get(row.get("subject_id"), [])
                     if row.get("semester") == "2026-2" else [])
        row["blocks"] = list(confirmed)
    return _validate(registry)


def _validate(registry):
    if registry.get("schema_version") != SCHEMA:
        raise SyncError("Versión del registro de semestres desconocida.")
    active = registry.get("active_semester")
    if not isinstance(active, str) or not re.fullmatch(r"20\d{2}-[1-3]", active):
        raise SyncError("Semestre activo inválido.")
    active_block = registry.get("active_block")
    if active_block is not None and (type(active_block) is not int or active_block not in (1, 2)):
        raise SyncError("Bloque activo fuera de 1/2.")
    courses = registry.get("courses")
    if not isinstance(courses, list):
        raise SyncError("Registro de cursos incompleto.")
    ids, subjects = set(), set()
    for row in courses:
        if not isinstance(row, dict) or type(row.get("course_id")) is not int or row["course_id"] <= 0:
            raise SyncError("ID Canvas no válido en registro.")
        subject, semester = row.get("subject_id"), row.get("semester")
        if not isinstance(subject, str) or not re.fullmatch(r"MAT-[A-Z0-9-]+", subject):
            raise SyncError("ID interno de materia no válido.")
        if not isinstance(semester, str) or not re.fullmatch(r"20\d{2}-[1-3]", semester):
            raise SyncError("Código de semestre no válido.")
        _valid_blocks(row.get("blocks"), empty_ok=True)  # [] = legado sin asignar, no activo
        if row.get("provenance") == "aprobado_local_pendiente_excel":
            name = row.get("canvas_name")
            if not isinstance(name, str) or not name.strip() or len(name) > 250:
                raise SyncError("Materia aprobada sin nombre verificado de Canvas.")
            if not isinstance(row.get("canvas_code"), str):
                raise SyncError("Materia aprobada sin curso/código Canvas de origen.")
        if row["course_id"] in ids or subject in subjects:
            raise SyncError("Mapeo Canvas/materia duplicado.")
        ids.add(row["course_id"])
        subjects.add(subject)
    if len(courses) > 500:
        raise SyncError("Registro de cursos supera límite operativo.")
    return registry


def _location(root):
    return safe_dir(Path(root), "control", "canvas-sync") / "course-registry.json"


def sha256_workbook(runtime):
    from .routine import workbook_version
    return workbook_version(runtime)


def _courses_from(subject_rows, subject_columns, term_rows, semester_columns):
    has_blocks = "bloques" in subject_columns
    has_active_block = "bloque_activo" in semester_columns
    if has_blocks != has_active_block:
        raise SyncError("Migración incompleta: los bloques deben existir en ambas hojas.")
    mapped = []
    for row in subject_rows:
        cid = row.get("curso_externo_id")
        if str(row.get("lms") or "").casefold() != "canvas" or cid is None:
            continue
        text_id = str(cid).strip()
        if text_id.endswith(".0"):
            text_id = text_id[:-2]
        if not text_id.isdigit():
            raise SyncError("ID de curso externo no numérico en V2.")
        mapped_row = {"course_id": int(text_id), "subject_id": str(row["materia_id"]),
                      "semester": str(row["semestre"]), "provenance": "workbook"}
        if has_blocks:
            raw = str(row.get("bloques") or "").strip().replace(" ", "")
            if raw and raw not in ("1", "2", "1,2"):
                raise SyncError("Bloques incorrectos en la base publicada.")
            mapped_row["blocks"] = [int(v) for v in raw.split(",")] if raw else []
        mapped.append(mapped_row)
    # Un solo semestre en estado Activo; no inferir de la fecha actual.
    active = [str(r["semestre_id"]) for r in term_rows
              if str(r.get("estado") or "").casefold() == "activo"]
    if len(active) != 1:
        raise SyncError("V2 requiere un único semestre activo para iniciar registro.")
    active_block = None
    if has_blocks:
        active_row = next(r for r in term_rows if str(r.get("semestre_id")) == active[0])
        value = active_row.get("bloque_activo")
        if value is not None and str(value).strip():
            raw = str(value).strip()
            if raw not in ("1", "1.0", "2", "2.0"):
                raise SyncError("Bloque activo inválido en la base publicada.")
            active_block = int(float(raw))
    # La materia Sistemas Transaccionales ya existe en V2 y Canvas fue verificado,
    # pero falta curso_externo_id en esa fila: migración acotada y explícita.
    subject, cid, semester = CURRENT_EXCEPTION
    if not any(r["subject_id"] == subject for r in mapped):
        source_rows = subject_rows
        if any(r.get("materia_id") == subject and r.get("semestre") == semester
               and str(r.get("curso_externo_id") or "").strip() == "" for r in source_rows):
            exception = {"course_id": cid, "subject_id": subject, "semester": semester,
                         "provenance": "documentado_pendiente_en_excel"}
            if has_blocks:
                raise SyncError("Migración incompleta: MAT-ST debe tener ID Canvas en Excel.")
            mapped.append(exception)
    return active[0], mapped, active_block if has_blocks else "legacy"


def workbook_courses(runtime, *, workbook_path=None):
    """Leer materias y bloques de V2 desde la base SQLite canónica.

    `workbook_path` es una copia `.sqlite` descargada y verificada; sin ella se usa la base
    local, comprobada contra el estado publicado.
    """
    if workbook_path is None:
        from .routine import workbook_version
        workbook_version(runtime)
    db = Path(workbook_path) if workbook_path is not None else Path(runtime) / store.DB_NAME
    try:
        catalog = store.read_catalog(db, ("01 - Materias", "05 - Semestres"))
        columns = {name: store.headers(db, name) for name in catalog}
    except store.StoreError as exc:
        raise SyncError(str(exc)) from exc
    if not catalog["01 - Materias"] or not catalog["05 - Semestres"]:
        raise SyncError("Materias o semestres no están definidos en V2.")
    return _courses_from(catalog["01 - Materias"], columns["01 - Materias"],
                         catalog["05 - Semestres"], columns["05 - Semestres"])

def is_empty_base(runtime):
    """Base publicada recién creada (sin materias ni semestres) y todavía sin registro local."""
    if _location(runtime).exists():
        return False
    try:
        sha256_workbook(runtime)
        catalog = store.read_catalog(Path(runtime) / store.DB_NAME, ("01 - Materias", "05 - Semestres"))
    except (SyncError, store.StoreError):
        return False
    return not catalog["01 - Materias"] and not catalog["05 - Semestres"]


def load_registry(runtime):
    path = _location(runtime)
    if path.is_symlink():
        raise SyncError("El registro de cursos no puede ser redirigido.")
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        migrated = upgrade_registry(record)
        if record.get("schema_version") == 1:
            atomic_json(path, migrated)
        return migrated
    active, mapped, block = workbook_courses(runtime)
    if block == "legacy":
        registry = upgrade_registry({"schema_version": 1, "active_semester": active,
                                     "courses": mapped})
    else:
        registry = _validate({"schema_version": SCHEMA, "active_semester": active,
                              "active_block": block, "courses": mapped,
                              "workbook_sha256": sha256_workbook(runtime)})
    atomic_json(path, registry)
    return registry


def active_mappings(registry):
    _validate(registry)
    block = registry["active_block"]
    if block is None:
        raise SyncError("Seleccione el bloque activo antes de sincronizar.")
    unassigned = [x["course_id"] for x in registry["courses"]
                  if x["semester"] == registry["active_semester"] and not x["blocks"]]
    if unassigned:
        raise SyncError(f"Materias sin bloque confirmado: {sorted(unassigned)}.")
    return {x["course_id"]: x["subject_id"] for x in registry["courses"]
            if x["semester"] == registry["active_semester"] and block in x["blocks"]}


def list_canvas_courses(runtime, client_factory=BudgetClient):
    auth, _ = valid(runtime)
    client = client_factory(auth["token"])
    if client.profile() != auth["account_id"]:
        raise SyncError("La identidad Canvas es diferente.")
    rows = strict_ids(client.courses(), "cursos activos")
    if len(rows) > 100:
        raise SyncError("Lista Canvas demasiado grande para revisión segura.")
    return sorted([{"course_id": c["id"], "name": str(c.get("name") or "")[:250],
                    "course_code": str(c.get("course_code") or "")[:120]}
                   for c in rows], key=lambda x: x["course_id"])


def discovery(registry, canvas_courses):
    _validate(registry)
    ids = {x["course_id"] for x in registry["courses"]}
    visible = {x["course_id"] for x in canvas_courses}
    candidates = [x for x in canvas_courses if x["course_id"] not in ids]
    current = [c for c in registry["courses"] if c["semester"] == registry["active_semester"]
               and registry["active_block"] in c["blocks"] and c["course_id"] in visible]
    absent = [c["course_id"] for c in registry["courses"]
              if c["semester"] == registry["active_semester"]
              and registry["active_block"] in c["blocks"] and c["course_id"] not in visible]
    return {"candidates": candidates, "active_known": current, "not_visible": sorted(absent)}


def save_discovery(runtime, registry, canvas_courses):
    result = discovery(registry, canvas_courses)
    folder = safe_dir(Path(runtime), "procesamiento", "canvas-sync", "discovery")
    path = folder / "latest.json"
    if path.is_symlink():
        raise SyncError("Catálogo Canvas redirigido.")
    data = {"schema_version": SCHEMA, "active_semester": registry["active_semester"],
            "active_block": registry["active_block"],
            "courses": canvas_courses, "candidate_ids": [c["course_id"] for c in result["candidates"]]}
    fingerprint = sha(data)
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    new_ids = set(data["candidate_ids"]) - set(old.get("data", {}).get("candidate_ids", []))
    if fingerprint != old.get("sha256"):
        atomic_json(path, {"sha256": fingerprint,
                           "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                           "data": data})
    return {**result, "new_candidate_ids": sorted(new_ids), "changed": fingerprint != old.get("sha256")}


def onboard(runtime, semester, selected_ids, canvas_courses, *, course_blocks=None,
            confirm=False):
    """Confirmación explícita en una sola acción, sin modificar el XLSX."""
    if not confirm:
        raise SyncError("Falta confirmación explícita del semestre y sus cursos.")
    if not isinstance(semester, str) or not re.fullmatch(r"20\d{2}-[1-3]", semester):
        raise SyncError("Semestre no válido (YYYY-N).")
    if not selected_ids or len(selected_ids) != len(set(selected_ids)) or any(type(i) is not int for i in selected_ids):
        raise SyncError("Seleccione IDs Canvas válidos y únicos.")
    if (not isinstance(course_blocks, dict) or set(course_blocks) != set(selected_ids)):
        raise SyncError("Indique bloques explícitos para cada curso seleccionado.")
    blocks_by_course = {cid: _valid_blocks(course_blocks[cid]) for cid in selected_ids}
    from capture.storage.job import job_lock
    from capture.models import CaptureError
    try:
        with job_lock(_location(runtime).parent):
            # Base nueva del asistente: el primer semestre lo define esta alta.
            fresh = is_empty_base(runtime)
            registry = ({"schema_version": SCHEMA, "active_semester": semester, "active_block": None,
                         "courses": [], "workbook_sha256": sha256_workbook(runtime)}
                        if fresh else load_registry(runtime))
            known = {x["course_id"] for x in registry["courses"]}
            verified_courses = {x["course_id"]: x for x in canvas_courses}
            visible = set(verified_courses)
            if len(verified_courses) != len(canvas_courses):
                raise SyncError("Catálogo Canvas contiene cursos duplicados.")
            if set(selected_ids) - visible or set(selected_ids) & known:
                raise SyncError("Hay cursos inexistentes en la lista visible o ya registrados.")
            current_subjects = {x["subject_id"] for x in registry["courses"]}
            additions = []
            for cid in sorted(selected_ids):
                new_id = f"MAT-{semester.replace('-', '')}-C{cid}"
                if new_id in current_subjects:
                    raise SyncError("ID interno ya registrado.")
                name = verified_courses[cid].get("name")
                code = verified_courses[cid].get("course_code", "")
                if not isinstance(name, str) or not name.strip() or not isinstance(code, str):
                    raise SyncError("Canvas no proporcionó un nombre de curso verificable.")
                additions.append({"course_id": cid, "subject_id": new_id,
                                  "semester": semester, "blocks": blocks_by_course[cid],
                                  "canvas_name": name.strip(), "canvas_code": code.strip(),
                                  "provenance": "aprobado_local_pendiente_excel"})
            next_block = (registry["active_block"] if semester == registry["active_semester"] and not fresh
                          else min(b for blocks in blocks_by_course.values() for b in blocks))
            updated = _validate({**registry, "active_semester": semester,
                                 "active_block": next_block,
                                 "courses": registry["courses"] + additions})
            atomic_json(_location(runtime), updated)
            return additions
    except CaptureError as exc:
        raise SyncError("Registro de matrícula ocupado por otra sesión.") from exc



def set_active_block(runtime, block, *, confirm=False):
    """Cambiar de bloque sin tocar semestre, materias históricas ni Excel."""
    if not confirm:
        raise SyncError("El cambio de bloque requiere confirmación.")
    if type(block) is not int or block not in (1, 2):
        raise SyncError("Bloque activo debe ser 1 o 2.")
    from capture.storage.job import job_lock
    from capture.models import CaptureError
    try:
        with job_lock(_location(runtime).parent):
            registry = load_registry(runtime)
            if registry["active_block"] == block:
                return False
            if not any(c["semester"] == registry["active_semester"] and block in c["blocks"]
                       for c in registry["courses"]):
                raise SyncError("No hay materias asociadas al bloque solicitado.")
            changed = _validate({**registry, "active_block": block})
            atomic_json(_location(runtime), changed)
            return True
    except CaptureError as exc:
        raise SyncError("Registro de matrícula ocupado por otra sesión.") from exc



def set_course_blocks(runtime, course_id, blocks, *, confirm=False):
    """Ampliar/revisar una pertenencia sin duplicar la materia ni rehacer la matrícula."""
    if not confirm:
        raise SyncError("La pertenencia a bloques requiere confirmación.")
    if type(course_id) is not int or course_id <= 0:
        raise SyncError("ID Canvas inválido.")
    wanted = _valid_blocks(blocks)
    from capture.storage.job import job_lock
    from capture.models import CaptureError
    try:
        with job_lock(_location(runtime).parent):
            registry = load_registry(runtime)
            matches = [c for c in registry["courses"] if c["course_id"] == course_id
                       and c["semester"] == registry["active_semester"]]
            if len(matches) != 1:
                raise SyncError("La materia no pertenece al semestre activo.")
            if matches[0]["blocks"] == wanted:
                return False
            changed = json.loads(json.dumps(registry))
            for c in changed["courses"]:
                if c["course_id"] == course_id:
                    c["blocks"] = wanted
                    c["block_provenance"] = "confirmado_por_estudiante_pendiente_excel"
            atomic_json(_location(runtime), _validate(changed))
            return True
    except CaptureError as exc:
        raise SyncError("Registro de matrícula ocupado por otra sesión.") from exc
