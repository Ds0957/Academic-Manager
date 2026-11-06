"""Puerta de lectura para el futuro Coordinador y Projects de materia.

API JSON versionada. Sin operaciones de escritura ni acciones sobre Canvas/Drive.
"""
from datetime import date, datetime, timedelta
from pathlib import Path
import hashlib
import json
import re

from academic_knowledge import index as knowledge
from academic_store import records, store
from capture.academic_manager.database import _date_text
from capture.storage.monitor import list_jobs, job_summary
from canvas_sync.common import SyncError
from canvas_sync.routine import workbook_version

SCHEMA_VERSION = 1
MAX_ITEMS = 100
MAX_EXCERPT = 6000
DATE_FIELDS = {"fecha_apertura", "fecha_entrega", "fecha_cierre", "fecha_envio_real",
               "fecha", "fecha_inicio", "fecha_fin"}


class AccessError(Exception):
    def __init__(self, code, message, *, action=None):
        super().__init__(message)
        self.code, self.action = code, action


def _required_index(runtime, *, allow_stale=False):
    try:
        manifest, _ = knowledge.load(runtime)
        actual = workbook_version(runtime)
    except (SyncError, FileNotFoundError, KeyError, ValueError) as exc:
        raise AccessError("SOURCE_UNAVAILABLE",
                          "El índice local o la base publicada no están disponibles o no validan.",
                          action="Revisar la tarea diaria e integridad del índice.") from exc
    expected = manifest["data"].get("workbook_sha256")
    fresh = actual == expected
    if not fresh and not allow_stale:
        raise AccessError("INDEX_STALE",
                          "El índice académico no corresponde a la versión actual del Excel.",
                          action="Ejecutar scripts\\knowledge.cmd build y repetir la consulta.")
    return manifest, {"index_sha256": manifest["sha256"],
                      "workbook_sha256": actual,
                      "index_workbook_sha256": expected,
                      "fresh": fresh,
                      "generated_at_utc": manifest.get("generado_utc")}


def _read_excel(runtime, sheet_names):
    catalog = knowledge.excel_catalog(runtime)
    return {sheet: catalog[sheet] for sheet in sheet_names}


def _date_value(value):
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not 1 <= value <= 2958465:
            raise AccessError("INVALID_SOURCE", "Fecha en Excel fuera de intervalo válido.")
        return (datetime(1899, 12, 30) + timedelta(days=value)).isoformat(timespec="seconds")
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)


