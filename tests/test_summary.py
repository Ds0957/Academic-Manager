"""`academic_access summary`: resumen funcional para empezar un chat (S6a)."""
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from academic_access.__main__ import main
from academic_store import records, store
from capture.storage.metadata import sha256
from canvas_sync.sync import sha
from tests.test_today import SHEETS, TODAY, task
from tests.xlsx_fixture import write_sheets

TOKEN_BYTES = b"SECRET-TOKEN-CIPHERTEXT-0123456789"


def note(nid, subject, kind, text, stamp):
    return ({"tabla": "notas", "accion": "insertar", "registro": {
        "nota_id": nid, "materia_id": subject, "tipo": kind, "texto": text}}, nid, stamp)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name) / "runtime"
        (self.runtime / "control").mkdir(parents=True)
        source = Path(self.tmp.name) / "source.xlsx"
        write_sheets(source, SHEETS)
        self.db = self.runtime / store.DB_NAME
        store.migrate(source, self.db)
        notes = [note("NOTA-1", "MAT-A", "nota", "uno", "2027-03-01T15:00:00Z"),
                 note("NOTA-2", "MAT-B", "decision", "x" * 250, "2027-03-09T15:00:00Z"),
                 note("NOTA-3", "MAT-A", "aclaracion", "tres", "2027-03-05T15:00:00Z"),
                 note("NOTA-4", "MAT-C", "nota", "cuatro", "2027-03-07T15:00:00Z"),
                 note("NOTA-5", "MAT-A", "nota", "cinco", "2027-03-07T15:00:00Z"),
                 # 03:00 UTC del 10 = 22:00 del 9 en Bogotá.
                 note("NOTA-6", "MAT-D", "nota", "seis", "2027-03-10T03:00:00Z")]
        tasks = [task(f"TAR-{n:02d}", "pendiente", f"2027-03-{n:02d}") for n in range(11, 23)]
        tasks += [task("TAR-SINF", "en_progreso", None), task("TAR-HECHA", "hecha", "2027-03-01"),
                  task("TAR-DESC", "descartada", "2027-03-01")]
        records.apply(self.db, [n[0] for n in notes] + tasks)
        conn = sqlite3.connect(self.db)
        with conn:
            for _, nid, stamp in notes:
                conn.execute("UPDATE notas SET creado_utc = ? WHERE nota_id = ?", (stamp, nid))
        conn.close()
        self.publish_state()

    def publish_state(self, digest=None, modified="2027-03-09T20:00:00.000Z"):
        state = {"status": "free", "lock": None,
                 "published": {"sha256": digest or sha256(self.db), "drive_modified_time": modified}}
        (self.runtime / "control" / "database-work-state.json").write_text(
            json.dumps(state), encoding="utf-8")

    def write_index(self, workbook_sha):
        folder = self.runtime / "procesamiento" / "academic-knowledge"
        folder.mkdir(parents=True)
        data = {"workbook_sha256": workbook_sha, "materias": {}, "actividades": [],
                "fuentes": {}, "relaciones": []}
        (folder / "index.json").write_text(json.dumps(
            {"sha256": sha(data), "generado_utc": "2027-03-09T12:00:00Z", "data": data}),
            encoding="utf-8")

    def write_sync_log(self, code=0):
        folder = self.runtime / "procesamiento" / "canvas-sync" / "logs"
        folder.mkdir(parents=True)
        (folder / "latest-status.json").write_text(
            json.dumps({"timestamp": "2027-03-10 06:00:00", "exit_code": code}), encoding="utf-8")

    def write_token(self):
        folder = self.runtime / "control" / "canvas-sync"
        folder.mkdir(parents=True)
        (folder / "canvas-token.dpapi").write_bytes(TOKEN_BYTES)

    def read(self, *args):
        out = io.StringIO()
        with patch.dict(os.environ, {"ACADEMIC_MANAGER_DATA": str(self.runtime)}), redirect_stdout(out):
            code = main(list(args))
        return code, out.getvalue()

    def summary(self, *args):
        code, raw = self.read("summary", "--fecha", TODAY, *args)
        body = json.loads(raw)
        self.assertEqual(code, 0, body)
        self.assertEqual((body["ok"], body["schema_version"], body["action"]), (True, 1, "summary"))
        return body["data"]

    def failure(self, *args):
        code, raw = self.read(*args)
        body = json.loads(raw)
        self.assertEqual(code, 2)
        self.assertFalse(body["ok"])
        return body

    def test_semester_and_active_subjects_with_last_class(self):
        data = self.summary()
        self.assertEqual(data["fecha"], TODAY)
        self.assertEqual(data["semestre"], {"semestre_id": "2027-1", "nombre": "Semestre 1 - 2027",
                                            "bloque_activo": 2})
        self.assertEqual(data["materias"], [
            {"materia_id": "MAT-A", "nombre": "A", "ultima_clase": "2027-03-09"},
            {"materia_id": "MAT-B", "nombre": "B", "ultima_clase": None},
            {"materia_id": "MAT-C", "nombre": "C", "ultima_clase": None},
            {"materia_id": "MAT-D", "nombre": "D", "ultima_clase": None},
            {"materia_id": "MAT-E", "nombre": "E", "ultima_clase": None}])

    def test_urgent_is_the_first_five_items_of_today(self):
        data = self.summary()
        code, raw = self.read("today", "--fecha", TODAY)
        today = json.loads(raw)["data"]
        self.assertEqual(data["urgente"], {"items": today["items"][:5], "resumen": today["resumen"]})
        self.assertGreater(len(today["items"]), 5)

    def test_recent_notes_and_open_tasks(self):
        data = self.summary()
        notes = data["reciente"]["notas"]
        self.assertEqual([n["nota_id"] for n in notes], ["NOTA-6", "NOTA-2", "NOTA-4", "NOTA-5", "NOTA-3"])
        self.assertEqual(notes[0], {"nota_id": "NOTA-6", "materia_id": "MAT-D", "tipo": "nota",
                                    "texto": "seis", "fecha": "2027-03-09"})
        self.assertEqual(notes[1]["texto"], "x" * 200)
        tasks = data["reciente"]["tareas"]
        self.assertEqual([t["tarea_id"] for t in tasks], [f"TAR-{n:02d}" for n in range(11, 21)])
        self.assertEqual(tasks[0], {"tarea_id": "TAR-11", "titulo": "Tarea TAR-11", "materia_id": "MAT-A",
                                    "fecha_limite": "2027-03-11", "estado": "pendiente"})

    def test_recent_notes_and_open_tasks_only_from_the_active_semester(self):
        # Issue #16: lo de semestres cerrados no se muestra; las tareas sin materia, sí.
        records.apply(self.db, [note("NOTA-OLD", "MAT-OLD", "decision", "vieja", None)[0],
                                task("TAR-OLD", "pendiente", "2027-03-01", subject="MAT-OLD"),
                                task("TAR-LIBRE", "pendiente", "2027-03-02", subject=None)])
        conn = sqlite3.connect(self.db)
        with conn:
            conn.execute("UPDATE notas SET creado_utc = '2027-03-10T12:00:00Z' WHERE nota_id = 'NOTA-OLD'")
        conn.close()
        self.publish_state()
        recent = self.summary()["reciente"]
        self.assertEqual([n["nota_id"] for n in recent["notas"]],
                         ["NOTA-6", "NOTA-2", "NOTA-4", "NOTA-5", "NOTA-3"])
        self.assertEqual([t["tarea_id"] for t in recent["tareas"]],
                         ["TAR-LIBRE"] + [f"TAR-{n:02d}" for n in range(11, 20)])
        self.assertIsNone(recent["tareas"][0]["materia_id"])

    def test_open_tasks_without_deadline_come_last(self):
        conn = sqlite3.connect(self.db)
        with conn:
            conn.execute("DELETE FROM tareas WHERE tarea_id >= 'TAR-15' AND tarea_id < 'TAR-SINF'")
        conn.close()
        self.publish_state()
        tasks = self.summary()["reciente"]["tareas"]
        self.assertEqual([t["tarea_id"] for t in tasks], ["TAR-11", "TAR-12", "TAR-13", "TAR-14", "TAR-SINF"])

    def test_system_section_with_all_sources(self):
        self.write_index(sha256(self.db))
        self.write_sync_log()
        sistema = self.summary()["sistema"]
        self.assertEqual(sistema["ultimo_canvas_sync"], {"status": "ok", "timestamp_local": "2027-03-10 06:00:00",
                                                         "exit_code": 0})
        self.assertEqual(sistema["indice"], {"fresh": True, "generated_at_utc": "2027-03-09T12:00:00Z"})
        self.assertEqual(sistema["publicado"], {"sha256": sha256(self.db)[:12],
                                                "drive_modified_time": "2027-03-09T20:00:00.000Z",
                                                "revision": store.info(self.db)["revision"]})

    def write_daily_logs(self, runs, message="Error operacional: Canvas Sync: El registro local es más antiguo."):
        """runs: [(\"YYYY-MM-DD HH:MM:SS\", exit_code)] en orden; un archivo por día como canvas-daily.ps1."""
        folder = self.runtime / "procesamiento" / "canvas-sync" / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        by_day = {}
        for stamp, code in runs:
            by_day.setdefault(stamp[:10], []).append(f"[{stamp}] exit_code={code}\r\n"
                                                     + (message if code else "Rutina OK") + "\r\n")
        for day, entries in by_day.items():
            (folder / f"canvas-{day}.log").write_text("\ufeff" + "".join(entries), encoding="utf-8")
        stamp, code = runs[-1]
        (folder / "latest-status.json").write_text(json.dumps(
            {"timestamp": stamp, "exit_code": code,
             "message": message if code else "ok", }), encoding="utf-8")

    def test_consecutive_canvas_failures_become_the_first_warning(self):
        self.write_daily_logs([("2027-03-07 18:00:01", 0), ("2027-03-08 08:00:01", 1),
                               ("2027-03-08 18:00:01", 1), ("2027-03-09 08:00:02", 1),
                               ("2027-03-10 08:00:02", 1)],
                              message="Error operacional: Canvas Sync: fallo en C:\\AcademicManagerData\\x.json")
        data = self.summary()
        self.assertEqual(list(data)[0], "avisos")
        history = data["sistema"]["canvas_sync_historial"]
        self.assertEqual((history["ultimo_ok"], history["fallos_seguidos"], history["fallando_desde"]),
                         ("2027-03-07 18:00:01", 4, "2027-03-08 08:00:01"))
        self.assertNotIn("AcademicManagerData", history["ultimo_error"])
        [warning] = [a for a in data["avisos"] if "Canvas" in a]
        self.assertIn("4 intentos seguidos", warning)
        self.assertIn("2027-03-08", warning)
        self.assertEqual(data["sistema"]["ultimo_canvas_sync"],
                         {"status": "error", "timestamp_local": "2027-03-10 08:00:02", "exit_code": 1})

    def test_a_single_failure_or_success_raises_no_canvas_warning(self):
        self.write_daily_logs([("2027-03-09 18:00:01", 0), ("2027-03-10 08:00:01", 1)])
        data = self.summary()
        self.assertEqual(data["sistema"]["canvas_sync_historial"]["fallos_seguidos"], 1)
        self.assertEqual([a for a in data["avisos"] if "Canvas" in a], [])

    def test_a_daily_task_that_stopped_running_is_warned(self):
        self.write_daily_logs([("2027-03-06 18:00:01", 0)])
        [warning] = [a for a in self.summary()["avisos"] if "Canvas" in a]
        self.assertIn("no se ejecuta desde 2027-03-06", warning)

    def test_stale_index_and_token_warnings_are_listed(self):
        self.write_index("0" * 64)
        avisos = self.summary()["avisos"]
        self.assertTrue(any("índice" in a for a in avisos), avisos)

    def test_no_logs_is_unknown_history_without_inventing_warnings(self):
        data = self.summary()
        self.assertEqual(data["sistema"]["canvas_sync_historial"],
                         {"ultimo_ok": None, "fallos_seguidos": None, "fallando_desde": None,
                          "ultimo_intento": None, "ultimo_error": None})
        self.assertEqual([a for a in data["avisos"] if "Canvas" in a], [])

    def test_stale_index_is_reported_not_failed(self):
        self.write_index("0" * 64)
        self.assertEqual(self.summary()["sistema"]["indice"]["fresh"], False)

    def test_missing_optional_sources_degrade_to_null(self):
        sistema = self.summary()["sistema"]
        self.assertEqual(sistema["indice"]["fresh"], "unknown")
        self.assertIsNone(sistema["indice"]["generated_at_utc"])
        self.assertEqual(sistema["ultimo_canvas_sync"]["status"], "unknown")
        token = sistema["token_canvas"]
        self.assertEqual((token["vence"], token["dias_restantes"]), (None, None))
        self.assertTrue(token["motivo"])
        self.publish_state(modified=None)
        self.assertIsNone(self.summary()["sistema"]["publicado"]["drive_modified_time"])

    def test_token_expiry_is_not_read_from_the_encrypted_credential(self):
        self.write_token()
        code, raw = self.read("summary", "--fecha", TODAY)
        self.assertEqual(code, 0)
        token = json.loads(raw)["data"]["sistema"]["token_canvas"]
        self.assertEqual((token["vence"], token["dias_restantes"]), (None, None))
        self.assertTrue(token["motivo"])
        self.assertNotIn(TOKEN_BYTES.decode(), raw)

    def write_meta(self, content):
        folder = self.runtime / "control" / "canvas-sync"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "token-meta.json").write_text(content, encoding="utf-8")

    def test_token_expiry_is_read_from_the_non_secret_metadata(self):
        self.write_token()
        self.write_meta(json.dumps({"schema_version": 1, "expires_at": "2027-05-01T23:30:00-05:00",
                                    "saved_at": "2027-02-01T10:00:00+00:00"}))
        self.assertEqual(self.summary()["sistema"]["token_canvas"],
                         {"vence": "2027-05-01", "dias_restantes": 52, "aviso": None, "motivo": None})
        self.write_meta(json.dumps({"schema_version": 1, "expires_at": "2027-03-20T23:30:00-05:00",
                                    "saved_at": "2027-02-01T10:00:00+00:00"}))
        token = self.summary()["sistema"]["token_canvas"]
        self.assertEqual((token["vence"], token["dias_restantes"]), ("2027-03-20", 10))
        self.assertIn("10 días", token["aviso"])

    def test_token_that_expired_earlier_today_is_reported_as_expired(self):
        from datetime import datetime, timedelta
        self.write_token()
        expired = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
        self.write_meta(json.dumps({"schema_version": 1, "expires_at": expired,
                                    "saved_at": "2027-02-01T10:00:00+00:00"}))
        code, raw = self.read("summary")
        self.assertEqual(code, 0, raw)
        self.assertIn("vencido", json.loads(raw)["data"]["sistema"]["token_canvas"]["aviso"])

    def test_malformed_token_metadata_degrades_to_null_with_reason(self):
        self.write_token()
        for content in ("{broken", json.dumps({"schema_version": 1, "expires_at": "mañana"})):
            self.write_meta(content)
            token = self.summary()["sistema"]["token_canvas"]
            self.assertEqual((token["vence"], token["dias_restantes"], token["aviso"]), (None, None, None))
            self.assertIn("ilegibles", token["motivo"])

    def test_token_lookup_creates_no_directories(self):
        self.summary()
        self.assertFalse((self.runtime / "control" / "canvas-sync").exists())

    def test_output_has_no_local_paths(self):
        self.write_index(sha256(self.db))
        self.write_sync_log()
        self.write_token()
        code, raw = self.read("summary", "--fecha", TODAY)
        self.assertEqual(code, 0)
        for fragment in (str(self.runtime), self.tmp.name, "\\\\", "/home", "C:"):
            self.assertNotIn(fragment, raw)

    def test_deterministic(self):
        self.assertEqual(self.summary(), self.summary())

    def test_gate_fails_when_store_differs_from_published_hash(self):
        self.publish_state("0" * 64)
        body = self.failure("summary", "--fecha", TODAY)
        self.assertEqual((body["action"], body["error"]["code"]), ("summary", "SOURCE_UNAVAILABLE"))

    def test_invalid_fecha_and_options_of_other_actions(self):
        for args in (("--fecha", "2027-02-30"), ("--dias", "7"), ("--tipo", "nota"),
                     ("--estado", "pendiente")):
            with self.subTest(args=args):
                body = self.failure("summary", *args)
                self.assertEqual(body["error"]["code"], "INVALID_ARGUMENT")

    def test_fecha_is_still_rejected_on_other_actions(self):
        for args in (("notes", "--fecha", TODAY), ("health", "--fecha", TODAY),
                     ("capabilities", "--fecha", TODAY)):
            with self.subTest(args=args):
                body = self.failure(*args)
                self.assertEqual((body["action"], body["error"]["code"]), ("unknown", "INVALID_ARGUMENT"))

    def test_today_keeps_its_options(self):
        code, raw = self.read("today", "--fecha", TODAY, "--dias", "7")
        self.assertEqual(code, 0, raw)
        self.assertEqual(json.loads(raw)["data"]["dias"], 7)

    def test_capabilities_lists_summary(self):
        code, raw = self.read("capabilities")
        self.assertEqual(code, 0)
        self.assertIn("summary", json.loads(raw)["data"]["commands"])


if __name__ == "__main__":
    unittest.main()
