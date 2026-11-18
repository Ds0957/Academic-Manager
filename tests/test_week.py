"""«Mi semana»: clases por atender con su estado y entregas de la semana (odd/tasks/mi-semana.md, S2–S4)."""
import json
import tempfile
import unittest
from pathlib import Path

from academic_access import week
from academic_store import records, store
from capture.storage.metadata import sha256
from tests.test_academic_store import workbook
from tests.test_today import activity, serial
from tests.xlsx_fixture import write_sheets

DAY = "2027-03-10"
SHEETS = workbook(**{
    "01 - Materias": [["materia_id", "semestre", "nombre", "curso_externo_id", "bloques"],
                      ["MAT-A", "2027-1", "Álgebra", "1", "1"],
                      ["MAT-B", "2027-1", "Biología", "2", "1"],
                      ["MAT-OLD", "2026-2", "Antigua", "3", "1"]],
    "02 - Actividades": [
        ["actividad_id", "materia_id", "titulo", "fecha_entrega", "fecha_cierre",
         "estado_entrega", "estado_calificacion"],
        activity("ACT-VENC", "MAT-A", "2027-03-08 23:59"),
        activity("ACT-HOY", "MAT-B", "2027-03-10 23:59"),
        activity("ACT-SEMANA", "MAT-A", "2027-03-16 23:59"),
        activity("ACT-LUEGO", "MAT-A", "2027-03-20 23:59"),
        activity("ACT-OLD", "MAT-OLD", "2027-03-11 23:59")],
    "03 - Clases": [["clase_id", "materia_id", "fecha", "procesada", "titulo"],
                    ["CLS-A-LISTA", "MAT-A", serial("2027-03-08 18:00"), True, "Clase lista"],
                    ["CLS-A-SININDICE", "MAT-A", serial("2027-03-01 18:00"), True, "Clase sin índice"],
                    ["CLS-A-VIEJA", "MAT-A", serial("2027-01-20 18:00"), True, "Clase vieja"],
                    ["CLS-B-CHAT", "MAT-B", serial("2027-03-09 18:00"), False, "Anotada en el chat"],
                    ["CLS-A-FUTURA", "MAT-A", serial("2027-03-12 18:00"), False, "Futura"],
                    ["CLS-OLD", "MAT-OLD", serial("2027-03-02 18:00"), True, "Otro semestre"]],
    "05 - Semestres": [["semestre_id", "nombre", "estado", "bloque_activo", None],
                       ["2027-1", "Semestre 1 - 2027", "Activo", 1, None],
                       ["2026-2", "Semestre 2 - 2026", "Cerrado", 1, None]],
})


class WeekBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime = Path(tmp.name) / "runtime"
        (self.runtime / "control").mkdir(parents=True)
        source = Path(tmp.name) / "source.xlsx"
        write_sheets(source, SHEETS)
        self.db = self.runtime / store.DB_NAME
        store.migrate(source, self.db)
        records.apply(self.db, [{"tabla": "indices_clase", "accion": "guardar", "registro": {
            "clase_id": "CLS-A-LISTA", "materia_id": "MAT-A",
            "contenido": json.dumps({"version": 1, "resumen": "x", "temas": []})}}])
        state = {"status": "free", "lock": None, "published": {"sha256": sha256(self.db)}}
        (self.runtime / "control" / "database-work-state.json").write_text(json.dumps(state), encoding="utf-8")

    def job(self, n, subject, day, status="completed", registered=False, **request):
        work = self.runtime / "procesamiento" / (str(n) * 32)
        work.mkdir(parents=True)
        (work / "job.json").write_text(json.dumps({
            "job_id": str(n) * 32, "status": status, "created_at": f"2027-03-0{n}T10:00:00Z",
            "request": {"subject": subject, "class_date": day, "duration": None, **request},
            "source_metadata": {"original_name": f"Grabación {n}"},
            **({"database_registration": {"class_id": "CLS-X"}} if registered else {})}), encoding="utf-8")

    def week(self):
        return week.week(self.runtime, on=DAY)


