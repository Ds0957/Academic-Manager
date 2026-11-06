"""Mapa de la materia en el panel (odd/tasks/mapa-materia.md, S6)."""
import hashlib
import unittest
from unittest.mock import MagicMock

from academic_access.core import AccessError
from academic_app import assets, map_page

from tests.test_academic_panel import PanelCase

MAPA = {
    "version": 1, "resumen": "Procesos, hilos y <planificación>.",
    "temas": [
        {"id": "SO2", "titulo": "Sistemas Operativos II", "explicacion": None, "padre": None, "eje": None,
         "fuentes": []},
        {"id": "T1", "titulo": "Procesos <script>", "explicacion": "Programa en ejecución.", "padre": "SO2",
         "eje": 1, "fuentes": [{"fuente_id": "lectura:91004:1:1", "cita": "Un proceso es un programa en ejecución."}]},
        {"id": "T2", "titulo": "Planificación", "explicacion": None, "padre": "SO2", "eje": 2,
         "fuentes": [{"fuente_id": "clase:CLS-1:transcripcion", "cita": "planificación round robin"}]},
        {"id": "T3", "titulo": "Round robin", "explicacion": None, "padre": "T2", "eje": 2,
         "fuentes": [{"fuente_id": "clase:CLS-1:transcripcion", "cita": "planificación round robin"}]},
    ],
    "relaciones": [{"de": "T2", "a": "T1", "texto": "decide qué proceso usa la CPU"}],
    "diagramas": [{"titulo": "Mapa mental", "mermaid": "mindmap\n  root((SO II))\n    A --> B <b>"}],
}


def saved(mapa=MAPA):
    return {"materia_id": "MAT-SO2", "mapa": mapa, "actualizado_utc": "2026-11-02T15:00:00Z",
            "workbook_sha256": "x"}


