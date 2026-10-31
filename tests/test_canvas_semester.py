"""Regresión multisemestre: matrículas ficticias, sin modificar Canvas o Excel."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from canvas_sync.common import SyncError
from canvas_sync.reconcile import reconcile
from canvas_sync.routine import routine
from canvas_sync.semester import (active_mappings, discovery, load_registry,
                                   onboard, save_discovery, upgrade_registry, set_active_block)
from canvas_sync.sync import sha


OLD = {"schema_version": 1, "active_semester": "2026-2",
       "courses": [{"course_id": 82862, "subject_id": "MAT-GBD",
                    "semester": "2026-2", "provenance": "workbook"},
                   {"course_id": 82892, "subject_id": "MAT-ST",
                    "semester": "2026-2", "provenance": "documentado_pendiente_en_excel"}]}


def canvas_record(cid, semester=None):
    return {"course_id": cid, "name": f"Materia de prueba {cid}",
            "course_code": semester or ""}


def evidence(cid):
    data = {"schema_version": 2, "source": "canvas_rest_get_only",
            "course": {"id": cid}, "assignments": [{"id": 333, "name": "Evaluación",
                                                    "kind_hint": "cuestionario"}]}
    return {"data": data, "sha256": sha(data)}


class SemesterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        location = self.root / "control" / "canvas-sync"
        location.mkdir(parents=True)
        (location / "course-registry.json").write_text(json.dumps(OLD), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_course_requires_confirm_and_keeps_history(self):
        rows = [canvas_record(82862), canvas_record(990001, "2027-1")]
        self.assertEqual([x["course_id"] for x in discovery(load_registry(self.root), rows)["candidates"]], [990001])
        with self.assertRaises(SyncError):
            onboard(self.root, "2027-1", [990001], rows)
        self.assertEqual(len(load_registry(self.root)["courses"]), 2)
        added = onboard(self.root, "2027-1", [990001], rows,
                        course_blocks={990001: [1, 2]}, confirm=True)
        self.assertEqual(added[0]["subject_id"], "MAT-20271-C990001")
        self.assertEqual(added[0]["blocks"], [1, 2])
        loaded = load_registry(self.root)
        self.assertEqual(len(loaded["courses"]), 3)
        self.assertEqual(active_mappings(loaded), {990001: "MAT-20271-C990001"})
        self.assertEqual(loaded["courses"][0]["subject_id"], "MAT-GBD")
        self.assertFalse((self.root / "database.xlsx").exists())
        self.assertFalse((self.root / "academic-manager.sqlite").exists())
        with self.assertRaises(SyncError):
            onboard(self.root, "2027-1", [990001], rows,
                        course_blocks={990001: [1, 2]}, confirm=True)

    def test_bad_semester_and_unknown_course_cannot_be_activated(self):
        rows = [canvas_record(990001)]
        for semester, ids in [("2027", [990001]), ("2027-1", [100000]), ("2027-1", [990001, 990001])]:
            with self.subTest(semester=semester, ids=ids):
                with self.assertRaises(SyncError):
                    onboard(self.root, semester, ids, rows,
                            course_blocks={cid: [1] for cid in ids}, confirm=True)
        self.assertEqual(load_registry(self.root)["active_semester"], "2026-2")

    def test_discovery_is_idempotent(self):
        rows = [canvas_record(82862), canvas_record(990001)]
        first = save_discovery(self.root, load_registry(self.root), rows)
        second = save_discovery(self.root, load_registry(self.root), rows)
        self.assertEqual(first["new_candidate_ids"], [990001])
        self.assertEqual(second["new_candidate_ids"], [])
        self.assertFalse(second["changed"])

    def test_same_assignment_id_in_future_term_cannot_match_old_subject(self):
        future = {**upgrade_registry(OLD), "active_semester": "2027-1", "active_block": 1,
                  "courses": upgrade_registry(OLD)["courses"] + [{"course_id": 990001,
                      "subject_id": "MAT-20271-C990001", "semester": "2027-1", "blocks": [1, 2]}]}
        result = reconcile({990001: evidence(990001)}, [
            {"materia_id": "MAT-GBD", "lms": "Canvas", "actividad_id": "ACT-GBD-001",
             "objeto_externo_id": 333}], mappings=active_mappings(future))
        self.assertEqual(result["counts"]["candidato_nuevo"], 1)
        self.assertIsNone(result["items"][0]["actividad_id"])

    def test_missing_old_course_cannot_be_treated_as_removed(self):
        path = self.root / "academic-manager.sqlite"
        path.write_bytes(b"mock bytes")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.root / "control" / "database-work-state.json").write_text(
            json.dumps({"status": "free", "lock": None, "published": {"sha256": digest}}),
            encoding="utf-8")
        result = routine(self.root, registry_override=upgrade_registry(OLD),
                         course_discoverer=lambda root: [canvas_record(82862), canvas_record(990001)],
                         synchronizer=lambda *_a, **_kw: self.fail("should not sync"))
        self.assertTrue(result["incomplete_coverage"])
        self.assertEqual(result["missing_course_ids"], [82892])
        self.assertFalse((self.root / "procesamiento" / "canvas-sync" / "routine" /
                          "semestres" / "2026-2" / "bloque2" / "latest.json").exists())

    def test_bootstrap_only_when_no_registry(self):
        (self.root / "control" / "canvas-sync" / "course-registry.json").unlink()
        with patch("canvas_sync.semester.workbook_courses", return_value=("2026-2", OLD["courses"], "legacy")):
            registry = load_registry(self.root)
        self.assertEqual(len(registry["courses"]), 2)
        self.assertEqual(load_registry(self.root), registry)


if __name__ == "__main__":
    unittest.main()