def _json_field(key, value):
    if value is None or value == "":
        return None
    if key in DATE_FIELDS:
        return _date_value(value)
    if isinstance(value, float) and value.is_integer() and key not in ("puntos_obtenidos", "puntos_posibles", "peso_porcentual", "nota_institucional"):
        return int(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _pick(row, fields):
    return {key: _json_field(key, row.get(key)) for key in fields}


ACTIVITY_FIELDS = (
    "actividad_id", "materia_id", "tipo", "titulo", "descripcion",
    "organizacion", "modalidad", "fecha_apertura", "fecha_entrega", "fecha_cierre",
    "estado_acceso", "estado_entrega", "estado_calificacion", "prioridad",
    "puntos_posibles", "puntos_obtenidos", "peso_porcentual", "nota_institucional",
    "lms", "objeto_externo_tipo", "objeto_externo_id", "url_fuente",
    "verificacion", "notas", "fecha_envio_real", "referencias_externas"
)
TRANSCRIPT_LINE = re.compile(r"\[(\d{2}:\d{2}:\d{2})[–-](\d{2}:\d{2}:\d{2})\]\s*(.*)")
CLASS_FIELDS = ("clase_id", "materia_id", "fecha", "titulo", "tema_principal",
                "estado_sesion", "estado_grabacion", "transcripcion", "analisis",
                "fuente", "verificacion", "notas")


def _paginate(items, *, limit=50, offset=0):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_ITEMS:
        raise AccessError("INVALID_ARGUMENT", f"limit debe estar entre 1 y {MAX_ITEMS}.")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise AccessError("INVALID_ARGUMENT", "offset no puede ser negativo.")
    return {"total": len(items), "offset": offset,
            "devueltos": len(items[offset:offset+limit]),
            "mas_resultados": offset+limit < len(items),
            "items": items[offset:offset+limit]}


def _ensure_subject(manifest, subject):
    if subject not in manifest["data"]["materias"]:
        raise AccessError("NOT_FOUND", "materia_id no existe en la base de conocimiento.")


def _catalog_subjects(runtime, manifest):
    """Semestre y bloques desde Excel canónico, sin bootstrap ni escritura local."""
    catalog = _read_excel(runtime, ("01 - Materias", "05 - Semestres"))
    active = [r for r in catalog["05 - Semestres"]
              if str(r.get("estado") or "").strip().casefold() == "activo"]
    if len(active) != 1:
        raise AccessError("INVALID_SOURCE", "El Excel debe tener un único semestre activo.")
    active_semester = str(active[0]["semestre_id"])
    raw_block = active[0].get("bloque_activo")
    if isinstance(raw_block, bool) or str(raw_block).strip() not in ("1", "1.0", "2", "2.0"):
        raise AccessError("INVALID_SOURCE", "Bloque activo no confirmado en Excel.")
    active_block = int(float(raw_block))
    by_id = {str(x["materia_id"]): x for x in catalog["01 - Materias"]
             if x.get("materia_id")}
    if len(by_id) != len(catalog["01 - Materias"]):
        raise AccessError("INVALID_SOURCE", "El Excel tiene IDs duplicados de materia.")
    result = []
    for mid, meta in manifest["data"]["materias"].items():
        course = by_id.get(mid)
        if course is None or str(course.get("semestre")) != meta["semestre"]:
            raise AccessError("INVALID_SOURCE", "La materia del índice no coincide con el Excel.")
        raw = str(course.get("bloques") or "").replace(" ", "")
        if raw not in ("1", "2", "1,2", ""):
            raise AccessError("INVALID_SOURCE", "Bloques de materia inválidos en el Excel.")
        blocks = [int(x) for x in raw.split(",")] if raw else None
        result.append({"materia_id": mid, "nombre": meta["nombre"],
                       "semestre_id": meta["semestre"],
                       "curso_canvas_id": meta.get("canvas_id"),
                       "bloques": blocks,
                       "semestre_activo": meta["semestre"] == active_semester,
                       "pertenece_bloque_activo": (meta["semestre"] == active_semester
                                                   and blocks is not None
                                                   and active_block in blocks)})
    return {"active_semester": active_semester, "active_block": active_block}, result


def capabilities():
    return {"schema_version": SCHEMA_VERSION, "access": "read_only",
            "provider": "local_windows_python_com_sqlite",
            "requires_chatgpt": False, "commands": {
              "health": "Salud local de índices y copia del workbook (sin reparar ni publicar)",
              "subjects": "Semestres y pertenencia a bloques",
              "subject": "Detalle de una materia y cobertura de fuentes",
              "activities": "Actividades verificadas y campos originales de Excel por materia",
              "activity": "Actividad exacta y relaciones contextuales",
              "classes": "Clases registradas y rutas de análisis/transcripciones",
              "search": "Fragmentos literales recuperados de materiales por materia",
              "source": "Lectura parcial de un texto verificado por SHA-256",
              "capture-jobs": "Estado observado de procesamiento de grabaciones y progreso ASR por job",
              "notes": "Notas, decisiones, aclaraciones y resúmenes de clase guardados desde el chat",
              "tasks": "Tareas personales registradas desde el chat",
              "tracking": "Seguimiento de entregas (estado, nota y puntos) registrado desde el chat",
              "today": "Lista ordenada de qué hacer primero: entregas vencidas y próximas, tareas, clases sin procesar y materias sin movimiento",
              "summary": "Resumen corto para empezar un chat: semestre y materias activas, lo urgente de today, notas recientes, tareas abiertas y estado del sistema",
              "subject-brief": "Resumen de una sola materia para empezar el chat de su Project: pendientes de today, estados de entrega, tareas abiertas, decisiones vigentes y resúmenes de clase recientes",
              "anomalies": "Inconsistencias de datos del semestre activo (archivos con el mismo nombre y distinto contenido, texto de plantilla en recursos, actividades sin fecha); solo informa, nunca corrige",
              "system": "Comprobación en vivo del sistema real: base local, Drive (identidad, permisos, lock, versión), Canvas Sync, token, índice y tarea diaria; solo lectura"},
            "notes": ["La salida refleja la última versión local validada, no una consulta remota en vivo.",
                      "El estado pendiente/calificado es observado, no una recomendación de prioridad.",
                      "El índice fresco significa que coincide con el SHA local del Excel; los textos se comprueban al consultarlos.",
                      "Las materias compartidas entre dos bloques se representan una sola vez.",
                      "Las fuentes sin texto no se inventan; no se eluden permisos institucionales."]}


def _sync_last_run(runtime):
    log = Path(runtime) / "procesamiento" / "canvas-sync" / "logs" / "latest-status.json"
    if not log.is_file() or log.is_symlink():
        return {"status": "unknown", "reason": "No hay log de tarea diaria verificable."}
    try:
        state = json.loads(log.read_text(encoding="utf-8-sig"))
        code = state.get("exit_code")
        if isinstance(code, bool) or type(code) not in (int, float):
            raise ValueError("Código de salida inválido")
        return {"status": "ok" if code == 0 else "error",
                "timestamp_local": state.get("timestamp"),
                "exit_code": int(code)}
    except (OSError, ValueError, TypeError, KeyError):
        return {"status": "unknown", "reason": "Log local no legible o incompatible."}


SYNC_LOG_DAYS = 30
_SYNC_ENTRY = re.compile(r"^﻿?\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] exit_code=(-?\d+)", re.M)
LOCAL_PATH = re.compile(r"(?<![\w/])(?:[A-Za-z]:[\\/]|/)[^\s'\"«»]+")


def sync_history(runtime):
    """Racha de la tarea diaria desde sus logs por día (`canvas-YYYY-MM-DD.log`), solo lectura.

    `fallos_seguidos` cuenta las ejecuciones fallidas desde el último éxito; None sin logs.
    """
    folder = Path(runtime) / "procesamiento" / "canvas-sync" / "logs"
    empty = {"ultimo_ok": None, "fallos_seguidos": None, "fallando_desde": None,
             "ultimo_intento": None, "ultimo_error": None}
    try:
        files = sorted(p for p in folder.glob("canvas-*.log") if p.is_file() and not p.is_symlink())
        runs = []
        for path in files[-SYNC_LOG_DAYS:]:
            runs += [(stamp, int(code)) for stamp, code in
                     _SYNC_ENTRY.findall(path.read_text(encoding="utf-8-sig", errors="replace"))]
    except OSError:
        return empty
    if not runs:
        return empty
    runs.sort()
    failed = 0
    for _, code in reversed(runs):
        if code == 0:
            break
        failed += 1
    ok = [stamp for stamp, code in runs if code == 0]
    error = None
    if failed:
        try:
            message = json.loads((folder / "latest-status.json").read_text(encoding="utf-8-sig")).get("message")
            lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
            error = LOCAL_PATH.sub("<ruta>", lines[-1])[:200] if lines else None
        except (OSError, ValueError, AttributeError):
            error = None
    return {"ultimo_ok": ok[-1] if ok else None, "fallos_seguidos": failed,
            "fallando_desde": runs[-failed][0] if failed else None,
            "ultimo_intento": runs[-1][0], "ultimo_error": error}


def sync_warning(history, day):
    """Aviso para el chat si Canvas Sync lleva un día fallando o sin ejecutarse; si no, None."""
    if history["ultimo_intento"] is None:
        return None
    last = date.fromisoformat(history["ultimo_intento"][:10])
    if (day - last).days >= 2:
        return (f"Canvas Sync no se ejecuta desde {last.isoformat()}: revisar la tarea diaria "
                "AcademicManager-CanvasDaily en el PC.")
    if history["fallos_seguidos"] and history["fallos_seguidos"] >= 2:
        since = history["fallando_desde"][:10]
        ok = history["ultimo_ok"][:10] if history["ultimo_ok"] else "sin registro"
        return (f"Canvas Sync falla desde {since} ({history['fallos_seguidos']} intentos seguidos; "
                f"último éxito: {ok}). Último error: {history['ultimo_error'] or 'desconocido'}")
    return None


def capture_jobs(runtime, *, identity=None, limit=15):
    if identity:
        return {"trabajos": [job_summary(runtime, identity)], "tipo": "captura_estado_observado"}
    return {"trabajos": list_jobs(runtime, limit=limit), "tipo": "captura_estado_observado",
            "nota": "La estimación solo cubre la fase indicada y es orientativa; no incluye análisis ni publicación."}


def health(runtime):
    manifest, freshness = _required_index(runtime, allow_stale=True)
    source_counts = knowledge.overview(runtime)
    registry, _ = _catalog_subjects(runtime, manifest)
    return {"status": "ok" if freshness["fresh"] else "stale", "index": freshness,
            "active_semester": registry["active_semester"],
            "active_block": registry["active_block"],
            "counts": {
                "materias": len(source_counts),
                "actividades": len(manifest["data"]["actividades"]),
                "fuentes": len(manifest["data"]["fuentes"]),
                "con_texto": sum(x["estado"] == "texto" for x in manifest["data"]["fuentes"].values()),
                "relaciones": len(manifest["data"]["relaciones"])},
            "ultimo_canvas_sync": _sync_last_run(runtime),
            "warnings": (["Reconstruir índice para consultas actualizadas."] if not freshness["fresh"] else [])}


def subjects(runtime):
    manifest, freshness = _required_index(runtime)
    registry, result = _catalog_subjects(runtime, manifest)
    return {"active_semester": registry["active_semester"],
            "active_block": registry["active_block"],
            "materias": result, "index": freshness}


def subject_detail(runtime, subject):
    manifest, freshness = _required_index(runtime)
    _ensure_subject(manifest, subject)
    _, entries = _catalog_subjects(runtime, manifest)
    meta = next(item for item in entries if item["materia_id"] == subject)
    coverage = knowledge.overview(runtime, subject)[subject]
    return {"materia": meta, "cobertura": coverage,
            "advertencias": (
                ["Sin texto de estudio autorizado disponible en el índice."]
                if not coverage["con_texto"] else []),
            "index": freshness}


def activities(runtime, *, subject, limit=50, offset=0):
    manifest, freshness = _required_index(runtime)
    _ensure_subject(manifest, subject)
    rows = _read_excel(runtime, ("02 - Actividades",))["02 - Actividades"]
    filtered = [_pick(r, ACTIVITY_FIELDS) for r in rows if str(r.get("materia_id")) == subject]
    filtered.sort(key=lambda x: x["actividad_id"])
    return {"materia_id": subject, "fechas_zona": "America/Bogota (hora Excel)",
            "obligatoriedad_complementarias": "no_verificada",
            "resultado": _paginate(filtered, limit=limit, offset=offset),
            "index": freshness}


def classes(runtime, *, subject, limit=50, offset=0):
    manifest, freshness = _required_index(runtime)
    _ensure_subject(manifest, subject)
    rows = _read_excel(runtime, ("03 - Clases",))["03 - Clases"]
    filtered = [_pick(r, CLASS_FIELDS) for r in rows if str(r.get("materia_id")) == subject]
    filtered.sort(key=lambda x: (str(x["fecha"]), str(x["clase_id"])))
    return {"materia_id": subject, "resultado": _paginate(filtered, limit=limit, offset=offset),
            "index": freshness}


def activity(runtime, activity_id, *, subject=None):
    manifest, freshness = _required_index(runtime)
    rows = _read_excel(runtime, ("02 - Actividades",))["02 - Actividades"]
    found = [r for r in rows if str(r.get("actividad_id") or "") == activity_id]
    if len(found) != 1:
        raise AccessError("NOT_FOUND" if not found else "INVALID_SOURCE",
                          "Actividad inexistente o identificador duplicado.")
    value = _pick(found[0], ACTIVITY_FIELDS)
    if subject is not None and value["materia_id"] != subject:
        raise AccessError("NOT_FOUND", "Actividad no pertenece a la materia solicitada.")
    context = knowledge.activity_context(runtime, activity_id)
    return {"actividad": value, "relaciones": context["fuentes_del_eje"],
            "analisis_de_clases_de_la_materia": context["analisis_de_clases_de_la_materia"],
            "limitacion_relaciones": context["aviso"],
            "fechas_zona": "America/Bogota (hora Excel)",
            "index": freshness}


def search(runtime, query, *, subject, limit=8):
    manifest, freshness = _required_index(runtime)
    _ensure_subject(manifest, subject)
    if type(limit) is not int or not 1 <= limit <= 30:
        raise AccessError("INVALID_ARGUMENT", "limit entre 1 y 30.")
    matches = knowledge.search(runtime, query, subject=subject, limit=limit)
    for found in matches:
        source = manifest["data"]["fuentes"][found["fuente_id"]]
        source_path = Path(source["ruta_texto"]) if source["ruta_texto"] else None
        if not source_path or not source_path.is_file() or _hash_file(source_path) != source["sha256_texto"]:
            raise AccessError("SOURCE_CHANGED", "Un texto fue modificado después de crear el índice.",
                              action="Reconstruir el índice; revisar la fuente si su hash no valida.")
    return {"materia_id": subject, "consulta": query,
            "resultados": matches, "index": freshness}


def _hash_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for blob in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blob)
    return h.hexdigest()


