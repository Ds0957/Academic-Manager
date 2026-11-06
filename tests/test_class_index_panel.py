"""Índice de la clase en el panel (odd/tasks/indice-clase.md, S6)."""
import unittest
from unittest.mock import MagicMock

from academic_access.core import AccessError
from academic_app import class_page

from tests.test_academic_panel import PanelCase

CLASS = "CLS-SO2-20261020-01"
PAGE = f"/panel/materia/MAT-SO2/clase/{CLASS}"
INDICE = {
    "version": 1, "resumen": "Se vio <planificación> y se explicó la entrega.",
    "temas": [
        {"titulo": "Planificación <script>", "categoria": "tema", "momentos": [
            {"inicio": "00:08:40", "texto": "Idea general", "cita": "la idea general del planificador"}]},
        {"titulo": "Instrucciones de la actividad", "categoria": "instrucciones", "momentos": [
            {"inicio": "00:04:12", "texto": "Cómo entregar", "cita": "se sube a Canvas en parejas"},
            {"inicio": "01:20:18", "texto": "Repite la fecha", "cita": "fecha de entrega del viernes"}]},
    ],
}
SEGMENTS = [{"inicio": "00:04:12", "fin": "00:04:40", "texto": "Se sube a Canvas <en parejas>."},
            {"inicio": "00:08:40", "fin": "00:09:02", "texto": "La idea general del planificador."}]


def saved(indice=INDICE):
    return {"clase_id": CLASS, "materia_id": "MAT-SO2", "titulo": "Sesión de <SO2>", "fecha": "2026-10-20",
            "indice": indice, "actualizado_utc": "2026-11-05T15:00:00Z", "workbook_sha256": "x"}


class RenderTests(unittest.TestCase):
    def test_summary_comes_first_then_administrative_topics_then_the_rest(self):
        html = class_page.page(saved(), "/t")
        self.assertLess(html.index("De qué trató"), html.index("Instrucciones de la actividad"))
        self.assertLess(html.index("Instrucciones de la actividad"), html.index("Planificación"))

    def test_each_moment_links_to_its_fragment_and_text_is_escaped(self):
        html = class_page.page(saved(), "/t")
        self.assertIn('href="/t#t-00-04-12"', html)
        self.assertIn('href="/t#t-01-20-18"', html)
        self.assertIn("Se vio &lt;planificación&gt;", html)
        self.assertIn("Planificación &lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_transcript_has_an_anchor_per_fragment_and_escapes_text(self):
        html = class_page.transcript(SEGMENTS)
        self.assertIn('id="t-00-04-12"', html)
        self.assertIn('id="t-00-08-40"', html)
        self.assertIn("Canvas &lt;en parejas&gt;", html)


class ClassPanelTests(PanelCase):
    def setUp(self):
        super().setUp()
        self.index = MagicMock(return_value=saved())
        self.indexes = MagicMock(return_value=[{"clase_id": CLASS, "titulo": "Sesión de SO2", "fecha": "2026-10-20",
                                                "temas": 2, "momentos": 3}])
        self.segments = MagicMock(return_value=SEGMENTS)
        self.wizard.deps.update(panel_class_index=self.index, panel_class_indexes=self.indexes,
                                panel_class_transcript=self.segments)

    def test_class_page_shows_summary_and_index_with_links_to_the_transcript(self):
        status, _, body = self.get(PAGE)
        self.assertEqual(status, 200)
        self.assertEqual(self.index.call_args[0][1], CLASS)
        self.assertIn("De qué trató", body)
        self.assertIn(f'href="{PAGE}/transcripcion#t-00-04-12"', body)
        self.assertIn("Sesión de &lt;SO2&gt;", body)
        self.assertIn('href="/panel/materia/MAT-SO2"', body)
        self.assertNotIn("<script src=", body)

    def test_class_without_index_explains_how_to_create_it(self):
        self.index.return_value = saved(None)
        status, _, body = self.get(PAGE)
        self.assertEqual(status, 200)
        self.assertIn("todavía no tiene índice", body)
        self.assertIn("Claude", body)

    def test_transcript_page_opens_at_each_fragment(self):
        status, _, body = self.get(PAGE + "/transcripcion")
        self.assertEqual(status, 200)
        self.assertEqual(self.segments.call_args[0][1:], (CLASS, "MAT-SO2"))
        self.assertIn('id="t-00-04-12"', body)
        self.assertIn(f'href="{PAGE}"', body)

    def test_subject_page_lists_classes_with_index(self):
        _, _, body = self.get("/panel/materia/MAT-SO2")
        self.assertIn("Índices de clase", body)
        self.assertIn(f'href="{PAGE}"', body)
        self.assertIn("3 momentos", body)

    def test_subject_page_survives_unreadable_indexes(self):
        self.indexes.side_effect = AccessError("INVALID_SOURCE", "x")
        status, _, body = self.get("/panel/materia/MAT-SO2")
        self.assertEqual(status, 200)
        self.assertIn("Pendientes y fechas", body)
        self.assertNotIn("INVALID_SOURCE", body)

    def test_unknown_class_is_not_found(self):
        self.index.side_effect = AccessError("NOT_FOUND", "Esa clase no está registrada.")
        for path in (PAGE, PAGE + "/transcripcion"):
            with self.subTest(path):
                self.assertEqual(self.get(path)[0], 404)
        self.segments.assert_not_called()

    def test_class_of_another_subject_is_not_found(self):
        self.index.return_value = {**saved(), "materia_id": "MAT-GBD"}
        self.assertEqual(self.get(PAGE)[0], 404)
        self.assertEqual(self.get(PAGE + "/transcripcion")[0], 404)
        self.segments.assert_not_called()

    def test_subject_outside_the_semester_is_not_found(self):
        self.assertEqual(self.get(f"/panel/materia/MAT-NOPE/clase/{CLASS}")[0], 404)
        self.index.assert_not_called()

    def test_malformed_class_paths_do_not_reach_the_reader(self):
        for path in ("/panel/materia/MAT-SO2/clase/", "/panel/materia/MAT-SO2/clase/a/b",
                     "/panel/materia/MAT-SO2/clase/..%2Fx"):
            with self.subTest(path):
                self.assertEqual(self.get(path)[0], 404)
        self.index.assert_not_called()

    def test_unreadable_transcript_shows_a_plain_problem(self):
        self.segments.side_effect = AccessError("SOURCE_UNAVAILABLE", "ruta interna")
        status, _, body = self.get(PAGE + "/transcripcion")
        self.assertEqual(status, 200)
        self.assertNotIn("ruta interna", body)


if __name__ == "__main__":
    unittest.main()
