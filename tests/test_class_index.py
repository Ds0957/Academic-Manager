"""Índice de la clase (odd/tasks/indice-clase.md, S2–S4): validación, minutos reales y guardado."""
import contextlib
import copy
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from academic_access import core
from academic_access.core import AccessError, transcript_segments
from academic_operations import chat, class_index
from academic_operations.actions import OperationError
from academic_store import records, store
from tests.test_chat_writes import ChatFixture

CLASS = "CLS-A-20271015-01"
TRANSCRIPT = ("# Transcripción\n\nFuente: Clase\n\nTiempos referidos al medio original.\n\n"
              "[00:04:12–00:04:40] Hoy les explico las instrucciones de la actividad del eje 3.\n\n"
              "[00:20:05–00:20:31] La entrega es en parejas\ny se sube a Canvas.\n\n"
              "[01:20:18–01:20:44] Recuerden la fecha de entrega del próximo viernes.\n")


def fake_source_text(runtime, source_id, *, subject):
    if source_id == f"clase:{CLASS}:transcripcion" and subject == "MAT-A":
        return TRANSCRIPT
    raise AccessError("NOT_FOUND", "Fuente no registrada para esta materia.")


def sample_index():
    return {
        "resumen": "Se explicó la actividad del eje 3 y cómo entregarla.",
        "temas": [
            {"titulo": "Procesos", "momentos": [
                {"inicio": "00:20:05", "texto": "Entrega", "cita": "La entrega es en parejas y se sube"}]},
            {"titulo": "Instrucciones de la actividad", "categoria": "instrucciones", "momentos": [
                {"inicio": "01:20:18", "texto": "Fecha", "cita": "fecha de entrega del próximo viernes"},
                {"inicio": "00:04:12", "texto": "Explica", "cita": "las instrucciones de la actividad"}]},
        ],
    }


class TranscriptSegmentsTests(unittest.TestCase):
    def test_reads_times_and_joins_lines_of_a_fragment(self):
        found = transcript_segments(TRANSCRIPT)
        self.assertEqual([s["inicio"] for s in found], ["00:04:12", "00:20:05", "01:20:18"])
        self.assertEqual(found[1]["fin"], "00:20:31")
        self.assertEqual(found[1]["texto"], "La entrega es en parejas y se sube a Canvas.")

    def test_transcript_without_times_has_no_fragments(self):
        self.assertEqual(transcript_segments("# Transcripción\n\nTexto sin tiempos."), [])


class NormalizeTests(unittest.TestCase):
    def test_keeps_topic_order_defaults_category_and_sorts_moments_by_time(self):
        content = class_index.normalize(sample_index())
        self.assertEqual(content["version"], 1)
        self.assertEqual([t["titulo"] for t in content["temas"]], ["Procesos", "Instrucciones de la actividad"])
        self.assertEqual(content["temas"][0]["categoria"], "tema")
        self.assertEqual([m["inicio"] for m in content["temas"][1]["momentos"]], ["00:04:12", "01:20:18"])
        self.assertEqual(class_index.normalize(content), content)

    def assert_invalid(self, index, words):
        with self.assertRaises(OperationError) as caught:
            class_index.normalize(index)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertIn(words, str(caught.exception))

    def test_rejects_invalid_documents_with_plain_messages(self):
        for change, words in (
                (lambda i: i.pop("resumen"), "resumen"),
                (lambda i: i.update(resumen="x" * 901), "resumen"),
                (lambda i: i.update(temas=[]), "temas"),
                (lambda i: i.update(temas=i["temas"] * 21), "temas"),
                (lambda i: i.update(otro=1), "otro"),
                (lambda i: i["temas"][0].update(categoria="otra"), "categoria"),
                (lambda i: i["temas"][0].update(momentos=[]), "momentos"),
                (lambda i: i["temas"][0]["momentos"][0].update(inicio="4:12"), "HH:MM:SS"),
                (lambda i: i["temas"][0]["momentos"][0].update(inicio="00:61:00"), "HH:MM:SS"),
                (lambda i: i["temas"][0]["momentos"][0].update(cita="corta"), "12"),
                (lambda i: i["temas"][0]["momentos"][0].update(cita="x" * 301), "cita"),
                (lambda i: i["temas"][0]["momentos"].append(dict(i["temas"][0]["momentos"][0])), "repite")):
            index = copy.deepcopy(sample_index())
            change(index)
            with self.subTest(words=words):
                self.assert_invalid(index, words)


