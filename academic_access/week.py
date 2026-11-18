"""«Mi semana»: clases por atender con su estado y entregas de la semana (solo lectura).

odd/tasks/mi-semana.md. El estado de cada clase sale de lo que ya existe:

- un trabajo de Capture sin registrar: «procesando», «con_error» o «falta_indice» (desde 0.9.1
  una clase solo entra a `03 - Clases` al completarla con su índice);
- una fila de `03 - Clases`: «lista» si tiene índice en `indices_clase`, «falta_indice» si está
  procesada sin índice, «sin_transcripcion» si se anotó desde el chat sin procesar.

«Estudiada» y las clases esperadas sin grabación llegan con otras entregas (no hay registro de
estudio ni horario todavía). Las entregas reutilizan `today`, que ya pone primero lo atrasado.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from academic_store import records, store

from . import core, today as today_reader

CLASS_WINDOW = today_reader.CLASS_WINDOW  # días en que una clase pendiente sigue en «Mi semana»
WEEK_DAYS = 7
NEXT_STEP = {
    "procesando": "Capture la está transcribiendo; siga el avance con Claude («capturas»).",
    "con_error": "Capture se detuvo. Pídale a Claude que la revise con «capturas» y la procese otra vez.",
    "falta_indice": "Pídale a Claude que la complete: lee la transcripción y crea su índice («completar_clase»).",
    "sin_transcripcion": "Pídale a Claude que la procese con el enlace de su grabación.",
    "lista": "Repásela con su índice, o estúdiela con Claude.",
    "sin_grabacion": "Todavía no aparece su grabación en Canvas ni en Calendar; se busca en cada sincronización.",
    "en_cola": "Encontrada en Canvas: Academic Manager la transcribe solo, una clase a la vez.",
    "solo_transcripcion": ("Drive no deja descargarla: ábrala, pulse el botón «Transcripción de Drive» y pídale a "
                           "Claude que importe las transcripciones descargadas."),
}
REGISTERED_WITHOUT_INDEX = "Ya está registrada: pídale a Claude que cree su índice («guardar_indice_clase»)."
PENDING = ("procesando", "con_error", "falta_indice", "sin_transcripcion", "en_cola", "solo_transcripcion",
           "sin_grabacion")
HIDDEN = Path("procesamiento") / "panel" / "clases-quitadas.json"
JOB_ID = re.compile(r"[0-9a-f]{32}")


def hidden(runtime):
    """Trabajos de Capture que la persona quitó de la lista porque no son clases."""
    path = Path(runtime) / HIDDEN
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if not path.is_symlink() else {}
    except (OSError, ValueError):
        return set()
    return {j for j in data.get("trabajos", []) if isinstance(j, str) and JOB_ID.fullmatch(j)} \
        if isinstance(data, dict) else set()


def set_hidden(runtime, job_id, hide):
    """Quita un trabajo de la lista o lo vuelve a mostrar; no toca el trabajo ni la base."""
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise core.AccessError("INVALID_ARGUMENT", "Ese trabajo no existe.")
    path = Path(runtime) / HIDDEN
    if path.is_symlink() or path.parent.is_symlink():
        raise core.AccessError("INVALID_SOURCE", "La lista de clases quitadas está redirigida.")
    current = hidden(runtime)
    current = current | {job_id} if hide else current - {job_id}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"trabajos": sorted(current)}), encoding="utf-8")
    os.replace(temporary, path)
    return sorted(current)
STALE_HOURS = 3  # sin avanzar tanto tiempo, el proceso de Capture ya no está corriendo


def _jobs(runtime):
    """job.json legibles de Capture, del más reciente al más antiguo."""
    found = []
    for path in (Path(runtime) / "procesamiento").glob("*/job.json"):
        if path.is_symlink() or path.parent.is_symlink():
            continue
        try:
            job = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(job, dict) and isinstance(job.get("request"), dict):
            found.append(job)
    return sorted(found, key=lambda j: str(j.get("created_at") or ""), reverse=True)


def _stalled(job, now):
    """Un trabajo «en curso» que no avanza hace horas murió (se cerró el PC o el proceso)."""
    try:
        stamp = str(job.get("updated_at") or job.get("created_at"))
        moment = datetime.fromisoformat(stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp)
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return now - moment > timedelta(hours=STALE_HOURS)


def _item(key, subject, names, day, title, state, *, job=None, class_id=None):
    return {"clave": key, "clase_id": class_id, "trabajo": job, "materia_id": subject,
            "materia": names.get(subject), "fecha": day, "titulo": title, "estado": state,
            "siguiente_paso": NEXT_STEP[state]}


def _found_recordings(runtime, active, names, registered, with_job):
    """Grabaciones que encontró la rutina de clases automáticas y todavía no tienen trabajo de Capture."""
    from academic_operations import auto_classes
    items = []
    for entry in auto_classes.found(runtime).values():
        subject, day = entry.get("materia_id"), entry.get("fecha")
        if (subject not in active or not isinstance(day, str) or "verificada" not in entry or entry.get("ignorada")
                or entry.get("file_id") in with_job or (subject, day) in registered):
            continue
        state = "en_cola" if entry.get("descargable") else "solo_transcripcion"
        item = _item(entry["file_id"], subject, names, day, entry.get("nombre") or entry.get("fuente"), state)
        item["enlace"] = entry.get("enlace")
        if entry.get("problema") and state == "en_cola":
            item["siguiente_paso"] = entry["problema"]
        items.append(item)
    return items


def _expected(runtime, active, names, day, known):
    """Clases del horario de Calendar de días anteriores que todavía no tienen grabación ni registro."""
    from academic_operations import calendar_classes
    items, shown = [], set(known)  # una sola fila por materia y fecha, aunque el horario la repita
    for event in calendar_classes.events(runtime):
        subject, when = event.get("materia_id"), event.get("fecha")
        if subject not in active or not isinstance(when, str) or when >= day.isoformat() or (subject, when) in shown:
            continue
        shown.add((subject, when))
        item = _item(f"agenda:{subject}:{when}", subject, names, when, event.get("titulo"), "sin_grabacion")
        if event.get("inicio"):
            item["siguiente_paso"] = f"Clase de las {event['inicio']}. " + item["siguiente_paso"]
        items.append(item)
    return items


def classes(runtime, *, on=None, now=None):
    """Todas las clases del semestre activo, con su estado, de la más reciente a la más antigua."""
    runtime = Path(runtime)
    day = today_reader.parse_date(on) if on is not None else today_reader.local_today()
    path = runtime / store.DB_NAME
    catalog = store.read_catalog(path, ("01 - Materias", "03 - Clases", "05 - Semestres"))
    active = set(today_reader._active_subjects(catalog))
    names = {str(r["materia_id"]): today_reader._text(r.get("nombre")) for r in catalog["01 - Materias"]
             if str(r.get("materia_id")) in active}
    by_name = {(name or "").strip().casefold(): ident for ident, name in names.items()}
    indexed = {r["clase_id"] for r in records.read(path, "indices_clase")}
    items, registered = [], set()
    for row in catalog["03 - Clases"]:
        subject, cid = today_reader._text(row.get("materia_id")), today_reader._text(row.get("clase_id"))
        when = today_reader._local_date(row.get("fecha"))
        if subject not in active or cid is None or when is None:
            continue
        registered.add((subject, when.isoformat()))
        if when > day:
            continue
        state = "lista" if cid in indexed else ("falta_indice" if row.get("procesada") else "sin_transcripcion")
        item = _item(cid, subject, names, when.isoformat(), today_reader._text(row.get("titulo")), state,
                     class_id=cid)
        if state == "falta_indice":
            item["siguiente_paso"] = REGISTERED_WITHOUT_INDEX
        items.append(item)
    seen, removed, quitadas = set(), hidden(runtime), []
    for job in _jobs(runtime):
        request = job["request"]
        subject = by_name.get(str(request.get("subject") or "").strip().casefold())
        when = request.get("class_date")
        if (subject is None or not isinstance(when, str) or job.get("database_registration")
                or request.get("duration") is not None or request.get("tipo") == "material"
                or (subject, when) in registered or (subject, when) in seen):
            continue
        seen.add((subject, when))  # el trabajo más reciente de esa clase es el que cuenta
        status = job.get("status")
        state = ("falta_indice" if status == "completed" else "con_error" if status == "failed"
                 or _stalled(job, now or datetime.now(timezone.utc)) else "procesando")
        title = (job.get("source_metadata") or {}).get("original_name")
        item = _item(job.get("job_id"), subject, names, when, title, state, job=job.get("job_id"))
        (quitadas if job.get("job_id") in removed else items).append(item)
    items += _found_recordings(runtime, active, names, registered, {
        (j.get("source_metadata") or {}).get("file_id") for j in _jobs(runtime)})
    items += _expected(runtime, active, names, day, {(i["materia_id"], i["fecha"]) for i in items} | registered)
    for found in (items, quitadas):
        found.sort(key=lambda i: (i["fecha"], i["clave"] or ""), reverse=True)
    return {"fecha": day.isoformat(), "clases": items, "quitadas": quitadas}


def week(runtime, *, on=None):
    """Lo de la semana: clases por atender (pendientes y listas de los últimos 7 días) y entregas."""
    runtime = Path(runtime)
    agenda = today_reader.today(runtime, on=on)
    day = today_reader.parse_date(agenda["fecha"])
    until = day + timedelta(days=WEEK_DAYS - 1)
    recent = (day - timedelta(days=CLASS_WINDOW)).isoformat()
    week_start = (day - timedelta(days=WEEK_DAYS - 1)).isoformat()
    attend = []
    for item in classes(runtime, on=agenda["fecha"])["clases"]:
        from_job = item["trabajo"] is not None
        if item["estado"] == "lista":
            if item["fecha"] >= week_start:
                attend.append(item)
        elif item["estado"] == "sin_grabacion":
            if item["fecha"] >= week_start:
                attend.append(item)
        elif from_job or item["fecha"] >= recent:
            attend.append(item)
    deliveries, others = [], []
    for item in agenda["items"]:
        if item["tipo"] == "clase_sin_procesar":
            continue  # está en las clases, con su estado
        due = item["tipo"] == "actividad_vencida" or (
            item["tipo"] == "actividad_proxima" and item["fecha"] and item["fecha"] <= until.isoformat())
        (deliveries if due else others).append(item)
    return {"fecha": agenda["fecha"], "hasta": until.isoformat(), "fechas_zona": agenda["fechas_zona"],
            "clases": attend, "entregas": deliveries, "otros": others,
            "resumen": {"clases_por_atender": sum(i["estado"] in PENDING for i in attend),
                        "clases_listas": sum(i["estado"] == "lista" for i in attend),
                        "entregas": len(deliveries),
                        "atrasadas": sum(i["tipo"] == "actividad_vencida" for i in deliveries)},
            "workbook_sha256": agenda["workbook_sha256"]}
