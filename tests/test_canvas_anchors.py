"""Texto de los enlaces de Drive en el contenido de Canvas (odd/tasks/clases-automaticas.md, S1)."""
import unittest

from canvas_sync.enriched import content_from_html

DRIVE = "https://drive.google.com/file/d/1SampleDriveFileIdForTests_000001/view?usp=sharing"


class DriveAnchorsTest(unittest.TestCase):
    def test_a_drive_link_keeps_the_text_it_is_written_on(self):
        html = (f'<p>Date: 12th of August</p><p>Link to access: <a href="{DRIVE}">Session <b>1</b></a></p>'
                '<p><a href="https://example.com/x">otro</a></p>')
        content = content_from_html(html)
        self.assertEqual(content["anchors"], [{"url": DRIVE, "text": "Session 1"}])
        self.assertEqual(content["links"], [DRIVE, "https://example.com/x"])
        self.assertIn("Link to access: Session 1", content["text"])

    def test_content_without_drive_links_keeps_its_previous_shape(self):
        content = content_from_html('<p>Hola <a href="https://example.com/x">aquí</a></p>')
        self.assertEqual(content, {"text": "Hola aquí", "truncated": False, "links": ["https://example.com/x"],
                                   "source_format": "canvas_html"})

    def test_anchor_list_is_bounded_and_ignores_scripts(self):
        many = "".join(f'<a href="{DRIVE}&n={n}">S{n}</a>' for n in range(200))
        content = content_from_html(many + f'<script><a href="{DRIVE}">x</a></script>')
        self.assertEqual(len(content["anchors"]), 120)


if __name__ == "__main__":
    unittest.main()