class MomentsCheckTests(unittest.TestCase):
    def check(self, index):
        class_index.check_moments(TRANSCRIPT, class_index.normalize(index))

    def test_real_moments_pass_whatever_the_spacing(self):
        self.check(sample_index())

    def test_a_time_that_is_not_the_start_of_a_fragment_fails_naming_topic_and_time(self):
        index = sample_index()
        index["temas"][0]["momentos"][0]["inicio"] = "00:20:06"
        with self.assertRaises(OperationError) as caught:
            self.check(index)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertIn("Procesos", str(caught.exception))
        self.assertIn("00:20:06", str(caught.exception))

    def test_a_quote_from_another_fragment_fails(self):
        index = sample_index()
        index["temas"][0]["momentos"][0]["cita"] = "las instrucciones de la actividad"
        with self.assertRaises(OperationError) as caught:
            self.check(index)
        self.assertIn("00:20:05", str(caught.exception))

    def test_a_transcript_without_times_cannot_have_an_index(self):
        with self.assertRaises(OperationError) as caught:
            class_index.check_moments("Texto sin tiempos.", class_index.normalize(sample_index()))
        self.assertIn("tiempos", str(caught.exception))


class ClassIndexFixture(ChatFixture):
    def setUp(self):
        super().setUp()
        patcher = patch("academic_operations.class_index.source_text", side_effect=fake_source_text)
        patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, index=None):
        return {"clase_id": CLASS, "indice": index or sample_index()}

    def save(self, index=None):
        found = chat.preview(self.runtime, "class-index", self.request(index))
        with self.drive():
            return found, chat.apply(self.runtime, "class-index", self.request(index), plan_id=found["plan_id"],
                                     approved=True, index_builder=lambda _: None)

    def rows(self):
        return records.read(Path(self.runtime) / store.DB_NAME, "indices_clase")


class ClassIndexWriteTests(ClassIndexFixture):
    def test_preview_details_what_will_be_saved_and_writes_nothing(self):
        found = chat.preview(self.runtime, "class-index", self.request())
        self.assertEqual(found["detalle"], {"clase_id": CLASS, "materia_id": "MAT-A", "temas": 2,
                                            "momentos": 3, "reemplaza": False, "momentos_anteriores": 0})
        self.assertTrue(found["requires_explicit_approval"])
        self.assertEqual(self.rows(), [])

    def test_approved_save_stores_canonical_content_with_its_subject(self):
        self.save()
        row = self.rows()[0]
        self.assertEqual((row["clase_id"], row["materia_id"]), (CLASS, "MAT-A"))
        self.assertEqual(json.loads(row["contenido"]), class_index.normalize(sample_index()))
        self.assertRegex(row["actualizado_utc"], r"Z$")

    def test_saving_again_replaces_the_index_and_says_so(self):
        self.save()
        changed = sample_index()
        changed["temas"].pop(0)
        found, _ = self.save(changed)
        self.assertTrue(found["detalle"]["reemplaza"])
        self.assertEqual(found["detalle"]["momentos_anteriores"], 3)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(len(json.loads(self.rows()[0]["contenido"])["temas"]), 1)

    def test_same_index_is_already_applied(self):
        self.save()
        self.assertTrue(chat.preview(self.runtime, "class-index", self.request())["already_applied"])

    def test_unknown_class_is_not_found(self):
        with self.assertRaises(OperationError) as caught:
            chat.preview(self.runtime, "class-index", {"clase_id": "CLS-X-1", "indice": sample_index()})
        self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_class_without_indexed_transcript_fails_plainly(self):
        with patch("academic_operations.class_index.source_text",
                   side_effect=AccessError("NOT_FOUND", "x")), self.assertRaises(OperationError) as caught:
            chat.preview(self.runtime, "class-index", self.request())
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertIn("transcripción", str(caught.exception))

    def test_invented_moment_writes_nothing(self):
        index = sample_index()
        index["temas"][0]["momentos"][0]["cita"] = "esto nunca lo dijo el profesor"
        with self.assertRaises(OperationError):
            chat.preview(self.runtime, "class-index", self.request(index))
        self.assertEqual(self.rows(), [])

    def test_approval_is_required(self):
        with self.assertRaises(OperationError) as caught:
            chat.apply(self.runtime, "class-index", self.request(), plan_id="a" * 64, approved=False)
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(self.rows(), [])

    def test_old_database_without_the_table_reads_empty(self):
        with contextlib.closing(sqlite3.connect(Path(self.runtime) / store.DB_NAME)) as conn, conn:
            conn.execute("DROP TABLE IF EXISTS indices_clase")
        self.assertEqual(self.rows(), [])