def source(runtime, source_id, *, subject=None, offset=0, length=3000):
    manifest, freshness = _required_index(runtime)
    obj = _source_entry(manifest, source_id, subject)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise AccessError("INVALID_ARGUMENT", "offset debe ser entero no negativo.")
    if not isinstance(length, int) or isinstance(length, bool) or not 1 <= length <= MAX_EXCERPT:
        raise AccessError("INVALID_ARGUMENT", "length fuera de límite.")
    filepath, contents = _verified_text(runtime, obj)
    return {"fuente_id": source_id, "materia_id": obj["materia_id"],
            "titulo": obj["titulo"], "tipo": obj["tipo"], "eje": obj["eje"],
            "sha256_texto": obj["sha256_texto"],
            "origen": obj["origen"], "ruta_texto": str(filepath),
            "total_caracteres": len(contents), "offset": offset,
            "devueltos": len(contents[offset:offset+length]),
            "hay_mas": offset+length < len(contents),
            "fragmento": contents[offset:offset+length],
            "index": freshness}


def source_text(runtime, source_id, *, subject):
    """Texto completo y verificado de una fuente de la materia (citas del mapa, S3)."""
    manifest, _ = _required_index(runtime)
    return _verified_text(runtime, _source_entry(manifest, source_id, subject))[1]


