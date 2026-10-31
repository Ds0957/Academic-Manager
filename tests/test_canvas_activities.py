"""Clasificación y actualización automática: pruebas sintéticas sin Drive ni Canvas real."""
import json
import tempfile
import unittest
from pathlib import Path

from academic_store import store
from academic_store.sheetview import open_book
from tests.xlsx_fixture import write_sheets

from canvas_sync.classification import classify, inferred_category, approve, load_decisions
from canvas_sync.common import SyncError
from canvas_sync.sync import sha
from canvas_sync.workbook_activities import plan_activities, mutate_activities, _local, _states, _excel_serial

def snapshot(cid, rows):
    data = {"schema_version": 2, "source": "canvas_rest_get_only",
            "course": {"id": cid}, "assignments": rows}
    return {"data": data, "sha256": sha(data)}


def item(aid, name, *, score=None, published=True, submitted=False):
    return {"id": aid, "name": name, "published": published, "kind_hint": "actividad_por_revisar",
            "html_url": f"https://areandina.instructure.com/courses/990001/assignments/{aid}",
            "points_possible": 20.0, "unlock_at": "2026-10-01T05:00:00Z",
            "due_at": "2026-10-12T04:59:59Z", "lock_at": "2026-10-12T04:59:59Z",
            "submission": {"workflow_state": "graded" if score is not None else
                           "submitted" if submitted else "unsubmitted",
                           "score": score, "submitted_at": "2026-10-10T12:00:00Z" if submitted else None}}


class ClassificationTests(unittest.TestCase):
    def test_conservative_rules(self):
        for name, kind, category in (
            ("Actividad evaluativa - Eje 2", "actividad_por_revisar", "actividad"),
            ("HOMEWORK ACTIVITY - WEEK 12 - UNIT 5", "actividad_por_revisar", "actividad"),
            ("Nota final [P1]", "nota_agregada", "resumen_calificaciones"),
            ("Actividad Complementaria Eje 3", "complementaria", "revisar"),
            ("LEARNING AGREEMENT - ACTA DE INICIO", "informativo_o_evaluable", "revisar"),
            ("TUTOR & CLASS LINK", "informativo", "referencia"),
        ):
            row = item(1, name)
            row["kind_hint"] = kind
            self.assertEqual(inferred_category(row)[0], category)

    def test_gradeable_supplemental_distinct_from_required_and_graded_agreement(self):
        extra = item(101, "Actividad Complementaria Eje 1")
        extra.update(kind_hint="complementaria", grading_type="points",
                     points_possible=5.0, submission_types=["online_upload"])
        self.assertEqual(inferred_category(extra)[0], "actividad")
        extra["submission_types"] = []
        self.assertEqual(inferred_category(extra)[0], "revisar")
        agreement = item(102, "LEARNING AGREEMENT - ACTA DE INICIO", score=4.0, submitted=True)
        agreement["kind_hint"] = "informativo_o_evaluable"
        self.assertEqual(inferred_category(agreement)[0], "actividad")
        agreement["submission"]["score"] = None
        self.assertEqual(inferred_category(agreement)[0], "revisar")

    def test_manual_decision_persists_and_name_change_reopens_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [item(991, "Actividad Complementaria Eje 1")]
            rows[0]["kind_hint"] = "complementaria"
            snap = {990001: snapshot(990001, rows)}
            self.assertTrue(approve(tmp, "990001:991", "actividad", snap, confirm=True))
            self.assertFalse(approve(tmp, "990001:991", "actividad", snap, confirm=True))
            self.assertEqual(classify(snap, load_decisions(tmp))[1]["actividad"], 1)
            rows2 = [dict(rows[0], name="Actividad complementaria renombrada")]
            self.assertEqual(classify({990001: snapshot(990001, rows2)}, load_decisions(tmp))[1]["revisar"], 1)
            with self.assertRaises(SyncError):
                approve(tmp, "990001:991", "actividad", snap, confirm=False)


class ActivityWorkbookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        source = Path(self.tmp.name) / "source.xlsx"
        from canvas_sync.workbook_activities import FIRST_HEADERS, EXTRA_HEADERS
        from tests.test_academic_store import workbook
        write_sheets(source, workbook(**{"02 - Actividades": [list(FIRST_HEADERS + EXTRA_HEADERS)]}))
        self.path = Path(self.tmp.name) / store.DB_NAME
        store.migrate(source, self.path)
        self.registry = {"schema_version": 2, "active_semester":"2027-1",
                         "active_block":1, "courses":[
                             {"course_id": 990001,"semester":"2027-1",
                              "subject_id":"MAT-20271-C990001","blocks":[1]}]}
        self.decs={"schema_version":1,"decisions":{}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_score_and_repeat_no_changes(self):
        source=item(300, "Actividad evaluativa - Eje 1",score=15.0,submitted=True)
        source["quiz_id"] = 123456
        snapshots={990001:snapshot(990001,[source])}
        changes,errors=plan_activities(self.path,self.registry,snapshots,self.decs)
        self.assertFalse(errors)
        self.assertEqual(sum(c[2]==1 for c in changes),1)
        mutate_activities(self.path,changes)
        self.assertEqual(plan_activities(self.path,self.registry,snapshots,self.decs)[0],[])
        app,book=open_book(self.path)
        try:
            s=book.Worksheets.Item("02 - Actividades")
            self.assertEqual(s.Cells.Item(2,1).Value,"ACT-C990001-A300")
            self.assertEqual(s.Cells.Item(2,16).Value,15.0)
            self.assertEqual(s.Cells.Item(2,13).Value,"calificada")
            self.assertAlmostEqual(s.Cells.Item(2,26).Value2,
                                   _excel_serial("2026-10-10T12:00:00Z"), places=7)
            self.assertIn("123456",str(s.Cells.Item(2,27).Value))
        finally:
            book.Close(False);app.Quit()
        newer = item(300,"Actividad evaluativa - Eje 1",score=17.0,submitted=True)
        newer["quiz_id"] = 123456
        new={990001:snapshot(990001,[newer])}
        ch,er=plan_activities(self.path,self.registry,new,self.decs)
        self.assertEqual([(x[2],x[4]) for x in ch],[(16,17.0)])
        mutate_activities(self.path,ch)
        self.assertEqual(plan_activities(self.path,self.registry,new,self.decs)[0],[])

    def test_no_import_of_complementary_without_approval(self):
        row=item(321,"Actividad Complementaria Eje 1")
        row["kind_hint"]="complementaria"
        changes,errs=plan_activities(self.path,self.registry,
                                     {990001:snapshot(990001,[row])},self.decs)
        self.assertFalse(any(x[1] != 1 for x in changes))  # solo encabezados de migración
        self.assertEqual(errs,["990001:321"])

    def test_late_grade_from_inactive_block_without_creating_old_obligations(self):
        self.registry["courses"].append({
            "course_id": 990002, "subject_id": "MAT-BLOQUE2", "semester": "2027-1",
            "blocks": [2]  # periodo activo 1: histórico no sincronizado como nueva obligación
        })
        store.apply_cells(self.path, [
            ("02 - Actividades", 2, col, None, value)
            for col, value in {1:"ACT-OLD-445",2:"MAT-BLOQUE2",4:"Actividad evaluativa - Eje 1",
                               12:"pendiente",13:"sin_calificar",15:20.0,
                               19:"Canvas",20:"assignment",21:"445"}.items()])
        graded=item(445,"Actividad evaluativa - Eje 1",score=10.0,submitted=True)
        graded["html_url"]="https://areandina.instructure.com/courses/990002/assignments/445"
        unknown=item(446,"Actividad evaluativa - Eje 2")
        unknown["html_url"]="https://areandina.instructure.com/courses/990002/assignments/446"
        snaps={990002:snapshot(990002,[graded,unknown])}
        changes,exc=plan_activities(self.path,self.registry,snaps,self.decs)
        self.assertFalse(exc)
        self.assertFalse(any(x[2]==1 and x[1]>1 for x in changes))
        self.assertIn(("02 - Actividades",2,16,None,10.0),changes)
        mutate_activities(self.path,changes)
        self.assertEqual(plan_activities(self.path,self.registry,snaps,self.decs)[0],[])

    def test_strict_utc_date_and_no_grade_invention(self):
        self.assertEqual(_local("2026-09-26T17:05:46Z").isoformat(),"2026-09-26T12:05:46")
        with self.assertRaises(SyncError):
            _local("2026-09-26T17:05:46")
        row=item(322,"Actividad evaluativa - Eje 2")
        self.assertIsNone(_states(row)[3])


if __name__ == "__main__":
    unittest.main()