class ClassIndexReadTests(ClassIndexFixture):
    def test_reader_says_when_there_is_no_index(self):
        found = core.class_index(self.runtime, CLASS)
        self.assertIsNone(found["indice"])
        self.assertEqual(found["materia_id"], "MAT-A")

    def test_reader_returns_the_saved_index(self):
        self.save()
        found = core.class_index(self.runtime, CLASS)
        self.assertEqual(found["indice"], class_index.normalize(sample_index()))
        self.assertEqual(found["titulo"], "Clase grabada")

    def test_unknown_class_is_not_found(self):
        with self.assertRaises(AccessError) as caught:
            core.class_index(self.runtime, "CLS-X-1")
        self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_subject_list_has_only_classes_with_index_and_counts_them(self):
        self.assertEqual(core.class_indexes(self.runtime, "MAT-A"), [])
        self.save()
        self.assertEqual(core.class_indexes(self.runtime, "MAT-A"),
                         [{"clase_id": CLASS, "titulo": "Clase grabada", "fecha": "2027-10-15",
                           "temas": 2, "momentos": 3}])


class ClassIndexCliTests(ClassIndexFixture):
    def test_cli_reads_the_index_from_input_file(self):
        path = Path(self.tmp.name) / "indice.json"
        path.write_text(json.dumps({"clase": CLASS, "indice": sample_index()}, ensure_ascii=False), encoding="utf-8")
        from academic_operations.__main__ import _read_input
        self.assertEqual(_read_input(str(path), "class-index"), self.request())

    def test_cli_accepts_more_than_the_note_limit_and_rejects_unknown_keys(self):
        from academic_operations.__main__ import MAX_INPUT_BYTES, _read_input
        path = Path(self.tmp.name) / "indice.json"
        big = {"clase": CLASS, "indice": {"resumen": "x" * (MAX_INPUT_BYTES + 10)}}
        path.write_text(json.dumps(big), encoding="utf-8")
        self.assertEqual(len(_read_input(str(path), "class-index")["indice"]["resumen"]), MAX_INPUT_BYTES + 10)
        path.write_text(json.dumps({"clase": CLASS, "otro": 1}), encoding="utf-8")
        with self.assertRaises(OperationError):
            _read_input(str(path), "class-index")


if __name__ == "__main__":
    unittest.main()


class RenderPunctuationTest(unittest.TestCase):
    """El texto publicado nunca junta la puntuación del momento con la de la cita (`días.: «…»`)."""

    def test_quote_goes_on_its_own_line_whatever_the_moment_ends_with(self):
        endings = ["Retomar la columna.", "¿Puede agotarse la empatía?", "¡Atención!", "Definición:",
                   "Ejemplos…", "Sin puntuación"]
        content = {"resumen": "Resumen.", "temas": [{"titulo": "Tema", "categoria": "tema", "momentos": [
            {"inicio": f"00:0{i}:00", "texto": text, "cita": f"cita {i}"} for i, text in enumerate(endings)]}]}
        lines = class_index.render(content).split("\n")
        for i, text in enumerate(endings):
            at = lines.index(f"- [00:0{i}:00] {text}")
            self.assertEqual(lines[at + 1], f"  «cita {i}»")
        self.assertNotRegex(class_index.render(content), r"[.?!:…]\s*:\s*«")