def transcript_segments(text):
    """Fragmentos de un `transcript.md`: «[HH:MM:SS–HH:MM:SS] texto»; sin tiempos no hay ninguno."""
    fragments = []
    for line in text.splitlines():
        found = TRANSCRIPT_LINE.match(line.strip())
        if found:
            fragments.append({"inicio": found[1], "fin": found[2], "texto": found[3]})
        elif fragments and line.strip():
            fragments[-1]["texto"] = f'{fragments[-1]["texto"]} {line.strip()}'
    return fragments


def _source_entry(manifest, source_id, subject):
    obj = manifest["data"]["fuentes"].get(source_id)
    if obj is None or (subject is not None and obj.get("materia_id") != subject):
        raise AccessError("NOT_FOUND", "Fuente no registrada para esta materia.")
    return obj


def _verified_text(runtime, obj):
    if obj.get("estado") != "texto" or not obj.get("ruta_texto"):
        raise AccessError("NO_TEXT", "El recurso está catalogado pero no dispone de texto autorizado.",
                          action="Consultar metadatos de la fuente; no inventar su contenido.")
    filepath = Path(obj["ruta_texto"])
    # Sólo se leen fuentes dentro de raíz autorizada de Drive o runtime local.
    allowed = (Path(runtime).resolve(), knowledge.DRIVE_ROOT.resolve())
    absolute = filepath.resolve()
    if filepath.is_symlink() or not any(absolute.is_relative_to(base) for base in allowed):
        raise AccessError("INVALID_SOURCE", "La ruta no pertenece a una fuente autorizada.")
    if not filepath.is_file() or _hash_file(filepath) != obj["sha256_texto"]:
        raise AccessError("SOURCE_CHANGED", "El archivo falta o su SHA ya no coincide.",
                          action="Actualizar fuentes y reconstruir índice.")
    return filepath, filepath.read_bytes().decode("utf-8-sig")


