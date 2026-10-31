"""Alta segura de materias y semestres aprobados, sin datos académicos inventados.

El cambio se planifica como operaciones de celda reproducibles y se publica
mediante el mismo protocolo de lock, SHA, checkpoint y Drive API de V2.1.
"""
from academic_store.sheetview import open_book

from .common import SyncError
from .semester import _validate
from .workbook_blocks import _sheet_data, _id, _blocks, _block_number

SUBJECT_HEADERS = (
    "materia_id", "semestre", "nombre", "codigo", "profesor", "estado", "prioridad",
    "ultima_clase", "ultima_actualizacion", "ruta_carpeta", "notas", "lms",
    "curso_externo_id", "seccion_externa", "periodo_externo", "bloques",
)
TERM_HEADERS = (
    "semestre_id", "nombre", "fecha_inicio", "fecha_fin", "estado", "notas",
    "bloque_activo",
)
MAX_ROWS = 200


def _free_row(sheet, reserved, count):
    for i in range(2, MAX_ROWS + 1):
        if i in reserved:
            continue
        if all(sheet.Cells.Item(i, j).Value in (None, "") for j in range(1, count + 1)):
            reserved.add(i)
            return i
    raise SyncError("No hay filas libres en la plantilla V2; se requiere ampliarla.")


