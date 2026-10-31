"""Canvas → Actividades V2: altas verificables y actualizaciones conservadoras.

Toda publicación usa la transacción de workbook_blocks; no modifica Canvas.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from academic_store.sheetview import open_book
from .common import SyncError
from .semester import active_mappings
from .reconcile import verify_snapshot
from .classification import ACTIVITY, classify, load_decisions

FIRST_HEADERS = (
    "actividad_id", "materia_id", "tipo", "titulo", "descripcion",
    "organizacion", "modalidad", "fecha_apertura", "fecha_entrega",
    "fecha_cierre", "estado_acceso", "estado_entrega", "estado_calificacion",
    "prioridad", "puntos_posibles", "puntos_obtenidos", "peso_porcentual",
    "nota_institucional", "lms", "objeto_externo_tipo", "objeto_externo_id",
    "url_fuente", "verificacion", "procesada", "notas",
)
EXTRA_HEADERS = ("fecha_envio_real", "referencias_externas")
CAPACITY = 1000
LOCAL_ZONE = ZoneInfo("America/Bogota")


def _utc(raw):
    if not raw:
        return None
    if not isinstance(raw, str):
        raise SyncError("Timestamp no textual recibido de Canvas.")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SyncError(f"Timestamp Canvas inválido: {raw}.") from exc
    if value.tzinfo is None:
        raise SyncError("Fecha Canvas sin huso horario explícito; no se importará.")
    return value.astimezone(timezone.utc)


def _local(raw):
    value = _utc(raw)
    return value.astimezone(LOCAL_ZONE).replace(tzinfo=None) if value else None


def _excel_serial(raw):
    local = _local(raw)
    return ((local - datetime(1899, 12, 30)).total_seconds() / 86400.0
            if local is not None else None)


def _points(value, what):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise SyncError(f"{what} no numérico, negativo o no finito.")
    return value


def _states(item, instant=None):
    now = instant or datetime.now(timezone.utc)
    unlock = _utc(item.get("unlock_at"))
    lock = _utc(item.get("lock_at"))
    access = ("bloqueada_por_fecha" if unlock and now < unlock else
              "cerrada" if lock and now >= lock else
              "disponible" if unlock or lock else None)
    sub = item.get("submission") or {}
    workflow = sub.get("workflow_state")
    submitted = sub.get("submitted_at")
    score = _points(sub.get("score"), "Puntaje obtenido Canvas")
    if workflow in ("graded", "submitted") or submitted:
        delivery = "entregada"
    elif workflow == "unsubmitted":
        delivery = "pendiente"
    else:
        delivery = None
    grade = "calificada" if score is not None else None
    return access, delivery, grade, score


def _additional_refs(row):
    items = []
    for kind in ("quiz", "discussion"):
        value = row.get(kind + "_id")
        if value is not None:
            if type(value) is not int or value <= 0:
                raise SyncError(f"ID secundario Canvas inválido: {kind}.")
            items.append({"sistema": "Canvas", "tipo": kind, "id": str(value)})
    return json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",",":")) if items else None


def _type(item):
    name = str(item.get("name") or "").strip().casefold()
    kind = item.get("kind_hint")
    if kind == "complementaria":
        return "complementaria"
    if kind == "informativo_o_evaluable":
        return "actividad_informativa_calificada"
    if name == "oral exam":
        return "examen_oral"
    if kind == "cuestionario" or " exam" in name:
        return "examen"
    if kind == "foro_evaluable":
        return "foro_calificado"
    if name.startswith("homework activity"):
        return "homework"
    return "tarea"


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 2400:
        raise SyncError(f"{field} falta o excede límite.")
    out = value.strip()
    if out.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        raise SyncError(f"{field} podría ser fórmula de Excel.")
    return out


def _load_snapshots(root, selected):
    snapshots = {}
    for cid in selected:
        filename = root / "procesamiento" / "canvas-sync" / "v01" / str(cid) / "latest.json"
        if not filename.is_file():
            raise SyncError(f"Snapshot Canvas no disponible para {cid}.")
        raw = json.loads(filename.read_text(encoding="utf-8"))
        verify_snapshot(raw, cid)
        snapshots[cid] = raw
    return snapshots


def plan_activities(path, registry, snapshots, decisions, *, allow_new_courses=None, now=None):
    """Operaciones idempotentes (hoja, fila, columna, antes, después).

    Nuevas actividades: solo materias del bloque activo. Calificaciones
    tardías: cualquier materia registrada que siga visible.
    """
    mapping = {r["course_id"]: r["subject_id"] for r in registry["courses"]}
    active = active_mappings(registry)
    for cid, raw in snapshots.items():
        if cid not in mapping:
            raise SyncError(f"Curso Canvas {cid} no está asociado a Academic Manager.")
        verify_snapshot(raw, cid)
    selections, _ = classify(snapshots, decisions)
    app = book = None
    changes, exceptions = [], []
    try:
        app, book = open_book(path)
        sheet = book.Worksheets.Item("02 - Actividades")
        header = tuple(str(sheet.Cells.Item(1, c).Value or "").strip()
                       for c in range(1, len(FIRST_HEADERS) + len(EXTRA_HEADERS) + 1))
        if (header[:len(FIRST_HEADERS)] != FIRST_HEADERS
                or any(header[len(FIRST_HEADERS)+i] not in ("", value)
                       for i,value in enumerate(EXTRA_HEADERS))):
            raise SyncError("La hoja de actividades no mantiene el contrato V2.1.")
        for i, title in enumerate(EXTRA_HEADERS, len(FIRST_HEADERS)+1):
            if not header[i-1]:
                changes.append(("02 - Actividades",1,i,None,title))
        used = sheet.UsedRange.Value2
        records = []
        existing = {}
        by_id = set()
        reserved = set()
        empty_rows = []
        for number, row in enumerate(used[1:], 2):
            if not isinstance(row, tuple):
                continue
            values = dict(zip(FIRST_HEADERS+EXTRA_HEADERS, row))
            ident = values["actividad_id"]
            if not ident:
                if all(v is None or v == "" for v in row):
                    empty_rows.append(number)
                elif any(v is not None and v != "" for v in row):
                    reserved.add(number)
                continue
            ident = str(ident)
            if ident in by_id:
                raise SyncError("IDs internos de actividad duplicados.")
            by_id.add(ident)
            reserved.add(number)
            records.append((number, values))
            if str(values.get("lms") or "").casefold() == "canvas":
                external = values.get("objeto_externo_id")
                if external is not None:
                    key = (str(values["materia_id"]),str(external).removesuffix(".0"))
                    if key in existing:
                        raise SyncError("Dos actividades vinculadas al mismo Canvas assignment.")
                    existing[key] = (number, values)
        for key, item in sorted(selections.items()):
            category = item["category"]
            row = item["row"]
            cid = item["course_id"]
            aid = item["assignment_id"]
            subject = mapping[cid]
            mapped = existing.get((subject, str(aid)))
            if category != ACTIVITY:
                if category == "revisar":
                    exceptions.append(key)
                if mapped:
                    # No borrar una actividad ya existente por una clasificación ambigua.
                    exceptions.append(f"{key}:actividad_preexistente_no_confirmada")
                continue
            if row.get("published") is not True:
                exceptions.append(f"{key}:no_publicada")
                continue
            points = _points(row.get("points_possible"), "Puntos posibles Canvas")
            access, delivery, grade, score = _states(row, now)
            if mapped:
                number, values = mapped
                if (values.get("titulo") and str(values["titulo"]).strip().casefold()
                    != str(row.get("name") or "").strip().casefold()):
                    exceptions.append(f"{key}:titulo_difiere")
                if (points is not None and values.get("puntos_posibles") is not None
                    and float(values["puntos_posibles"]) != points):
                    exceptions.append(f"{key}:puntos_posibles_conflicto")
                # Campos manuales y fechas históricas nunca se sobreescriben.
                for col, desired in ((11, access), (12, delivery), (13, grade),
                                     (16, score)):
                    if desired is None:
                        continue
                    before = values[FIRST_HEADERS[col-1]]
                    if col == 12 and before == "entregada" and desired == "pendiente":
                        continue
                    if col == 13 and before == "calificada" and desired != "calificada":
                        continue
                    if before != desired:
                        changes.append(("02 - Actividades", number, col, before, desired))
                submitted = _excel_serial((row.get("submission") or {}).get("submitted_at"))
                saved_submitted = values.get("fecha_envio_real")
                if submitted is not None:
                    if saved_submitted is None:
                        changes.append(("02 - Actividades", number, 26, None, submitted))
                    # Si Canvas cambia una fecha ya estructurada, revisar: no
                    # sobrescribir sin determinar si es reenvío o rectificación.
                    elif isinstance(saved_submitted, (int, float)):
                        if abs((saved_submitted-submitted)*86400) > 60:
                            exceptions.append(f"{key}:fecha_envio_real_cambio")
                refs = _additional_refs(row)
                current_refs = values.get("referencias_externas")
                if refs is not None:
                    if not current_refs:
                        changes.append(("02 - Actividades", number, 27, None, refs))
                    elif str(current_refs) != refs:
                        exceptions.append(f"{key}:referencias_externas_conflicto")
                continue
            if cid not in active:
                # Carga secundaria: solo actualiza notas de cursos históricos ya
                # importados, nunca incorpora obligaciones pasadas sin validación.
                continue
            name = _text(row.get("name"), "Título Canvas")
            url = _text(row.get("html_url"), "URL Canvas")
            if not url.startswith(f"https://areandina.instructure.com/courses/{cid}/"):
                raise SyncError(f"URL Canvas inesperada para {key}.")
            internal = f"ACT-C{cid}-A{aid}"
            if internal in by_id:
                raise SyncError("Colisión de identidad de nueva actividad.")
            by_id.add(internal)
            number = next((i for i in empty_rows if i not in reserved and i <= CAPACITY),None)
            if number is None and len(used) < CAPACITY:
                number = max(len(used)+1, max(reserved, default=1)+1)
            if number is None or number > CAPACITY:
                raise SyncError("No quedan filas libres para actividades.")
            reserved.add(number)
            notes = "Importada de Canvas GET-only. Fechas con UTC explícito representadas en America/Bogota; peso y nota institucional no inferidos."
            if row.get("kind_hint") == "complementaria":
                notes += " Complementaria evaluable: obligatoriedad NO confirmada; no priorizar como tarea obligatoria."
            if row.get("kind_hint") == "informativo_o_evaluable":
                notes += " Elemento informativo con entrega/calificación verificadas; no asumir nuevas obligaciones."
            raw_submission = (row.get("submission") or {}).get("submitted_at")
            if raw_submission:
                _utc(raw_submission)
                notes += f" Entrega observada UTC: {raw_submission}."
            values = (internal, subject, _type(row), name, None, None, None,
                      _excel_serial(row.get("unlock_at")), _excel_serial(row.get("due_at")),
                      _excel_serial(row.get("lock_at")), access, delivery, grade,
                      None, points, score, None, None, "Canvas", "assignment",
                      str(aid), url, "snapshot_canvas_verificado", False, notes,
                      _excel_serial(raw_submission), _additional_refs(row))
            changes.extend(("02 - Actividades", number, col, None, value)
                           for col,value in enumerate(values,1) if value is not None)
        return changes, sorted(set(exceptions))
    finally:
        if book is not None:
            book.Close(False)
        if app is not None:
            app.Quit()


def mutate_activities(path, changes):
    # Los formatos de celda (números, fechas, anchos) pertenecen a la vista XLSX generada.
    from .workbook_blocks import mutate
    mutate(path, changes)


def publish_activities(root, registry, snapshots, decisions, *, dry_run=True, api=None):
    from .workbook_blocks import publish
    current = Path(root)
    for cid, data in snapshots.items():
        filename = current / "procesamiento" / "canvas-sync" / "v01" / str(cid) / "latest.json"
        if not filename.is_file():
            raise SyncError("Faltan evidencias de la sincronización.")
        actual = json.loads(filename.read_text(encoding="utf-8"))
        if actual.get("sha256") != data.get("sha256"):
            raise SyncError("Snapshot Canvas cambió antes de publicación.")
    def planner(path, reg):
        changes, _ = plan_activities(path, reg, snapshots, decisions)
        return changes
    return publish(root, registry, dry_run=dry_run, api=api, planner=planner,
                   writer=mutate_activities)
