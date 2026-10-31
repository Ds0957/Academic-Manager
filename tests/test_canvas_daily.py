"""Regresión del flujo diario: pruebas sintéticas, sin acceso a Canvas ni Excel."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from canvas_sync.common import SyncError
from canvas_sync.routine import prepare_report, routine
from canvas_sync.sync import sha

# Aulas de 2026-2 como datos de ejemplo.
COURSES = {82862: "MAT-GBD", 82892: "MAT-ST", 91003: "MAT-ING3",
           91004: "MAT-SO2", 91005: "MAT-SER"}


def snap(cid, assignments):
    data = {"schema_version": 2, "source": "canvas_rest_get_only",
            "course": {"id": cid}, "assignments": assignments}
    return {"sha256": sha(data), "data": data}


def item(cid, aid, decision="candidato_nuevo", kind="actividad_por_revisar"):
    return {"course_id": cid, "materia_id": COURSES[cid], "assignment_id": aid,
            "titulo_canvas": f"Actividad {aid}", "tipo_provisional": kind,
            "actividad_id": None, "decision": decision, "diferencias": {},
            "referencias_externas": {}, "submitted_at": None}


class RoutineTests(unittest.TestCase):
    def test_explicit_non_activity_filter_is_conservative(self):
        cid = 82862
        items = [
            {**item(cid, 1, "revisar_clasificacion", "nota_agregada"),
             "titulo_canvas": "Nota final [P1]"},
            {**item(cid, 2, "revisar_clasificacion", "complementaria"),
             "titulo_canvas": "Actividad complementaria"},
            item(cid, 3),
        ]
        snapshot = snap(cid, [{"id": 1}, {"id": 2}, {"id": 3}])
        report = prepare_report({"items": items}, {cid: snapshot}, "hash")
        self.assertEqual(report["resumen"]["excluidos_de_actividades"], 1)
        self.assertEqual(report["resumen"]["pendientes"], 2)
        self.assertEqual(report["resumen"]["novedades_para_revisar"], 2)

    def test_repeat_and_source_change_only_alert_once(self):
        cid = 82862
        comparison = {"items": [item(cid, 391671)]}
        snapshots = {cid: snap(cid, [{"id": 391671, "name": "Original"}])}
        first = prepare_report(comparison, snapshots, "hash")
        second = prepare_report(comparison, snapshots, "hash", first)
        self.assertEqual(len(first["novedades"]), 1)
        self.assertEqual(second["novedades"], [])
        changed = {cid: snap(cid, [{"id": 391671, "name": "Actualizada"}])}
        third = prepare_report(comparison, changed, "hash", second)
        self.assertEqual(third["novedades"][0]["novedad"], "evidencia_modificada")

    def test_change_existing_uncompared_source_becomes_exception(self):
        cid = 82862
        existing = item(cid, 1, decision="coincidencia")
        existing["actividad_id"] = "ACT-GBD-001"
        before = prepare_report({"items": [existing]},
                                {cid: snap(cid, [{"id": 1, "due_at": "2026-10-12"}])}, "hash")
        after = prepare_report({"items": [existing]},
                               {cid: snap(cid, [{"id": 1, "due_at": "2026-10-19"}])}, "hash", before)
        self.assertEqual(before["resumen"]["ya_en_base"], 1)
        self.assertEqual(after["pendientes"][0]["situacion"], "evidencia_cambio_sin_diferencia_visible")
        self.assertEqual(after["novedades"][0]["novedad"], "evidencia_modificada")
        again = prepare_report({"items": [existing]},
                               {cid: snap(cid, [{"id": 1, "due_at": "2026-10-19"}])}, "hash", after)
        self.assertEqual(len(again["pendientes"]), 1)
        self.assertEqual(again["novedades"], [])

    def test_full_local_workflow_idempotent_and_failure_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "control").mkdir()
            workbook = root / "academic-manager.sqlite"
            workbook.write_bytes(b"fake-workbook-for-test-only")
            expected = hashlib.sha256(workbook.read_bytes()).hexdigest()
            state = {"status": "free", "lock": None, "published": {"sha256": expected}}
            (root / "control" / "database-work-state.json").write_text(json.dumps(state), encoding="utf-8")
            records = []
            for cid in COURSES:
                folder = root / "procesamiento" / "canvas-sync" / "v01" / str(cid)
                folder.mkdir(parents=True)
                data = snap(cid, [{"id": cid}])
                (folder / "latest.json").write_text(json.dumps(data), encoding="utf-8")
                records.append(item(cid, cid))
            def comparator(runtime, course_ids, mappings):
                return {"items": records, "snapshot_sha256": {
                    str(cid): snap(cid, [{"id": cid}])["sha256"] for cid in course_ids}}
            registry = {"schema_version": 2, "active_semester": "2026-2", "active_block": 2,
                        "courses": [{"course_id": cid, "subject_id": COURSES[cid],
                                     "semester": "2026-2", "blocks": [2]} for cid in COURSES]}
            first = routine(root, do_sync=False, comparer=comparator, registry_override=registry)
            second = routine(root, do_sync=False, comparer=comparator, registry_override=registry)
            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertEqual(len(first["report"]["novedades"]), 5)
            self.assertEqual(second["report"]["novedades"], [])
            # Sin metadatos de vencimiento: el bloque token lo informa sin fallar.
            self.assertEqual((first["report"]["token"]["vence"], first["report"]["token"]["dias_restantes"]),
                             (None, None))
            self.assertIn("status", first["report"]["token"]["aviso"])
            previous = (root / "procesamiento" / "canvas-sync" / "routine" / "semestres" / "2026-2" / "bloque2" / "latest.json").read_bytes()
            workbook.write_bytes(b"changed")
            with self.assertRaises(SyncError):
                routine(root, do_sync=False, comparer=comparator, registry_override=registry)
            self.assertEqual(previous, (root / "procesamiento" / "canvas-sync" / "routine" / "semestres" / "2026-2" / "bloque2" / "latest.json").read_bytes())

    def test_daily_report_includes_token_block_outside_the_digest(self):
        from datetime import datetime, timedelta
        from canvas_sync import auth
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "control" / "canvas-sync").mkdir(parents=True)
            workbook = root / "academic-manager.sqlite"
            workbook.write_bytes(b"fake-workbook-for-test-only")
            state = {"status": "free", "lock": None,
                     "published": {"sha256": hashlib.sha256(workbook.read_bytes()).hexdigest()}}
            (root / "control" / "database-work-state.json").write_text(json.dumps(state), encoding="utf-8")
            cid = 82862
            folder = root / "procesamiento" / "canvas-sync" / "v01" / str(cid)
            folder.mkdir(parents=True)
            (folder / "latest.json").write_text(json.dumps(snap(cid, [{"id": cid}])), encoding="utf-8")
            def comparator(runtime, course_ids, mappings):
                return {"items": [item(cid, cid)],
                        "snapshot_sha256": {str(cid): snap(cid, [{"id": cid}])["sha256"]}}
            registry = {"schema_version": 2, "active_semester": "2026-2", "active_block": 2,
                        "courses": [{"course_id": cid, "subject_id": COURSES[cid],
                                     "semester": "2026-2", "blocks": [2]}]}
            now = datetime.now().astimezone()
            auth.save_meta(root, now + timedelta(days=40))
            first = routine(root, do_sync=False, comparer=comparator, registry_override=registry)
            self.assertEqual(first["report"]["token"],
                             {"vence": (now + timedelta(days=40)).date().isoformat(),
                              "dias_restantes": 40, "aviso": None})
            latest = Path(first["path"])
            self.assertEqual(json.loads(latest.read_text(encoding="utf-8"))["report"]["token"],
                             first["report"]["token"])
            auth.save_meta(root, now + timedelta(days=10))
            second = routine(root, do_sync=False, comparer=comparator, registry_override=registry)
            self.assertFalse(second["changed"])
            self.assertEqual(second["report"]["token"]["dias_restantes"], 10)
            self.assertIn("10 días", second["report"]["token"]["aviso"])

    def test_approved_new_assignment_leaves_pending_queue_after_import(self):
        cid = 82862
        new_item = item(cid, 391683)
        snapshots = {cid: snap(cid, [{"id": 391683, "name": "Actividad evaluativa - Eje 2"}])}
        before = prepare_report({"items": [new_item]}, snapshots, "old_sha")
        linked = {**new_item, "decision": "coincidencia",
                  "actividad_id": "ACT-C82862-A391683"}
        after = prepare_report({"items": [linked]}, snapshots, "new_sha", before)
        self.assertEqual(after["resumen"]["ya_en_base"], 1)
        self.assertEqual(after["resumen"]["pendientes"], 0)
        self.assertEqual(after["novedades"], [])

    def test_removed_assignment_is_flagged_not_deleted(self):
        cid = 82862
        old = prepare_report({"items": [item(cid, 1)]},
                             {cid: snap(cid, [{"id": 1}])}, "hash")
        new = prepare_report({"items": []},
                             {cid: snap(cid, [])}, "hash", old)
        self.assertEqual(new["novedades"][0]["novedad"], "ya_no_aparece_en_snapshot")


if __name__ == "__main__":
    unittest.main()
