"""Pruebas aisladas de reconciliación: sin servicios, Excel ni escritura."""
import unittest
from canvas_sync.common import SyncError
from canvas_sync.reconcile import reconcile, verify_snapshot
from canvas_sync.sync import sha

GBD = {82862: "MAT-GBD"}

def sample(items):
    data = {"schema_version": 2, "source": "canvas_rest_get_only",
            "course": {"id": 82862}, "assignments": items}
    return {"sha256": sha(data), "data": data}


class ReconciliationTests(unittest.TestCase):
    def test_existing_match_and_new_classification(self):
        data = sample([
            {"id": 391671, "name": "Eje 1", "points_possible": 20,
             "kind_hint": "cuestionario", "classification_confirmed": False,
             "quiz_id": 228076, "submission": {"submitted_at": None}},
            {"id": 391692, "name": "Nota final [P1]", "kind_hint": "nota_agregada",
             "classification_confirmed": False}
        ])
        existing = [{"actividad_id": "ACT-GBD-001", "materia_id": "MAT-GBD",
                     "lms": "Canvas", "objeto_externo_id": 391671,
                     "titulo": "Eje 1", "puntos_posibles": 20}]
        result = reconcile({82862: data}, existing, mappings=GBD)
        self.assertEqual(result["counts"]["coincidencia"], 1)
        self.assertEqual(result["counts"]["revisar_clasificacion"], 1)
        self.assertEqual(result["items"][0]["referencias_externas"], {"quiz_id": 228076})
        self.assertEqual(result["items"][1]["actividad_id"], None)

    def test_detects_change_but_never_applies_it(self):
        data = sample([{"id": 391671, "name": "Nuevo título", "points_possible": 20}])
        existing = [{"actividad_id": "ACT-GBD-001", "materia_id": "MAT-GBD",
                     "lms": "Canvas", "objeto_externo_id": 391671,
                     "titulo": "Anterior", "puntos_posibles": 20}]
        result = reconcile({82862: data}, existing, mappings=GBD)
        self.assertEqual(result["counts"]["revisar_cambios"], 1)
        self.assertEqual(existing[0]["titulo"], "Anterior")

    def test_rejects_corrupt_snapshot(self):
        record = sample([{"id": 1}])
        record["sha256"] = "incorrect"
        with self.assertRaises(SyncError):
            verify_snapshot(record, 82862)

    def test_rejects_ambiguous_existing_ids(self):
        record = sample([])
        existing = [{"materia_id": "MAT-GBD", "lms": "Canvas",
                     "actividad_id": "A", "objeto_externo_id": 1},
                    {"materia_id": "MAT-GBD", "lms": "Canvas",
                     "actividad_id": "B", "objeto_externo_id": 1}]
        with self.assertRaises(SyncError):
            reconcile({82862: record}, existing, mappings=GBD)

    def test_requires_explicit_course_mapping(self):
        # Issue #16: sin aulas del registro no hay respaldo silencioso a las de 2026-2.
        data = sample([{"id": 391671, "name": "Eje 1"}])
        with self.assertRaises(SyncError) as cm:
            reconcile({82862: data}, [])
        self.assertIn("registro", str(cm.exception))
        # Un registro sin aulas activas sigue siendo válido: no autoriza ningún curso.
        self.assertEqual(reconcile({}, [], mappings={})["items"], [])
        with self.assertRaisesRegex(SyncError, "no autorizado"):
            reconcile({82862: data}, [], mappings={})
        self.assertEqual(reconcile({82862: data}, [], mappings=GBD)["items"][0]["materia_id"], "MAT-GBD")

    def test_rejects_duplicate_canvas_ids(self):
        record = sample([{"id": 1}, {"id": 1}])
        with self.assertRaises(SyncError):
            verify_snapshot(record, 82862)


if __name__ == "__main__":
    unittest.main()