def _safe_canvas_text(value, what, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise SyncError(f"{what} de Canvas falta o no es válido.")
    clean = value.strip()
    if clean.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        raise SyncError(f"{what} contiene un prefijo no admitido por Excel.")
    return clean


def _approved_catalog(registry, canvas_courses):
    """Corroborar en vivo las materias NUEVAS y el nombre visto al aprobar."""
    pending = [c for c in registry["courses"]
               if c.get("provenance") == "aprobado_local_pendiente_excel"]
    if not pending:
        return {}
    if canvas_courses is None:
        raise SyncError("Para altas nuevas es obligatorio verificar Canvas en vivo.")
    if len(canvas_courses) != len({r["course_id"] for r in canvas_courses}):
        raise SyncError("Canvas devolvió cursos duplicados.")
    catalog = {r["course_id"]: r for r in canvas_courses}
    for c in pending:
        live = catalog.get(c["course_id"])
        if live is None:
            raise SyncError(f"Curso Canvas {c['course_id']} dejó de estar visible.")
        name = _safe_canvas_text(live.get("name"), "Nombre", 250)
        code = live.get("course_code") or ""
        if not isinstance(code, str) or len(code) > 120 or code.startswith(("=", "+", "-", "@")):
            raise SyncError("Código de curso Canvas no válido.")
        if name != c["canvas_name"] or code.strip() != c["canvas_code"]:
            raise SyncError(f"El curso Canvas {c['course_id']} cambió desde su aprobación.")
    return catalog


def plan_enrollment(path, registry, canvas_courses=None):
    """Previsualizar altas y transición; no escribe en el workbook."""
    _validate(registry)
    active = registry["active_semester"]
    block = registry["active_block"]
    if block not in (1, 2):
        raise SyncError("Bloque activo no confirmado.")
    catalog = _approved_catalog(registry, canvas_courses)
    app = book = None
    try:
        app, book = open_book(path)
        sheet = book.Worksheets.Item("01 - Materias")
        term_sheet = book.Worksheets.Item("05 - Semestres")
        subjects, header = _sheet_data(sheet, SUBJECT_HEADERS[:-1], "bloques", 16)
        terms, term_header = _sheet_data(term_sheet, TERM_HEADERS[:-1], "bloque_activo", 7)
        if header != "bloques" or term_header != "bloque_activo":
            raise SyncError("La base canónica aún no tiene el esquema V2.1 de bloques.")
        if len(subjects) >= MAX_ROWS - 1 or len(terms) >= MAX_ROWS - 1:
            raise SyncError("Capacidad de filas agotada.")
        canvas_ids = {}
        for sid, (_, row) in subjects.items():
            cid = _id(row.get("curso_externo_id"))
            if cid is not None:
                if cid in canvas_ids:
                    raise SyncError("ID Canvas duplicado entre materias del Excel.")
                canvas_ids[cid] = sid
        active_terms = [tid for tid, (_, r) in terms.items()
                        if str(r.get("estado") or "").casefold() == "activo"]
        # Una base nueva (sin ningún semestre) recibe aquí su primer semestre.
        if len(active_terms) != 1 and (terms or subjects):
            raise SyncError("La base debe tener exactamente un semestre Activo.")
        changed = []
        subj_rows = {rownum for rownum, _ in subjects.values()}
        term_rows = {rownum for rownum, _ in terms.values()}
        next_term = active not in terms
        if next_term:
            name = f"Semestre {int(active[-1])} - {active[:4]}"
            new_term_row = _free_row(term_sheet, term_rows, len(TERM_HEADERS))
            term_values = (active, name, None, None, "Activo", None, block)
            changed.extend(("05 - Semestres", new_term_row, i, None, value)
                           for i, value in enumerate(term_values, 1) if value is not None)
        else:
            rownum, entry = terms[active]
            if _block_number(entry.get("bloque_activo")) != block:
                changed.append(("05 - Semestres", rownum, 7,
                                entry.get("bloque_activo"), block))
            if str(entry.get("estado") or "").casefold() != "activo":
                changed.append(("05 - Semestres", rownum, 5, entry.get("estado"), "Activo"))
        for old_sem in active_terms:
            if old_sem != active:
                oldrow, olddata = terms[old_sem]
                changed.append(("05 - Semestres", oldrow, 5, olddata["estado"], "Inactivo"))

        for c in registry["courses"]:
            sid = c["subject_id"]
            if not c["blocks"]:
                raise SyncError(f"Falta asociación a bloques para {sid}.")
            if c["semester"] not in terms and c["semester"] != active:
                raise SyncError(f"No se publicará una materia de semestre desconocido: {sid}.")
            previous = subjects.get(sid)
            if previous:
                rownum, data = previous
                if str(data.get("semestre") or "") != c["semester"]:
                    raise SyncError(f"Semestre en conflicto para {sid}.")
                stored_id = _id(data.get("curso_externo_id"))
                if stored_id != c["course_id"] or str(data.get("lms") or "").lower() != "canvas":
                    raise SyncError(f"Curso Canvas/LMS contradictorio para {sid}.")
                existing_blocks = _blocks(data.get("bloques"))
                if existing_blocks != c["blocks"]:
                    changed.append(("01 - Materias", rownum, 16, data.get("bloques"),
                                    ",".join(map(str, c["blocks"]))))
                # Registros históricos se mantienen intactos salvo su pertenencia a bloques.
                continue
            if c.get("provenance") != "aprobado_local_pendiente_excel":
                raise SyncError(f"La materia {sid} falta en la base y no fue aprobada.")
            canonical_id = "MAT-{}-C{}".format(c["semester"].replace("-", ""), c["course_id"])
            if sid != canonical_id:
                raise SyncError("La materia nueva no tiene un ID interno generado por Academic Manager.")
            if c["semester"] != active:
                raise SyncError(f"No incorporar {sid} fuera del semestre activo aprobado.")
            if c["course_id"] in canvas_ids:
                raise SyncError(f"Canvas {c['course_id']} ya pertenece a otra materia.")
            if c["course_id"] not in catalog:
                raise SyncError(f"Falta confirmación Canvas de {sid}.")
            name = _safe_canvas_text(catalog[c["course_id"]]["name"], "Nombre", 250)
            code = str(catalog[c["course_id"]].get("course_code") or "").strip()
            values = (
                sid, c["semester"], name, code or None, None,
                "Activa", None, None, None, None, None,
                "Canvas", str(c["course_id"]), None, None,
                ",".join(map(str, c["blocks"]))
            )
            rownum = _free_row(sheet, subj_rows, len(SUBJECT_HEADERS))
            changed.extend(("01 - Materias", rownum, i, None, value)
                           for i, value in enumerate(values, 1) if value is not None)
            canvas_ids[c["course_id"]] = sid
        return changed
    finally:
        if book is not None:
            book.Close(False)
        if app is not None:
            app.Quit()


def publish_enrollment(runtime, registry, *, dry_run=True, api=None, catalog=None):
    """Verificar Canvas antes de una eventual publicación; no confirma solo."""
    if catalog is None and any(c.get("provenance") == "aprobado_local_pendiente_excel"
                               for c in registry["courses"]):
        from .semester import list_canvas_courses
        catalog = list_canvas_courses(runtime)
    from .workbook_blocks import publish, mutate
    def planner(path, r):
        return plan_enrollment(path, r, canvas_courses=catalog)
    result = publish(runtime, registry, dry_run=dry_run, api=api,
                     planner=planner, writer=mutate)
    if not dry_run and result["mode"] in ("published", "unchanged"):
        # Tras la confirmación canónica deja de ser una matrícula preparatoria.
        import json
        from .semester import _location, load_registry
        from capture.storage.metadata import atomic_json
        latest = load_registry(runtime)
        promoted = json.loads(json.dumps(latest))
        for course in promoted["courses"]:
            if course.get("provenance") == "aprobado_local_pendiente_excel":
                course["provenance"] = "workbook"
        atomic_json(_location(runtime), _validate(promoted))
    return result
