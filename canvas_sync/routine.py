"""Rutina Canvas -> V2 (Etapa 1): actualizar evidencia y preparar excepciones.

No modifica Canvas, Excel, estado de publicación ni clasificaciones aprobadas.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from academic_store.store import DB_NAME
from capture.storage.job import job_lock
from . import auth
from .common import StoreBusy, SyncError, atomic_json, safe_dir
from .enriched import sync_rich
from .reconcile import preview
from .semester import load_registry, active_mappings, list_canvas_courses, save_discovery
from .sync import sha


def workbook_version(runtime):
    """Hash de la base SQLite canónica, comprobado contra el estado local publicado.

    No atribuye frescura remota. El antiguo database.xlsx ya no cuenta.
    """
    root = Path(runtime)
    path = root / DB_NAME
    state_path = root / "control" / "database-work-state.json"
    if not path.is_file() or not state_path.is_file():
        raise SyncError("No hay base local y estado de control para reconciliar.")
    if path.is_symlink() or state_path.is_symlink():
        raise SyncError("No se admite una base/estado redirigido.")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("status") != "free" or state.get("lock") is not None:
        raise StoreBusy("El estado local de la base no está libre.", lock=state.get("lock"))
    expected = (state.get("published") or {}).get("sha256")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if not expected or expected != actual:
        raise SyncError("La base local difiere del hash publicado en el estado local.")
    return actual


def ignored_as_activity(item):
    """Solo descarta de la COLA DE ACTIVIDADES, no de recursos ni del inventario."""
    title = (item.get("titulo_canvas") or "").strip().casefold()
    kind = item.get("tipo_provisional")
    if kind == "nota_agregada" and title.startswith("nota final"):
        return "resumen_de_notas"
    if kind == "informativo" and ("class recordings" in title or "tutor & class link" in title):
        return "informacion_o_enlace"
    return None


def prepare_report(comparison, snapshots, workbook_sha, previous=None):
    """Diferencia nueva alerta versus pendiente persistente. No confirma actividades."""
    prior = previous or {}
    prev = {x["key"]: x["signature"] for category in ("pendientes", "coincidencias", "excluidos")
            for x in prior.get(category, [])}
    previous_pending = {x["key"]: x.get("situacion") for x in prior.get("pendientes", [])}
    previously_unresolved = {key for key, status in previous_pending.items()
                             if status == "evidencia_cambio_sin_diferencia_visible"}
    pending, ignored, matched, updates = [], [], [], []
    for item in comparison["items"]:
        cid, aid = item["course_id"], item["assignment_id"]
        key = f"{cid}:{aid}"
        # Hash de evidencia completa: detecta modificaciones de fechas, calificación,
        # instrucciones, rúbricas, etc., aunque el comparador V2 aún no las aplique.
        raw = next((x for x in snapshots[cid]["data"]["assignments"] if x["id"] == aid), None)
        if raw is None:
            raise SyncError("Evidencia de assignment desaparecida.")
        signature = sha({"canvas": raw, "decision": item["decision"],
                        "diferencias": item["diferencias"], "v2": item["actividad_id"]})
        category = ignored_as_activity(item)
        minimal = {"key": key, "materia_id": item["materia_id"], "assignment_id": aid,
                   "titulo": item["titulo_canvas"], "signature": signature}
        if category and item["actividad_id"] is None:
            ignored.append({**minimal, "motivo": category})
            continue
        newly_registered = (item["decision"] == "coincidencia" and item["actividad_id"]
                            and previous_pending.get(key) in ("candidato_nuevo", "revisar_clasificacion"))
        if newly_registered or (item["decision"] == "coincidencia"
                                and key not in previously_unresolved
                                and (key not in prev or prev[key] == signature)):
            matched.append(minimal)
            continue
        situation = ("evidencia_cambio_sin_diferencia_visible" if item["decision"] == "coincidencia"
                     else item["decision"])
        record = {**minimal, "situacion": situation,
                  "tipo_provisional": item["tipo_provisional"],
                  "actividad_id": item["actividad_id"],
                  "diferencias": item["diferencias"],
                  "referencias_externas": item["referencias_externas"],
                  "submitted_at": item["submitted_at"]}
        pending.append(record)
        if key not in prev:
            updates.append({**minimal, "novedad": "nuevo_para_revisar"})
        elif prev[key] != signature:
            updates.append({**minimal, "novedad": "evidencia_modificada"})
    for key in sorted(set(prev) - {f"{x['course_id']}:{x['assignment_id']}" for x in comparison["items"]}):
        updates.append({"key": key, "novedad": "ya_no_aparece_en_snapshot",
                        "titulo": None, "materia_id": None, "assignment_id": None, "signature": None})
    return {
        "schema_version": 1, "modo": "solo_lectura", "workbook_sha256": workbook_sha,
        "snapshot_sha256": {str(k): snapshots[k]["sha256"] for k in sorted(snapshots)},
        "resumen": {"total_canvas": len(comparison["items"]),
                    "ya_en_base": len(matched),
                    "excluidos_de_actividades": len(ignored),
                    "pendientes": len(pending),
                    "novedades_para_revisar": len(updates)},
        "coincidencias": matched, "excluidos": ignored,
        "pendientes": pending, "novedades": updates,
        "advertencia": "No equivale a una aprobación, sincronización del Excel ni comprobación remota de su versión."
    }


def token_block(runtime, now=None):
    """Vencimiento del token desde los metadatos no secretos; nunca descifra."""
    current = now or datetime.now().astimezone()
    try:
        return auth.notice(auth.read_meta(runtime), current.date(), now=current)
    except SyncError as exc:
        return {"vence": None, "dias_restantes": None, "aviso": str(exc)}


def routine(runtime, do_sync=True, synchronizer=sync_rich, comparer=preview,
            registry_override=None, course_discoverer=list_canvas_courses):
    root = Path(runtime).resolve()
    location = safe_dir(root, "procesamiento", "canvas-sync", "routine")
    with job_lock(location):
        base_sha = workbook_version(root)
        registry = registry_override if registry_override is not None else load_registry(root)
        mappings = active_mappings(registry)
        discovery_result = None
        selected = tuple(sorted(mappings))
        observed = selected
        if do_sync:
            canvas_courses = course_discoverer(root)
            discovery_result = save_discovery(root, registry, canvas_courses)
            visible = {x["course_id"] for x in canvas_courses}
            selected = tuple(cid for cid in selected if cid in visible)
            if len(selected) != len(mappings):
                return {"changed": False, "report": None, "path": None,
                        "discovery": discovery_result, "incomplete_coverage": True,
                        "missing_course_ids": sorted(set(mappings) - set(selected))}
            # Las materias de bloques anteriores son optativas para esta corrida:
            # si continúan visibles, buscar sus calificaciones tardías.
            observed = tuple(sorted(x["course_id"] for x in registry["courses"]
                                    if x["course_id"] in visible))
            synced = synchronizer(root, course_ids=observed)
            if {x["course_id"] for x in synced} != set(observed):
                raise SyncError("Sincronización incompleta de cursos asociados.")
        if not selected:
            raise SyncError("El semestre no tiene cursos asociados.")
        comparison = comparer(root, course_ids=selected, mappings=mappings)
        snapshots = {}
        for cid in selected:
            p = root / "procesamiento" / "canvas-sync" / "v01" / str(cid) / "latest.json"
            snapshots[cid] = json.loads(p.read_text(encoding="utf-8"))
        from .reconcile import verify_snapshot
        for cid, snapshot in snapshots.items():
            verify_snapshot(snapshot, cid)
            if comparison["snapshot_sha256"].get(str(cid)) != snapshot["sha256"]:
                raise SyncError("Snapshot Canvas cambió durante la reconciliación.")
        if workbook_version(root) != base_sha:
            raise SyncError("El Excel local cambió durante la rutina; resultado descartado.")
        semester_dir = safe_dir(location, "semestres", registry["active_semester"])
        block = registry["active_block"]
        block_dir = safe_dir(semester_dir, f"bloque{block}")
        latest = block_dir / "latest.json"
        # Migración: compartir SOLO evidencias de cursos transversales entre
        # bloques, sin interpretar la ausencia de cursos del otro como retirada.
        other_block = semester_dir / f"bloque{3-block}" / "latest.json"
        old_semester = semester_dir / "latest.json"
        legacy = location / "latest.json"
        for path in (latest, other_block, old_semester, legacy):
            if path.is_symlink():
                raise SyncError("Destino de informe redirigido.")
        source = next((p for p in (latest, other_block, old_semester, legacy)
                       if p.is_file() and (p != legacy or registry["active_semester"] == "2026-2")), None)
        previous = json.loads(source.read_text(encoding="utf-8"))["report"] if source else None
        if previous is not None:
            prefixes = tuple(f"{cid}:" for cid in selected)
            previous = {**previous, **{
                category: [x for x in previous.get(category, [])
                           if x["key"].startswith(prefixes)]
                for category in ("coincidencias", "excluidos", "pendientes")
            }}
        report = prepare_report(comparison, snapshots, base_sha, previous)
        report["semestre"] = registry["active_semester"]
        report["bloque"] = block
        report["materias_del_bloque"] = list(selected)
        report["token"] = token_block(root)
        # Novedades y token no forman parte del digest de contenido: cambian por
        # la anterior ejecución o por la fecha, no por el estado académico.
        canonical = {k: v for k, v in report.items() if k not in ("novedades", "token")}
        canonical["resumen"] = {k: v for k, v in report["resumen"].items()
                                if k != "novedades_para_revisar"}
        digest = sha(canonical)
        if latest.exists() and json.loads(latest.read_text(encoding="utf-8")).get("sha256") == digest:
            # Conservar la última versión; ninguna nueva alerta ni snapshot.
            return {"changed": False, "report": {**report, "novedades": []},
                    "path": str(latest), "discovery": discovery_result,
                    "observed_course_ids": list(observed)}
        record = {"sha256": digest, "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                  "report": report}
        atomic_json(latest, record)
        return {"changed": True, "report": report, "path": str(latest),
                "discovery": discovery_result, "observed_course_ids": list(observed)}