NOTE_TYPES = records.NOTE_TYPES
TASK_STATES = records.TASK_STATES
TRACK_STATES = records.TRACK_STATES


def _records(runtime, table, subject, value, options, name):
    """Tablas propias de la base publicada; no dependen del índice de conocimiento."""
    if value is not None and value not in options:
        raise AccessError("INVALID_ARGUMENT", f"{name} debe ser uno de: {', '.join(options)}.")
    try:
        actual = workbook_version(runtime)
    except SyncError as exc:
        raise AccessError("SOURCE_UNAVAILABLE", "La base publicada no está disponible o no valida.",
                          action="Revisar la tarea diaria y la integridad de la base local.") from exc
    path = Path(runtime) / store.DB_NAME
    if subject is not None and subject not in {
            str(r["materia_id"]) for r in store.read_catalog(path, ("01 - Materias",))["01 - Materias"]}:
        raise AccessError("NOT_FOUND", "materia_id no existe en la base.")
    return path, actual, records.read(path, table)


def note_rows(rows, *, subject=None, kind=None):
    """Notas filtradas, de la más reciente a la más antigua (`notes`, `subject-brief`)."""
    rows = [r for r in rows if (subject is None or r["materia_id"] == subject)
            and (kind is None or r["tipo"] == kind)]
    rows.sort(key=lambda r: r["nota_id"])
    rows.sort(key=lambda r: r["creado_utc"], reverse=True)
    return rows


