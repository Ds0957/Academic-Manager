"""Botón «Transcripción de Drive» en «Configuración» → «Claude» (odd/tasks/transcripcion-ingles.md, S1)."""
import html
import re
import unittest
from urllib.parse import unquote

from academic_app import drive_button
from academic_app.server import CSP


class DriveButtonTest(unittest.TestCase):
    def test_bookmark_is_the_script_in_one_line(self):
        link = drive_button.href()
        self.assertTrue(link.startswith("javascript:"))
        self.assertNotRegex(link, r"\s")
        code = unquote(link[len("javascript:"):])
        self.assertEqual(code, " ".join(line.strip() for line in drive_button.SCRIPT.splitlines()))

    def test_script_asks_drive_for_the_track_and_names_the_file_with_the_recording_code(self):
        code = drive_button.SCRIPT
        for piece in ("location.hostname !== 'drive.google.com'", "/timedtext?id=", "&type=list",
                      "&type=track&fmt=vtt&lang=", "credentials: 'include'", "' [' + id + '].vtt'"):
            self.assertIn(piece, code)
        self.assertNotIn("DOMParser", code)  # Drive aplica Trusted Types
        hosts = set(re.findall(r"https?://([^/'\"]+)", code))
        self.assertEqual(hosts, set(), "el botón no envía nada a otra dirección")

    def test_card_offers_the_link_to_drag_and_the_text_to_paste(self):
        boxes = []
        card = drive_button.card(lambda ident, label, text: boxes.append((ident, text)) or "<copy/>")
        link = re.search(r'<a class="marcador" href="([^"]+)">Transcripción de Drive</a>', card)
        self.assertIsNotNone(link)
        self.assertEqual(html.unescape(link.group(1)), drive_button.href())
        self.assertEqual(boxes, [("drive-button", drive_button.href())])

    def test_the_app_never_runs_the_bookmark_itself(self):
        self.assertIn("script-src", CSP)
        self.assertNotIn("unsafe-inline';", CSP.split("script-src")[1] + ";")


if __name__ == "__main__":
    unittest.main()