class MapRenderTests(unittest.TestCase):
    def test_tree_follows_parents_in_order_and_escapes_text(self):
        html = map_page.tree(MAPA)
        self.assertLess(html.index("Sistemas Operativos II"), html.index("Procesos"))
        self.assertLess(html.index("Planificación"), html.index("Round robin"))
        # Round robin is nested inside Planificación's list.
        self.assertRegex(html, r"Planificación.*?<ul[^>]*>.*Round robin", )
        self.assertIn("Procesos &lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("Eje 1", html)
        self.assertIn("Un proceso es un programa en ejecución.", html)
        self.assertIn("lectura:91004:1:1", html)

    def test_relations_use_topic_titles(self):
        html = map_page.relations(MAPA)
        self.assertIn("Planificación", html)
        self.assertIn("decide qué proceso usa la CPU", html)
        self.assertIn("Procesos &lt;script&gt;", html)
        self.assertEqual(map_page.relations({**MAPA, "relaciones": []}), "")

    def test_diagrams_are_escaped_mermaid_blocks(self):
        html = map_page.diagrams(MAPA)
        self.assertIn('<pre class="mermaid">', html)
        self.assertIn("A --&gt; B &lt;b&gt;", html)
        self.assertIn("Mapa mental", html)
        self.assertEqual(map_page.diagrams({**MAPA, "diagramas": []}), "")


class AssetTests(unittest.TestCase):
    def test_vendored_mermaid_matches_its_pin(self):
        for name, (filename, digest, _) in assets.ASSETS.items():
            with self.subTest(name):
                data = (assets.STATIC_DIR / filename).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
        self.assertIn("/static/mermaid-12.2.0.min.js", assets.ASSETS)
        self.assertTrue((assets.STATIC_DIR / "mermaid.LICENSE").is_file())

    def test_git_never_rewrites_line_endings_of_pinned_scripts(self):
        # A Windows checkout (core.autocrlf=true) would turn LF into CRLF and break the SHA-256 pin.
        import subprocess
        root = assets.STATIC_DIR.parents[1]
        files = [f"academic_app/static/{name}" for name, _, _ in assets.ASSETS.values()]
        out = subprocess.run(["git", "check-attr", "text", "--", *files], cwd=root, capture_output=True,
                             text=True, check=True).stdout
        for line in out.strip().splitlines():
            self.assertTrue(line.endswith("text: unset"), line)

    def test_init_script_uses_strict_security(self):
        script = (assets.STATIC_DIR / "diagramas.js").read_text(encoding="utf-8")
        self.assertIn('securityLevel: "strict"', script)


class MapPanelTests(PanelCase):
    def setUp(self):
        super().setUp()
        self.map = MagicMock(return_value=saved())
        self.wizard.deps["panel_map"] = self.map

    def test_map_page_shows_tree_relations_and_diagrams(self):
        status, response, body = self.get("/panel/materia/MAT-SO2/mapa")
        self.assertEqual(status, 200)
        self.map.assert_called_once()
        self.assertEqual(self.map.call_args[0][1], "MAT-SO2")
        self.assertIn("Mapa de la materia", body)
        self.assertIn("Procesos, hilos y &lt;planificación&gt;.", body)
        self.assertIn("Round robin", body)
        self.assertIn("decide qué proceso usa la CPU", body)
        self.assertIn('<pre class="mermaid">', body)
        self.assertIn('<script src="/static/mermaid-12.2.0.min.js"></script>', body)
        self.assertIn('<script src="/static/diagramas.js"></script>', body)
        self.assertIn('href="/panel/materia/MAT-SO2"', body)

    def test_without_diagrams_no_script_is_loaded(self):
        self.map.return_value = saved({**MAPA, "diagramas": []})
        _, _, body = self.get("/panel/materia/MAT-SO2/mapa")
        self.assertNotIn("<script src=", body)

    def test_without_a_map_it_explains_how_to_create_it(self):
        self.map.return_value = {**saved(), "mapa": None, "actualizado_utc": None}
        status, _, body = self.get("/panel/materia/MAT-SO2/mapa")
        self.assertEqual(status, 200)
        self.assertIn("todavía no tiene mapa", body)
        self.assertIn("Claude", body)
        self.assertNotIn("<script src=", body)

    def test_subject_page_links_to_its_map(self):
        _, _, body = self.get("/panel/materia/MAT-SO2")
        self.assertIn('href="/panel/materia/MAT-SO2/mapa"', body)
        self.assertIn("4 temas", body)
        self.assertNotIn("<script src=", body)

    def test_unreadable_map_does_not_hide_the_subject_page(self):
        self.map.side_effect = AccessError("INVALID_SOURCE", "El mapa guardado de la materia no es válido.")
        status, _, body = self.get("/panel/materia/MAT-SO2")
        self.assertEqual(status, 200)
        self.assertIn("Pendientes y fechas", body)
        status, _, body = self.get("/panel/materia/MAT-SO2/mapa")
        self.assertEqual(status, 200)
        self.assertNotIn("INVALID_SOURCE", body)

    def test_map_of_a_subject_outside_the_semester_is_not_found(self):
        status, _, _ = self.get("/panel/materia/MAT-NOPE/mapa")
        self.assertEqual(status, 404)
        status, _, _ = self.get("/panel/materia/MAT-SO2/otra")
        self.assertEqual(status, 404)
        self.map.assert_not_called()

    def test_static_scripts_are_served_as_javascript_and_cached(self):
        status, response, body = self.get("/static/diagramas.js")
        self.assertEqual(status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/javascript; charset=utf-8")
        self.assertIn("max-age", response.getheader("Cache-Control"))
        self.assertIn("mermaid.initialize", body)
        status, response, _ = self.get("/static/mermaid-12.2.0.min.js")
        self.assertEqual((status, response.getheader("Content-Type")), (200, "text/javascript; charset=utf-8"))
        for missing in ("/static/otro.js", "/static/../wizard.py", "/static/"):
            with self.subTest(missing):
                self.assertEqual(self.get(missing)[0], 404)

    def test_csp_allows_same_origin_scripts_and_still_no_inline_or_eval(self):
        from academic_app import server as app_server
        _, response, _ = self.get("/panel/materia/MAT-SO2/mapa")
        csp = response.getheader("Content-Security-Policy")
        self.assertEqual(csp, app_server.CSP)
        script_src = next(p.strip() for p in csp.split(";") if p.strip().startswith("script-src"))
        self.assertIn("'self'", script_src)
        self.assertNotIn("unsafe", script_src)


if __name__ == "__main__":
    unittest.main()
