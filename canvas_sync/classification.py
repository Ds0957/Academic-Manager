"""Clasificación persistente de assignments de Canvas por evidencia.

Las reglas automáticas son intencionalmente conservadoras: nunca atribuyen
obligatoriedad a complementarias o contenidos mixtos.
"""
import json
from pathlib import Path
from .common import SyncError, safe_dir, atomic_json
from .reconcile import verify_snapshot

SCHEMA = 1
ACTIVITY = "actividad"
REVIEW = "revisar"
REFERENCE = "referencia"
EXCLUDED = "resumen_calificaciones"
MANUAL = {ACTIVITY, REFERENCE, EXCLUDED}


def decisions_path(root):
    return safe_dir(Path(root), "control", "canvas-sync") / "assignment-decisions.json"


def load_decisions(root):
    path = decisions_path(root)
    if path.is_symlink():
        raise SyncError("Archivo de decisiones redirigido.")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "schema_version": SCHEMA, "decisions": {}
    }
    if data.get("schema_version") != SCHEMA or not isinstance(data.get("decisions"), dict):
        raise SyncError("Registro de decisiones incompatibles.")
    for key, item in data["decisions"].items():
        if (not isinstance(item, dict) or item.get("category") not in MANUAL
                or item.get("origin") not in ("manual", "regla_confirmada")):
            raise SyncError(f"Decisión de Canvas inválida: {key}.")
    return data


def inferred_category(row):
    name = str(row.get("name") or "").strip()
    normalized = name.casefold()
    kind = str(row.get("kind_hint") or "")
    if kind == "nota_agregada" and normalized.startswith("nota final"):
        return EXCLUDED, "Canvas agrupa calificaciones; no es entrega individual."
    if kind == "informativo" and ("class recordings" in normalized or "tutor & class link" in normalized):
        return REFERENCE, "Enlace/material informativo, no entrega evaluativa."
    if kind == "complementaria":
        # Es un assignment evaluable real; NO significa obligación ordinaria.
        kinds = row.get("submission_types") or []
        points = row.get("points_possible")
        if (row.get("published") is True and row.get("grading_type") == "points"
                and type(points) in (int, float) and points > 0
                and any(t in ("online_upload", "online_text_entry", "online_url",
                              "external_tool", "online_quiz") for t in kinds)):
            return ACTIVITY, "Actividad evaluable complementaria; obligatoriedad no confirmada."
        return REVIEW, "Complementaria sin evidencia suficiente de evaluación."
    if kind == "informativo_o_evaluable":
        sub = row.get("submission") or {}
        score = sub.get("score")
        if (row.get("published") is True and sub.get("workflow_state") == "graded"
                and type(score) in (int, float) and score >= 0):
            return ACTIVITY, "Elemento informativo con entrega y calificación real."
        return REVIEW, "Finalidad mixta sin calificación comprobada."
    # Actividades explicitas, sin asumir detalles no verificados.
    approved_prefixes = ("actividad evaluativa", "homework activity", "oral exam",
                         "first term exam", "second term exam", "final exam")
    if name and normalized.startswith(approved_prefixes) and row.get("published") is True:
        return ACTIVITY, "Título explícitamente evaluativo y publicado en Canvas."
    return REVIEW, "Sin prueba suficiente de que sea actividad curricular."


def classify(snapshots, stored):
    decisions = stored["decisions"]
    outcome = {}
    counts = {ACTIVITY: 0, REVIEW: 0, REFERENCE: 0, EXCLUDED: 0}
    for cid, raw in sorted(snapshots.items()):
        for row in verify_snapshot(raw, cid):
            key = f"{cid}:{row['id']}"
            manual = decisions.get(key)
            inferred, reason = inferred_category(row)
            if manual:
                category = manual["category"]
                # cambios en título/tipo no anulan una decisión humana, pero
                # señalan revisión de evidencia si cambia la finalidad.
                if manual.get("name") and manual["name"] != row.get("name"):
                    category = REVIEW
                    reason = "Nombre Canvas cambió desde aprobación; revisar."
                else:
                    reason = f"Decisión persistida ({manual['origin']})."
            else:
                category = inferred
            outcome[key] = {"category": category, "reason": reason, "row": row,
                            "course_id": cid, "assignment_id": row["id"]}
            counts[category] += 1
    return outcome, counts


def approve(root, key, category, snapshots, *, confirm=False):
    if not confirm or category not in MANUAL:
        raise SyncError("Decisión de clasificación sin confirmación válida.")
    records, _ = classify(snapshots, load_decisions(root))
    if key not in records:
        raise SyncError("No existe el assignment en snapshots verificados.")
    row = records[key]["row"]
    stored = load_decisions(root)
    current = stored["decisions"].get(key)
    new = {"category": category, "origin": "manual", "name": row.get("name")}
    if current == new:
        return False
    stored["decisions"][key] = new
    atomic_json(decisions_path(root), stored)
    return True



def persist_rule_decisions(root, snapshots):
    """Materializar una sola vez las clasificaciones no ambiguas observadas."""
    data = load_decisions(root)
    items, _ = classify(snapshots, data)
    changed = 0
    for key, entry in items.items():
        if key in data["decisions"] or entry["category"] == REVIEW:
            continue
        data["decisions"][key] = {
            "category": entry["category"], "origin": "regla_confirmada",
            "name": entry["row"].get("name")
        }
        changed += 1
    if changed:
        atomic_json(decisions_path(root), data)
    return changed
