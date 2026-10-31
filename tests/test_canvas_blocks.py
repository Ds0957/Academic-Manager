"""Bloques académicos: transversales, continuidad e idempotencia (sintético)."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from canvas_sync.common import SyncError
from canvas_sync.reconcile import reconcile
from canvas_sync.routine import routine
from canvas_sync.semester import (active_mappings, load_registry, onboard,
                                  set_active_block, upgrade_registry, set_course_blocks)
from canvas_sync.sync import sha


def evidence(cid):
    payload = {"schema_version": 2, "source": "canvas_rest_get_only",
               "course": {"id": cid},
               "assignments": [{"id": cid * 10, "name": f"Tarea {cid}",
                                "kind_hint": "actividad_por_revisar"}]}
    return {"data": payload, "sha256": sha(payload)}


class AcademicBlockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        folder = self.root / "control" / "canvas-sync"
        folder.mkdir(parents=True)
        self.initial = {
            "schema_version": 2, "active_semester": "2026-2", "active_block": 1,
            "courses": [
                {"course_id": 91003, "subject_id": "MAT-ING3", "semester": "2026-2",
                 "blocks": [1, 2]},
                {"course_id": 100001, "subject_id": "MAT-BLOQUE1", "semester": "2026-2",
                 "blocks": [1]},
                {"course_id": 82862, "subject_id": "MAT-GBD", "semester": "2026-2",
                 "blocks": [2]},
            ]
        }
        (folder / "course-registry.json").write_text(json.dumps(self.initial), encoding="utf-8")
        workbook = self.root / "academic-manager.sqlite"
        workbook.write_bytes(b"stub for unit testing")
        (self.root / "control" / "database-work-state.json").write_text(json.dumps({
            "status": "free", "lock": None,
            "published": {"sha256": hashlib.sha256(workbook.read_bytes()).hexdigest()}
        }), encoding="utf-8")
        for cid in (91003, 100001, 82862):
            location = self.root / "procesamiento" / "canvas-sync" / "v01" / str(cid)
            location.mkdir(parents=True)
            (location / "latest.json").write_text(json.dumps(evidence(cid)), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def compare(root, course_ids, mappings):
        items = []
        digests = {}
        for cid in course_ids:
            items.append({
                "course_id": cid, "materia_id": mappings[cid],
                "assignment_id": cid*10, "titulo_canvas": f"Tarea {cid}",
                "tipo_provisional": "actividad_por_revisar", "actividad_id": None,
                "decision": "candidato_nuevo", "diferencias": {},
                "referencias_externas": {}, "submitted_at": None
            })
            digests[str(cid)] = evidence(cid)["sha256"]
        return {"items": items, "snapshot_sha256": digests}

    def test_cross_block_one_subject_not_duplicated(self):
        first = routine(self.root, do_sync=False, comparer=self.compare)
        self.assertEqual(first["report"]["bloque"], 1)
        self.assertEqual(set(first["report"]["materias_del_bloque"]), {91003, 100001})
        self.assertEqual(len(first["report"]["novedades"]), 2)
        changed = set_active_block(self.root, 2, confirm=True)
        self.assertTrue(changed)
        second = routine(self.root, do_sync=False, comparer=self.compare)
        self.assertEqual(set(second["report"]["materias_del_bloque"]), {91003, 82862})
        self.assertEqual([x["key"] for x in second["report"]["novedades"]],
                         [f"82862:{82862*10}"])
        again = routine(self.root, do_sync=False, comparer=self.compare)
        self.assertFalse(again["changed"])
        self.assertEqual(again["report"]["novedades"], [])
        current = load_registry(self.root)
        self.assertEqual(current["active_semester"], "2026-2")
        self.assertEqual(len([c for c in current["courses"] if c["subject_id"] == "MAT-ING3"]), 1)

    def test_missing_old_block_canvas_course_is_not_fatal(self):
        set_active_block(self.root, 2, confirm=True)
        result = routine(
            self.root, do_sync=True, comparer=self.compare,
            course_discoverer=lambda _: [
                {"course_id": 91003, "name": "English III", "course_code": ""},
                {"course_id": 82862, "name": "Database", "course_code": ""}
            ],
            synchronizer=lambda _root, course_ids: [
                {"course_id": cid} for cid in course_ids
            ])
        self.assertNotIn("incomplete_coverage", result)
        self.assertEqual(result["discovery"]["not_visible"], [])
        self.assertEqual(len(result["report"]["materias_del_bloque"]), 2)

    def test_existing_subject_cannot_be_reonboarded_as_second_block(self):
        # Un solo curso transversal; asignar ambos bloques en una única relación.
        rows = [{"course_id": 91003, "name": "English III", "course_code": ""}]
        with self.assertRaises(SyncError):
            onboard(self.root, "2026-2", [91003], rows,
                    course_blocks={91003: [1, 2]}, confirm=True)

    def test_new_block1_course_in_same_semester_does_not_reset_active(self):
        set_active_block(self.root, 2, confirm=True)
        rows = [{"course_id": 777001, "name": "Materia nueva", "course_code": ""}]
        added = onboard(self.root, "2026-2", [777001], rows,
                        course_blocks={777001: [1]}, confirm=True)
        self.assertEqual(added[0]["blocks"], [1])
        reg = load_registry(self.root)
        self.assertEqual(reg["active_block"], 2)
        self.assertNotIn(777001, active_mappings(reg))

    def test_legacy_real_semester_migration_and_future_unknown(self):
        old = {"schema_version": 1, "active_semester": "2026-2",
               "courses": [
                   {"course_id": 91003, "subject_id": "MAT-ING3", "semester": "2026-2"},
                   {"course_id": 82862, "subject_id": "MAT-GBD", "semester": "2026-2"},
                   {"course_id": 777111, "subject_id": "MAT-OTRA", "semester": "2026-2"},
                   {"course_id": 990000, "subject_id": "MAT-OTROSEM", "semester": "2027-1"},
               ]}
        upgraded = upgrade_registry(old)
        self.assertEqual(upgraded["schema_version"], 2)
        self.assertEqual(upgraded["active_block"], 2)
        self.assertEqual(upgraded["courses"][0]["blocks"], [1, 2])
        self.assertEqual(upgraded["courses"][1]["blocks"], [2])
        self.assertEqual(upgraded["courses"][2]["blocks"], [])
        self.assertEqual(upgraded["courses"][3]["blocks"], [])
        with self.assertRaises(SyncError):
            active_mappings(upgraded)

    def test_existing_course_can_become_cross_block_later(self):
        self.assertTrue(set_course_blocks(self.root, 91003, [1], confirm=True))
        self.assertTrue(set_course_blocks(self.root, 91003, [1, 2], confirm=True))
        self.assertFalse(set_course_blocks(self.root, 91003, [2, 1], confirm=True))
        reg = load_registry(self.root)
        english = [x for x in reg["courses"] if x["subject_id"] == "MAT-ING3"]
        self.assertEqual(len(english), 1)
        self.assertEqual(english[0]["blocks"], [1, 2])
        self.assertEqual(english[0]["block_provenance"],
                         "confirmado_por_estudiante_pendiente_excel")
        self.assertEqual(reg["active_block"], 1)

    def test_invalid_block_changes_rejected(self):
        with self.assertRaises(SyncError):
            set_active_block(self.root, 3, confirm=True)
        with self.assertRaises(SyncError):
            set_active_block(self.root, 2, confirm=False)
        with self.assertRaises(SyncError):
            onboard(self.root, "2027-1", [888], [{"course_id": 888}],
                    course_blocks={888: [1, 1]}, confirm=True)


if __name__ == "__main__":
    unittest.main()
