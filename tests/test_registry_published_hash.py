"""Registro de Canvas con un hash anterior a la base publicada (workbook_blocks.publish).

Otra publicación (una clase de Capture, otro equipo) cambia la base sin tocar
course-registry.json. Si las materias, bloques y semestre siguen iguales, la publicación
continúa y guarda el hash nuevo; si cambiaron, se detiene como siempre.
"""
import json
import unittest

from canvas_sync.common import SyncError
from canvas_sync.semester import workbook_courses
from canvas_sync.workbook_blocks import publish
from capture.storage.metadata import sha256
from tests.test_chat_writes import ChatFixture

OLD = "0" * 64


class StaleRegistryHashTests(ChatFixture):
    def registry_path(self):
        return self.runtime / "control" / "canvas-sync" / "course-registry.json"

    def registry(self, **changes):
        # Materias, bloques y semestre tal como están en la base publicada simulada.
        active, courses, block = workbook_courses(None, workbook_path=self.shared)
        data = json.loads(self.registry_path().read_text(encoding="utf-8"))
        data.update(active_semester=active, active_block=block, courses=courses, **changes)
        self.registry_path().write_text(json.dumps(data), encoding="utf-8")
        return data

    def test_same_enrollment_with_an_older_hash_continues_and_records_the_published_hash(self):
        registry = self.registry(workbook_sha256=OLD)
        with self.drive():
            result = publish(self.runtime, registry, dry_run=False, planner=lambda path, reg: [])
        self.assertEqual(result["mode"], "unchanged")
        self.assertEqual(result["sha256"], sha256(self.shared))
        saved = json.loads(self.registry_path().read_text(encoding="utf-8"))
        self.assertEqual(saved["workbook_sha256"], sha256(self.shared))
        self.assertEqual(saved["courses"], registry["courses"])
        self.assertEqual(self.uploads, 0)

    def test_different_enrollment_with_an_older_hash_still_stops_and_changes_nothing(self):
        registry = self.registry(workbook_sha256=OLD)
        registry["courses"].append({"subject_id": "MAT-X", "semester": registry["active_semester"],
                                    "course_id": 9999, "blocks": [2], "provenance": "workbook"})
        self.registry_path().write_text(json.dumps(registry), encoding="utf-8")
        before = self.registry_path().read_bytes()
        state = dict(self.shared_state)
        with self.drive(), self.assertRaisesRegex(SyncError, "más antiguo que el workbook publicado"):
            publish(self.runtime, registry, dry_run=False, planner=lambda path, reg: [])
        self.assertEqual(self.registry_path().read_bytes(), before)
        self.assertEqual(self.shared_state, state)
        self.assertEqual(self.uploads, 0)

    def test_matching_hash_keeps_working_as_before(self):
        registry = self.registry(workbook_sha256=sha256(self.shared))
        with self.drive():
            result = publish(self.runtime, registry, dry_run=False, planner=lambda path, reg: [])
        self.assertEqual(result["mode"], "unchanged")
        self.assertEqual(self.uploads, 0)


if __name__ == "__main__":
    unittest.main()