class ClassStatesTest(WeekBase):
    def test_each_class_gets_its_state_and_next_step(self):
        self.job(1, "Álgebra", "2027-03-03", status="transcribing")
        self.job(2, "Biología", "2027-03-02", status="failed")
        self.job(3, "biología ", "2027-03-04")
        from datetime import datetime, timezone
        just_after = datetime(2027, 3, 1, 11, 0, tzinfo=timezone.utc)  # el trabajo 1 empezó hace una hora
        states = {c["clave"]: c for c in week.classes(self.runtime, on=DAY, now=just_after)["clases"]}
        self.assertEqual({k: v["estado"] for k, v in states.items()}, {
            "1" * 32: "procesando", "2" * 32: "con_error", "3" * 32: "falta_indice",
            "CLS-A-LISTA": "lista", "CLS-A-SININDICE": "falta_indice", "CLS-A-VIEJA": "falta_indice",
            "CLS-B-CHAT": "sin_transcripcion"})
        job = states["3" * 32]
        self.assertEqual((job["materia_id"], job["fecha"], job["titulo"], job["trabajo"], job["clase_id"]),
                         ("MAT-B", "2027-03-04", "Grabación 3", "3" * 32, None))
        self.assertIn("completar_clase", states["3" * 32]["siguiente_paso"])
        self.assertIn("guardar_indice_clase", states["CLS-A-SININDICE"]["siguiente_paso"])  # ya registrada
        self.assertIn("procese", states["CLS-B-CHAT"]["siguiente_paso"])
        self.assertEqual(states["CLS-A-LISTA"]["trabajo"], None)

    def test_a_running_job_that_stopped_advancing_is_an_error(self):
        from datetime import datetime, timezone
        self.job(1, "Álgebra", "2027-03-03", status="transcribing")
        self.job(2, "Biología", "2027-03-04", status="cancelled")
        for n, stamp in ((1, "2027-03-10T09:00:00Z"), (2, "2027-03-10T11:30:00.123456+00:00")):
            path = self.runtime / "procesamiento" / (str(n) * 32) / "job.json"
            path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "updated_at": stamp}),
                            encoding="utf-8")
        now = datetime(2027, 3, 10, 12, 30, tzinfo=timezone.utc)
        states = {c["clave"]: c["estado"] for c in week.classes(self.runtime, on=DAY, now=now)["clases"]}
        self.assertEqual((states["1" * 32], states["2" * 32]), ("con_error", "procesando"))

    def test_jobs_that_are_not_pending_classes_are_left_out(self):
        self.job(1, "Álgebra", "2027-03-05", registered=True)       # ya es una clase
        self.job(2, "Álgebra", "2027-03-01")                        # esa fecha ya está registrada
        self.job(3, "Álgebra", "2027-03-06", duration=300)          # fragmento de prueba
        self.job(4, "Álgebra", None, tipo="material")               # material de estudio
        self.job(5, "Antigua", "2027-03-06")                        # otro semestre
        self.job(6, "Desconocida", "2027-03-06")                    # materia que no existe
        keys = {c["clave"] for c in week.classes(self.runtime, on=DAY)["clases"]}
        self.assertFalse(keys & {str(n) * 32 for n in range(1, 7)})

    def test_the_newest_job_of_a_class_wins(self):
        self.job(1, "Álgebra", "2027-03-06", status="failed")
        self.job(2, "Álgebra", "2027-03-06")
        found = [c for c in week.classes(self.runtime, on=DAY)["clases"] if c["fecha"] == "2027-03-06"]
        self.assertEqual([(c["trabajo"], c["estado"]) for c in found], [("2" * 32, "falta_indice")])

    def test_classes_are_newest_first_and_future_ones_wait(self):
        found = week.classes(self.runtime, on=DAY)["clases"]
        self.assertEqual([c["fecha"] for c in found], sorted((c["fecha"] for c in found), reverse=True))
        self.assertNotIn("CLS-A-FUTURA", {c["clave"] for c in found})
        self.assertNotIn("CLS-OLD", {c["clave"] for c in found})


class FoundRecordingsTest(WeekBase):
    def found(self, n, subject, day, **entry):
        from academic_operations import auto_classes
        records = auto_classes.found(self.runtime)
        file_id = f"1SampleDriveFileIdForTests_0000{n:02d}"
        records[file_id] = {"file_id": file_id, "enlace": f"https://drive.google.com/file/d/{file_id}/view",
                            "materia_id": subject, "fecha": day, "fuente": "Grabaciones", "nombre": f"Grabación {n}",
                            "verificada": "2027-03-09T10:00:00Z", "descargable": True, "ignorada": False, **entry}
        auto_classes._save(self.runtime, records)
        return file_id

    def test_found_recordings_wait_in_line_or_need_the_browser_button(self):
        queued = self.found(1, "MAT-A", "2027-03-05")
        button = self.found(2, "MAT-B", "2027-03-06", descargable=False)
        self.found(3, "MAT-A", "2027-03-07", ignorada=True)            # no es un video
        self.found(4, "MAT-A", "2027-03-01")                           # esa clase ya está registrada
        self.found(5, "MAT-OLD", "2027-03-04")                         # otro semestre
        unverified = self.found(6, "MAT-A", "2027-03-03")
        from academic_operations import auto_classes
        records = auto_classes.found(self.runtime)
        del records[unverified]["verificada"]
        auto_classes._save(self.runtime, records)
        states = {c["clave"]: c for c in week.classes(self.runtime, on=DAY)["clases"] if c["clave"].startswith("1Sample")}
        self.assertEqual({k: v["estado"] for k, v in states.items()}, {queued: "en_cola", button: "solo_transcripcion"})
        self.assertEqual(states[button]["enlace"], f"https://drive.google.com/file/d/{button}/view")
        self.assertIn("Transcripción de Drive", states[button]["siguiente_paso"])
        self.assertEqual((states[queued]["titulo"], states[queued]["materia_id"]), ("Grabación 1", "MAT-A"))
        self.assertIn(queued, [c["clave"] for c in self.week()["clases"]])  # pendiente: va a «Mi semana»

    def test_a_recording_that_already_has_a_job_shows_only_the_job(self):
        file_id = self.found(1, "MAT-A", "2027-03-05")
        self.job(1, "Álgebra", "2027-03-05", status="transcribing")
        path = self.runtime / "procesamiento" / ("1" * 32) / "job.json"
        job = json.loads(path.read_text(encoding="utf-8"))
        job["source_metadata"]["file_id"] = file_id
        path.write_text(json.dumps(job), encoding="utf-8")
        keys = [c["clave"] for c in week.classes(self.runtime, on=DAY)["clases"]]
        self.assertIn("1" * 32, keys)
        self.assertNotIn(file_id, keys)