def task_rows(rows, *, subject=None, states=None):
    """Tareas filtradas, por fecha límite (sin fecha al final) y luego por id."""
    rows = [r for r in rows if (subject is None or r["materia_id"] == subject)
            and (states is None or r["estado"] in states)]
    rows.sort(key=lambda r: (r["fecha_limite"] is None, r["fecha_limite"] or "", r["tarea_id"]))
    return rows


def tracking_items(path, rows, *, subject=None, state=None):
    """Seguimiento con `materia_id` y `titulo` de su actividad."""
    known = {str(a["actividad_id"]): a for a in
             store.read_catalog(path, ("02 - Actividades",))["02 - Actividades"] if a.get("actividad_id")}
    items = []
    for row in rows:
        activity = known.get(row["actividad_id"], {})
        item = {**row, "materia_id": activity.get("materia_id"), "titulo": activity.get("titulo")}
        if (subject is None or item["materia_id"] == subject) and (state is None or row["estado"] == state):
            items.append(item)
    return items


def notes(runtime, *, subject=None, kind=None, limit=50, offset=0):
    _, actual, rows = _records(runtime, "notas", subject, kind, NOTE_TYPES, "tipo")
    rows = note_rows(rows, subject=subject, kind=kind)
    return {"materia_id": subject, "resultado": _paginate(rows, limit=limit, offset=offset),
            "workbook_sha256": actual}


