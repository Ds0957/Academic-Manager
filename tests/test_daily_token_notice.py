"""La salida de `canvas_sync daily` muestra el aviso de caducidad del token en el log diario."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from canvas_sync.__main__ import main


def outcome(aviso):
    return {"path": "informe.json", "report": {
        "semestre": "2026-2", "bloque": 2, "novedades": [],
        "resumen": {"total_canvas": 1, "ya_en_base": 1, "excluidos_de_actividades": 0, "pendientes": 0},
        "token": {"vence": "2027-01-04", "dias_restantes": 10, "aviso": aviso}}}


class DailyTokenNoticeTests(unittest.TestCase):
    def run_daily(self, aviso):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             patch("canvas_sync.__main__.routine", return_value=outcome(aviso)), redirect_stdout(out):
            code = main(["daily", "--offline", "--runtime", tmp])
        return code, out.getvalue()

    def test_warning_is_printed_when_present(self):
        code, text = self.run_daily("El token de Canvas vence en 10 días (2027-01-04). Renueve con renew.")
        self.assertEqual(code, 0)
        self.assertIn("AVISO TOKEN CANVAS: El token de Canvas vence en 10 días", text)

    def test_no_warning_line_without_notice(self):
        code, text = self.run_daily(None)
        self.assertEqual(code, 0)
        self.assertNotIn("AVISO TOKEN", text)


if __name__ == "__main__":
    unittest.main()