class CalendarScheduleTest(WeekBase):
    def schedule(self, *rows):
        from academic_operations import calendar_classes
        calendar_classes._save(self.runtime, [{"materia_id": s, "fecha": d, "inicio": "18:00", "fin": "19:00",
                                                "titulo": t, "grabaciones": []} for s, d, t in rows])

    def test_past_classes_of_the_schedule_without_recording_are_shown(self):
        self.job(1, "Biología", "2027-03-04")
        self.schedule(("MAT-A", "2027-03-06", "Sesión Álgebra"), ("MAT-B", "2027-03-04", "Sesión Biología"),
                      ("MAT-A", "2027-03-08", "Ya registrada"), ("MAT-A", "2027-03-12", "Futura"),
                      ("MAT-OLD", "2027-03-05", "Otro semestre"))
        found = {c["clave"]: c for c in week.classes(self.runtime, on=DAY)["clases"]}
        expected = [c for c in found.values() if c["estado"] == "sin_grabacion"]
        self.assertEqual([(c["materia_id"], c["fecha"], c["titulo"]) for c in expected],
                         [("MAT-A", "2027-03-06", "Sesión Álgebra")])
        self.assertIn("18:00", expected[0]["siguiente_paso"])
        self.assertIn(expected[0]["clave"], [c["clave"] for c in self.week()["clases"]])

    def test_a_class_of_today_is_not_missing_its_recording_yet(self):
        self.schedule(("MAT-A", DAY, "Clase de hoy"))
        states = [c["estado"] for c in week.classes(self.runtime, on=DAY)["clases"]]
        self.assertNotIn("sin_grabacion", states)


class HiddenTest(WeekBase):
    def test_a_job_removed_from_the_list_moves_aside_and_can_come_back(self):
        self.job(1, "Álgebra", "2027-03-03")
        work = self.runtime / "procesamiento" / ("1" * 32) / "job.json"
        before = work.read_bytes()
        self.assertEqual(week.set_hidden(self.runtime, "1" * 32, True), ["1" * 32])
        found = week.classes(self.runtime, on=DAY)
        self.assertNotIn("1" * 32, {c["clave"] for c in found["clases"]})
        self.assertEqual([c["clave"] for c in found["quitadas"]], ["1" * 32])
        self.assertNotIn("1" * 32, {c["clave"] for c in self.week()["clases"]})
        self.assertEqual(work.read_bytes(), before)  # el trabajo no cambia
        week.set_hidden(self.runtime, "1" * 32, False)
        self.assertIn("1" * 32, {c["clave"] for c in week.classes(self.runtime, on=DAY)["clases"]})

    def test_only_job_ids_are_accepted(self):
        for bad in ("../x", "CLS-A-LISTA", "1" * 31, None):
            with self.assertRaises(Exception):
                week.set_hidden(self.runtime, bad, True)
        self.assertEqual(week.hidden(self.runtime), set())

    def test_a_damaged_list_hides_nothing(self):
        path = self.runtime / week.HIDDEN
        path.parent.mkdir(parents=True)
        path.write_text("{no json", encoding="utf-8")
        self.assertEqual(week.hidden(self.runtime), set())


class WeekTest(WeekBase):
    def test_classes_to_attend_are_the_pending_ones_and_this_weeks_ready_ones(self):
        self.job(1, "Álgebra", "2027-03-03")
        attend = [c["clave"] for c in self.week()["clases"]]
        self.assertEqual(attend, ["CLS-B-CHAT", "CLS-A-LISTA", "1" * 32, "CLS-A-SININDICE"])
        # Lo viejo sin índice (más de 21 días) queda solo en «Clases»; lo listo de otras semanas también.

    def test_deliveries_of_the_next_7_days_overdue_first(self):
        result = self.week()
        self.assertEqual((result["fecha"], result["hasta"]), (DAY, "2027-03-16"))
        self.assertEqual([i["actividad_id"] for i in result["entregas"]], ["ACT-VENC", "ACT-HOY", "ACT-SEMANA"])
        self.assertIn("ACT-LUEGO", [i.get("actividad_id") for i in result["otros"]])
        self.assertNotIn("clase_sin_procesar", {i["tipo"] for i in result["otros"]})

    def test_counts_for_the_header(self):
        self.job(1, "Álgebra", "2027-03-03")
        self.assertEqual(self.week()["resumen"], {"clases_por_atender": 3, "clases_listas": 1, "entregas": 3,
                                                  "atrasadas": 1})


if __name__ == "__main__":
    unittest.main()