def tasks(runtime, *, subject=None, state=None, limit=50, offset=0):
    _, actual, rows = _records(runtime, "tareas", subject, state, TASK_STATES, "estado")
    rows = task_rows(rows, subject=subject, states=None if state is None else (state,))
    return {"materia_id": subject, "resultado": _paginate(rows, limit=limit, offset=offset),
            "workbook_sha256": actual}


def tracking(runtime, *, subject=None, state=None, limit=50, offset=0):
    path, actual, rows = _records(runtime, "seguimiento", subject, state, TRACK_STATES, "estado")
    items = tracking_items(path, rows, subject=subject, state=state)
    return {"materia_id": subject, "resultado": _paginate(items, limit=limit, offset=offset),
            "workbook_sha256": actual}


def subject_map(runtime, subject):
    """Mapa guardado de la materia (tabla `mapas`); `mapa` es None si aún no hay uno."""
    _, actual, rows = _records(runtime, "mapas", subject, None, (), "")
    row = next((r for r in rows if r["materia_id"] == subject), None)
    if row is None:
        return {"materia_id": subject, "mapa": None, "actualizado_utc": None, "workbook_sha256": actual}
    try:
        content = json.loads(row["contenido"])
    except ValueError as exc:
        raise AccessError("INVALID_SOURCE", "El mapa guardado de la materia no es válido.") from exc
    return {"materia_id": subject, "mapa": content, "actualizado_utc": row["actualizado_utc"],
            "workbook_sha256": actual}


def _class_row(path, class_id):
    rows = store.read_catalog(path, ("03 - Clases",))["03 - Clases"]
    row = next((r for r in rows if str(r.get("clase_id")) == class_id), None)
    if row is None:
        raise AccessError("NOT_FOUND", "Esa clase no está registrada.")
    return row


def _index_content(row):
    try:
        return json.loads(row["contenido"])
    except ValueError as exc:
        raise AccessError("INVALID_SOURCE", "El índice guardado de la clase no es válido.") from exc


def class_index(runtime, class_id):
    """Índice guardado de la clase (tabla `indices_clase`); `indice` es None si aún no hay uno."""
    path, actual, rows = _records(runtime, "indices_clase", None, None, (), "")
    row = _class_row(path, class_id)
    saved = next((r for r in rows if r["clase_id"] == class_id), None)
    return {"clase_id": class_id, "materia_id": str(row["materia_id"]), "titulo": row.get("titulo"),
            "fecha": _date_text(row.get("fecha")), "indice": _index_content(saved) if saved else None,
            "actualizado_utc": saved["actualizado_utc"] if saved else None, "workbook_sha256": actual}


def class_indexes(runtime, subject):
    """Clases de la materia que ya tienen índice, de la más reciente a la más antigua."""
    path, _, rows = _records(runtime, "indices_clase", subject, None, (), "")
    found = []
    for row in rows:
        if row["materia_id"] != subject:
            continue
        content, details = _index_content(row), _class_row(path, row["clase_id"])
        found.append({"clase_id": row["clase_id"], "titulo": details.get("titulo"),
                      "fecha": _date_text(details.get("fecha")), "temas": len(content["temas"]),
                      "momentos": sum(len(t["momentos"]) for t in content["temas"])})
    return sorted(found, key=lambda c: (c["fecha"], c["clase_id"]), reverse=True)


def class_transcript(runtime, class_id, subject):
    """Fragmentos con tiempo de la transcripción de la clase, para abrirla en un minuto."""
    return transcript_segments(source_text(runtime, f"clase:{class_id}:transcripcion", subject=subject))
