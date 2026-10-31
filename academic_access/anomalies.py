"""`anomalies`: inconsistencias de datos observadas, solo lectura (S7b).

Recorre la base publicada y la evidencia local de PDF de Canvas
(`procesamiento/canvas-sync/pdfs/<curso>/<archivo>/latest.json`, la forma que escribe
`canvas_sync.resources.save_pdf`) del semestre activo. Nunca corrige ni escribe nada:
solo informa. Se valida contra `workbook_version`, no contra el índice de conocimiento.
"""
import json
from pathlib import Path

from academic_knowledge.index import safe_path
from academic_store import store

from . import core
from .today import _active_subjects, _text

TYPES = ("archivo_duplicado", "texto_de_prueba", "actividad_sin_fecha")
PLACEHOLDERS = ("lorem ipsum", "texto de prueba")
PDF_SOURCE = "canvas_api_file_metadata_and_signed_read"
MAX_TEXT_BYTES = 3_000_000


def _course_ids(rows):
    """materia_id → ID de curso Canvas, igual que `semester._courses_from`, sin escribir el registro."""
    result = {}
    for row in rows:
        raw = str(row.get("curso_externo_id") or "").strip().removesuffix(".0")
        if str(row.get("lms") or "").casefold() == "canvas" and raw.isdigit() and row.get("materia_id"):
            result[str(row["materia_id"])] = raw
    return result


def _pdf_entries(base, course):
    """Metadatos válidos de los PDF descargados de un curso; entradas dañadas se omiten."""
    entries = []
    try:
        folder = safe_path(base, course)
        children = sorted(folder.iterdir()) if folder.is_dir() else []
    except (core.SyncError, OSError):
        return entries
    for child in children:
        try:
            latest = safe_path(folder, child.name, "latest.json")
            if not latest.is_file():
                continue
            meta = json.loads(latest.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                continue
            if (meta.get("source") != PDF_SOURCE or str(meta.get("course_id")) != course
                    or str(meta.get("file_id")) != child.name
                    or not isinstance(meta.get("display_name"), str)
                    or not isinstance(meta.get("sha256"), str)):
                continue
            text = None
            if meta.get("relative_text"):
                path = safe_path(folder, child.name, meta["relative_text"])
                if path.is_file() and path.stat().st_size <= MAX_TEXT_BYTES:
                    text = path.read_bytes().decode("utf-8-sig", errors="replace")
        except (core.SyncError, OSError, ValueError, TypeError):
            continue
        entries.append({"file_id": child.name, "nombre": meta["display_name"],
                        "sha256": meta["sha256"], "texto": text})
    return entries


def _duplicates(subject, entries):
    by_name = {}
    for entry in entries:
        by_name.setdefault(entry["nombre"], []).append(entry)
    items = []
    for name, group in by_name.items():
        if len({e["sha256"] for e in group}) < 2:
            continue
        ids = sorted((e["file_id"] for e in group), key=lambda x: (len(x), x))
        items.append({"tipo": "archivo_duplicado", "materia_id": subject, "ids": ids,
                      "detalle": f"Hay {len(ids)} archivos «{name}» con contenido distinto "
                                 "(SHA-256 diferente); identifíquelos por ID de Canvas, no por nombre."})
    return items


def _placeholders(subject, entries):
    items = []
    for entry in entries:
        lowered = (entry["texto"] or "").casefold()
        found = [marker for marker in PLACEHOLDERS if marker in lowered]
        if found:
            items.append({"tipo": "texto_de_prueba", "materia_id": subject, "ids": [entry["file_id"]],
                          "detalle": f"El texto extraído de «{entry['nombre']}» contiene "
                                     + ", ".join(f"«{m}»" for m in found)
                                     + "; puede ser una plantilla reutilizada."})
    return items


def _undated(rows, current):
    items = []
    for row in rows:
        aid, subject = _text(row.get("actividad_id")), _text(row.get("materia_id"))
        if aid is None or subject not in current or _text(row.get("fecha_entrega")) is not None:
            continue
        items.append({"tipo": "actividad_sin_fecha", "materia_id": subject, "ids": [aid],
                      "detalle": f"La actividad «{_text(row.get('titulo')) or aid}» no tiene fecha de entrega."})
    return items


def _key(item):
    return (TYPES.index(item["tipo"]), item["materia_id"],
            [(len(x), x) for x in item["ids"]])


def anomalies(runtime, *, subject=None):
    try:
        actual = core.workbook_version(runtime)
    except core.SyncError as exc:
        raise core.AccessError("SOURCE_UNAVAILABLE", "La base publicada no está disponible o no valida.",
                               action="Revisar la tarea diaria y la integridad de la base local.") from exc
    path = Path(runtime) / store.DB_NAME
    catalog = store.read_catalog(path, ("01 - Materias", "02 - Actividades", "05 - Semestres"))
    if subject is not None and subject not in {str(r["materia_id"]) for r in catalog["01 - Materias"]
                                               if r.get("materia_id")}:
        raise core.AccessError("NOT_FOUND", "materia_id no existe en la base.")
    # Solo el semestre activo: lo de semestres cerrados ya no se puede corregir.
    current = set(_active_subjects(catalog))
    if subject is not None:
        current &= {subject}
    courses = _course_ids(catalog["01 - Materias"])
    base = Path(runtime) / "procesamiento" / "canvas-sync" / "pdfs"
    pdfs_available = base.is_dir() and not base.is_symlink()
    items = []
    if pdfs_available:
        for mid in sorted(current):
            if mid in courses:
                entries = _pdf_entries(base, courses[mid])
                items += _duplicates(mid, entries) + _placeholders(mid, entries)
    items += _undated(catalog["02 - Actividades"], current)
    items.sort(key=_key)
    return {"materia_id": subject,
            "resumen": {kind: sum(i["tipo"] == kind for i in items) for kind in TYPES},
            "fuentes": {"recursos_pdf": "disponible" if pdfs_available else "no_disponible",
                        "actividades": "disponible"},
            "items": items, "workbook_sha256": actual}
