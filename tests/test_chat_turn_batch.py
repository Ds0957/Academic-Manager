"""Escrituras del chat: turno de escritura con espera acotada (#9) y lotes con una aprobación (#10)."""
import json
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from academic_operations import chat
from academic_store import records, store
from capture.storage.job import job_lock
from capture.storage.metadata import sha256
from tests.test_chat_writes import ChatFixture


def recent():
    return datetime.now(timezone.utc).isoformat()


class TurnTests(ChatFixture):
    def setUp(self):
        super().setUp()
        for name, value in (("TURN_WAIT_SECONDS", 2.0), ("TURN_POLL_SECONDS", 0.02)):
            p = patch.object(chat, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.note = ("note", "--materia", "MAT-A", "--tipo", "decision", "--texto", "Parcial el jueves.")

    def plan(self, *args):
        code, body = self.run_cli(*args)
        self.assertEqual(code, 0, body)
        return body["data"]["plan_id"]

    def hold_turn(self, seconds):
        """Otro escritor del mismo PC tiene el turno durante `seconds`."""
        ready = threading.Event()

        def holder():
            with job_lock(chat._turn_dir(self.runtime.resolve())):
                ready.set()
                time.sleep(seconds)
        thread = threading.Thread(target=holder)
        thread.start()
        ready.wait()
        self.addCleanup(thread.join)
        return thread

    def test_busy_turn_times_out_as_store_busy_without_writing(self):
        plan_id = self.plan(*self.note)
        before = sha256(self.db)
        self.hold_turn(3.0)
        with self.drive():
            code, body = self.run_cli(*self.note, "--plan-id", plan_id, "--approve")
        self.assertEqual(code, 2)
        error = body["error"]
        self.assertEqual((error["code"], error["retryable"], error["publicado"]), ("STORE_BUSY", True, "no"))
        self.assertIn("mismo plan_id", error["suggested_action"])
        self.assertEqual(sha256(self.db), before)
        self.assertEqual(self.uploads, 0)
        self.assertEqual(records.read(self.db, "notas"), [])

    def test_write_waits_for_the_turn_and_then_publishes_with_the_same_plan(self):
        plan_id = self.plan(*self.note)
        self.hold_turn(0.3)
        with self.drive():
            code, body = self.run_cli(*self.note, "--plan-id", plan_id, "--approve")
        self.assertEqual(code, 0, body)
        self.assertTrue(body["data"]["published"])
        self.assertEqual(len(records.read(self.db, "notas")), 1)
        self.assertEqual(self.uploads, 1)

    def test_shared_lock_of_another_writer_is_waited_out_before_locking(self):
        plan_id = self.plan(*self.note)
        free = json.loads(json.dumps(self.shared_state))
        self.shared_state.update({"status": "editing", "lock": {"machine": "OTRO", "started_at": recent()}})
        timer = threading.Timer(0.3, lambda: (self.shared_state.clear(), self.shared_state.update(free)))
        timer.start()
        self.addCleanup(timer.cancel)
        with self.drive():
            code, body = self.run_cli(*self.note, "--plan-id", plan_id, "--approve")
        self.assertEqual(code, 0, body)
        self.assertEqual(self.uploads, 1)
        self.assertEqual(len(records.read(self.db, "notas")), 1)

    def test_preview_waits_while_the_local_state_is_being_edited(self):
        state_path = self.runtime / "control" / "database-work-state.json"
        free = state_path.read_text(encoding="utf-8")
        busy = json.loads(free)
        busy.update({"status": "editing", "lock": {"machine": "ESTE", "started_at": recent()}})
        state_path.write_text(json.dumps(busy), encoding="utf-8")
        timer = threading.Timer(0.3, lambda: state_path.write_text(free, encoding="utf-8"))
        timer.start()
        self.addCleanup(timer.cancel)
        code, body = self.run_cli(*self.note)
        self.assertEqual(code, 0, body)
        self.assertRegex(body["data"]["plan_id"], r"^[0-9a-f]{64}$")

    def test_a_lock_held_for_too_long_fails_at_once_as_needing_recovery(self):
        plan_id = self.plan(*self.note)
        old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        self.shared_state.update({"status": "editing", "lock": {"machine": "OTRO", "started_at": old}})
        started = time.monotonic()
        with patch.object(chat, "TURN_WAIT_SECONDS", 30.0), self.drive():
            code, body = self.run_cli(*self.note, "--plan-id", plan_id, "--approve")
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(code, 2)
        error = body["error"]
        self.assertEqual((error["code"], error["retryable"], error["publicado"]),
                         ("STORE_UNAVAILABLE", False, "no"))
        self.assertIn("recuperación supervisada", error["suggested_action"])
        self.assertEqual(self.uploads, 0)

    def test_a_local_edit_without_lock_details_is_not_waited_for(self):
        state_path = self.runtime / "control" / "database-work-state.json"
        busy = json.loads(state_path.read_text(encoding="utf-8"))
        busy.update({"status": "editing", "lock": None})
        state_path.write_text(json.dumps(busy), encoding="utf-8")
        with patch.object(chat, "TURN_WAIT_SECONDS", 30.0):
            started = time.monotonic()
            code, body = self.run_cli(*self.note)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual((code, body["error"]["code"]), (2, "STORE_UNAVAILABLE"))

    def test_two_simultaneous_writes_both_publish_once(self):
        first = self.note
        second = ("task", "--titulo", "Preparar parcial", "--materia", "MAT-A")
        plans = [self.plan(*first), self.plan(*second)]
        results = {}

        def run(name, args, plan_id):
            results[name] = chat.apply(self.runtime, args[0], self.request(args), plan_id=plan_id,
                                       approved=True, index_builder=lambda runtime: time.sleep(0.05))
        with self.drive():
            threads = [threading.Thread(target=run, args=("a", first, plans[0])),
                       threading.Thread(target=run, args=("b", second, plans[1]))]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertTrue(results["a"]["published"] and results["b"]["published"])
        self.assertEqual(self.uploads, 2)
        self.assertEqual((len(records.read(self.db, "notas")), len(records.read(self.db, "tareas"))), (1, 1))
        self.assertEqual(sha256(self.db), self.shared_state["published"]["sha256"])

    def request(self, args):
        from academic_operations.__main__ import CHAT_OPTIONS
        request = dict.fromkeys(CHAT_OPTIONS.values())
        pairs = iter(args[1:])
        request.update({CHAT_OPTIONS[k]: v for k, v in zip(pairs, pairs)})
        return request

    def test_failure_after_the_change_was_prepared_is_reported_as_uncertain(self):
        plan_id = self.plan(*self.note)
        with self.drive(), patch("canvas_sync.workbook_blocks.drive.upload",
                                 side_effect=OSError("red caída")):
            code, body = self.run_cli(*self.note, "--plan-id", plan_id, "--approve")
        self.assertEqual(code, 2)
        self.assertEqual(body["error"]["publicado"], "incierto")
        self.assertIn("summary", body["error"]["suggested_action"])

    def test_failure_after_locking_but_before_writing_is_reported_as_uncertain(self):
        plan_id = self.plan(*self.note)
        real_copy = shutil.copyfile

        def copy(src, dst, *a, **kw):  # solo falla la copia de seguridad, ya con el lock tomado
            if str(dst).endswith("-before-blocks.sqlite"):
                raise OSError("disco lleno")
            return real_copy(src, dst, *a, **kw)
        with self.drive(), patch("canvas_sync.workbook_blocks.shutil.copyfile", side_effect=copy):
            code, body = self.run_cli(*self.note, "--plan-id", plan_id, "--approve")
        self.assertEqual(code, 2)
        self.assertEqual(self.shared_state["status"], "editing")  # el lock queda para recuperación
        self.assertEqual(body["error"]["publicado"], "incierto")
        self.assertIn("summary", body["error"]["suggested_action"])

    def test_failure_before_any_change_is_reported_as_not_published(self):
        plan_id = self.plan(*self.note)
        self.shared_state["published"]["sha256"] = "otro"
        with self.drive():
            code, body = self.run_cli(*self.note, "--plan-id", plan_id, "--approve")
        self.assertEqual(code, 2)
        self.assertEqual((body["error"]["code"], body["error"]["publicado"]), ("PUBLISH_FAILED", "no"))


class BatchTests(ChatFixture):
    def write_input(self, content):
        path = Path(self.tmp.name) / "lote.json"
        path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def batch(self, *ops):
        return self.write_input({"operaciones": list(ops)})

    def test_batch_previews_every_item_with_one_plan_and_publishes_once(self):
        path = self.batch(
            {"accion": "note", "materia": "MAT-A", "tipo": "decision", "texto": "Parcial el jueves & 30 %."},
            {"accion": "note", "materia": "MAT-B", "tipo": "aclaracion", "texto": "Trabajo en parejas."},
            {"accion": "task", "titulo": "Estudiar tema 4", "materia": "MAT-A", "fecha_limite": "2027-03-01"},
            {"accion": "track", "actividad": "ACT-C9202-A2", "estado": "entregada"})
        before, revision = sha256(self.db), store.revision(self.db)
        code, body = self.run_cli("batch", "--input", path)
        self.assertEqual(code, 0, body)
        data = body["data"]
        self.assertEqual([op["accion"] for op in data["operation"]["operaciones"]],
                         ["note", "note", "task", "track"])
        self.assertEqual(sha256(self.db), before)
        with self.drive():
            code, body = self.run_cli("batch", "--input", path, "--plan-id", data["plan_id"], "--approve")
        self.assertEqual(code, 0, body)
        self.assertTrue(body["data"]["published"])
        self.assertEqual(self.uploads, 1)
        self.assertEqual(store.revision(self.db), revision + 1)
        self.assertEqual(sorted(r["texto"] for r in records.read(self.db, "notas")),
                         ["Parcial el jueves & 30 %.", "Trabajo en parejas."])
        self.assertEqual([r["titulo"] for r in records.read(self.db, "tareas")], ["Estudiar tema 4"])
        self.assertEqual(records.find(self.db, "seguimiento", "ACT-C9202-A2")["estado"], "entregada")
        with self.drive():
            code, body = self.run_cli("batch", "--input", path, "--plan-id", data["plan_id"], "--approve")
        self.assertEqual((code, body["data"]["already_applied"], self.uploads), (0, True, 1))

    def test_one_invalid_item_rejects_the_whole_batch(self):
        path = self.batch(
            {"accion": "note", "materia": "MAT-A", "tipo": "nota", "texto": "Válida."},
            {"accion": "note", "materia": "MAT-ZZZ", "tipo": "nota", "texto": "Materia inexistente."})
        before = sha256(self.db)
        code, body = self.run_cli("batch", "--input", path)
        self.assertEqual(code, 2)
        self.assertEqual(body["error"]["code"], "NOT_FOUND")
        self.assertIn("Operación 2", body["error"]["message"])
        self.assertFalse(body["error"]["retryable"])
        self.assertEqual(sha256(self.db), before)

    def test_malformed_batches_are_invalid_arguments(self):
        note = {"accion": "note", "materia": "MAT-A", "tipo": "nota", "texto": "x"}
        cases = {
            "sin lista": {"operaciones": "x"},
            "vacía": {"operaciones": []},
            "demasiadas": {"operaciones": [{**note, "texto": str(i)} for i in range(21)]},
            "clave extra": {"operaciones": [note], "otra": 1},
            "sin acción": {"operaciones": [{k: v for k, v in note.items() if k != "accion"}]},
            "acción no admitida": {"operaciones": [{"accion": "class", "materia": "MAT-A",
                                                    "fecha": "2027-01-01", "titulo": "x"}]},
            "clave desconocida": {"operaciones": [{**note, "color": "rojo"}]},
            "repetida": {"operaciones": [note, note]},
        }
        for name, content in cases.items():
            with self.subTest(name):
                code, body = self.run_cli("batch", "--input", self.write_input(content))
                self.assertEqual((code, body["error"]["code"]), (2, "INVALID_ARGUMENT"), body)
        code, body = self.run_cli("batch", "--materia", "MAT-A")
        self.assertEqual((code, body["error"]["code"]), (2, "INVALID_ARGUMENT"))

    def test_single_note_still_has_its_own_plan(self):
        code, single = self.run_cli("note", "--materia", "MAT-A", "--tipo", "nota", "--texto", "x")
        self.assertEqual(code, 0)
        self.assertEqual(single["data"]["operation"]["accion"], "note")
