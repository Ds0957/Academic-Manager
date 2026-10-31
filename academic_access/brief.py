"""`subject-brief`: resumen de una sola materia para empezar el chat de su Project (solo lectura).

Reúne lo que el Project de materia necesita al empezar: lo pendiente de `today` para esa
materia, estados de entrega, tareas abiertas, decisiones vigentes y resúmenes de clase
recientes. Lee la base publicada y se valida contra `workbook_version`, no contra el índice.
"""
from pathlib import Path

from academic_store import records, store

from . import core, summary, today as today_module

# Una sola materia: el horizonte máximo de `today` no satura y muestra lo que viene.
DAYS = today_module.MAX_DAYS
RECENT_SUMMARIES = 5


def subject_brief(runtime, subject, *, on=None):
    day = today_module.parse_date(on) if on is not None else today_module.local_today()
    # Mismo control que `notes`: base publicada y materia existente, o error.
    path, actual, notes = core._records(runtime, "notas", subject, None, (), "tipo")
    catalog = store.read_catalog(path, ("01 - Materias", "03 - Clases", "05 - Semestres"))
    active = today_module._active_subjects(catalog)
    row = next(r for r in catalog["01 - Materias"] if str(r.get("materia_id")) == subject)
    meta = summary._subjects(catalog, [subject], day)[0]
    urgent = today_module.today(Path(runtime), on=day.isoformat(), days=DAYS)
    pending = [i for i in urgent["items"] if i["materia_id"] == subject]
    return {"fecha": day.isoformat(), "fechas_zona": "America/Bogota", "dias": DAYS,
            "materia": {"materia_id": subject, "nombre": meta["nombre"],
                        "semestre_id": today_module._text(row.get("semestre")),
                        "semestre_activo": subject in active,
                        "ultima_clase": meta["ultima_clase"]},
            "pendientes": {"items": pending,
                           "resumen": {kind: sum(i["tipo"] == kind for i in pending)
                                       for kind in today_module.TYPES}},
            "entregas": core.tracking_items(path, records.read(path, "seguimiento"), subject=subject),
            "tareas": core.task_rows(records.read(path, "tareas"), subject=subject,
                                     states=today_module.OPEN_TASKS),
            "decisiones": core.note_rows(notes, subject=subject, kind="decision"),
            "resumenes_clase": core.note_rows(notes, subject=subject,
                                              kind="resumen_clase")[:RECENT_SUMMARIES],
            "workbook_sha256": actual}
