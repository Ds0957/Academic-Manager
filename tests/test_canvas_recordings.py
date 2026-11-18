"""Grabaciones con su fecha en el contenido de Canvas (odd/tasks/clases-automaticas.md, S1)."""
import unittest
from datetime import date

from canvas_sync import recordings

ID = "1SampleDriveFileIdForTests_0000{:02d}"
LINK = "https://drive.google.com/file/d/{}/view?usp=sharing"

# Como el foro de SER: la fecha y debajo su enlace, en el texto.
FORUM = {"text": "Estudiantes, buen día\nEn este foro serán publicados los enlaces.\n1 de octubre\n"
                 + LINK.format(ID.format(1)) + "\n8 octubre\n" + LINK.format(ID.format(2))
                 + "\n15 de Octubre: " + LINK.format(ID.format(3)),
         "links": [LINK.format(ID.format(n)) for n in (1, 2, 3)]}

# Como la página de inglés: la fecha en un bloque y el enlace detrás de «Session N».
SESSIONS = {"text": "0th Session - Intro\nDate: 5th of August\nLink to access: Session 0\n"
                    "1st Session\nDate: 12th of August\nTopic seen in class: modal verbs\nLink to access: Session 1\n"
                    "9th Session\nDate: 14th of October\nLink to access:\n"
                    "10th Session\nDate: November 4\nLink to access: Session 10",
            "links": [LINK.format(ID.format(n)) for n in (10, 11, 12)],
            "anchors": [{"url": LINK.format(ID.format(10)), "text": "Session 0"},
                        {"url": LINK.format(ID.format(11)), "text": "Session 1"},
                        {"url": LINK.format(ID.format(12)), "text": "Session 10"}]}

SYNCED = date(2026, 11, 12)


class FindTest(unittest.TestCase):
    def found(self, content, synced=SYNCED):
        return [(r["file_id"][-2:], r["fecha"]) for r in recordings.find(content, synced)]

    def test_forum_dates_followed_by_their_link(self):
        self.assertEqual(self.found(FORUM), [("01", "2026-10-01"), ("02", "2026-10-08"), ("03", "2026-10-15")])

    def test_session_blocks_whose_link_is_the_session_name(self):
        self.assertEqual(self.found(SESSIONS), [("10", "2026-08-05"), ("11", "2026-08-12"), ("12", "2026-11-04")])

    def test_result_has_the_canonical_link(self):
        first = recordings.find(FORUM, SYNCED)[0]
        self.assertEqual(first["enlace"], f"https://drive.google.com/file/d/{ID.format(1)}/view")

    def test_a_date_far_after_the_sync_belongs_to_last_year(self):
        content = {"text": "20 de diciembre\n" + LINK.format(ID.format(5)), "links": []}
        self.assertEqual(self.found(content, date(2027, 1, 15)), [("05", "2026-12-20")])
        self.assertEqual(self.found(content, date(2026, 12, 15)), [("05", "2026-12-20")])  # dentro de una semana

    def test_links_without_a_date_before_them_are_left_out(self):
        content = {"text": "Lectura recomendada\n" + LINK.format(ID.format(7)) + "\n3 de marzo\nsin enlace",
                   "links": [LINK.format(ID.format(7))]}
        self.assertEqual(self.found(content), [])

    def test_numeric_dates_day_first(self):
        content = {"text": "Date: 21 - 10 - 2026\nLink to access: " + LINK.format(ID.format(4))
                           + "\nDate: 28/10/2026\n" + LINK.format(ID.format(6)), "links": []}
        self.assertEqual(self.found(content), [("04", "2026-10-21"), ("06", "2026-10-28")])

    def test_an_unreadable_date_label_does_not_inherit_the_previous_date(self):
        content = {"text": "Date: 7th of October\nLink to access: 9 Session\nDate: pendiente\n"
                           + LINK.format(ID.format(4)), "links": []}
        self.assertEqual(self.found(content), [])

    def test_impossible_dates_and_other_hosts_are_ignored(self):
        content = {"text": "31 de febrero\n" + LINK.format(ID.format(8)) + "\n2 de marzo\nhttps://evil.example/file/d/"
                           + ID.format(9) + "/view", "links": []}
        self.assertEqual(self.found(content), [])

    def test_the_same_recording_counts_once(self):
        content = {"text": FORUM["text"] + "\n22 octubre\n" + LINK.format(ID.format(1)), "links": []}
        self.assertEqual([f for f, _ in self.found(content)], ["01", "02", "03"])

    def test_empty_or_missing_content(self):
        self.assertEqual(recordings.find(None, SYNCED), [])
        self.assertEqual(recordings.find({"text": None}, SYNCED), [])


class SnapshotTest(unittest.TestCase):
    def test_every_content_of_a_course_is_read_with_its_source(self):
        snapshot = {"captured_at_utc": "2026-11-12T13:00:00Z", "data": {
            "assignments": [{"id": 1, "name": "CLASS RECORDINGS", "instructions": SESSIONS}],
            "sections": {"discussion_topics": {"status": "ok", "data": [
                             {"id": 2, "title": "Grabación de encuentros", "instructions": FORUM}]},
                         "announcements": [{"id": 3, "title": "Aviso", "instructions": None}],
                         "page_details": [{"url": "inicio", "title": "Inicio", "instructions": FORUM}]}}}
        found = recordings.from_snapshot(snapshot)
        self.assertEqual(len(found), 6)  # la página repite las del foro: se cuentan una vez
        self.assertEqual({r["fuente"] for r in found}, {"CLASS RECORDINGS", "Grabación de encuentros"})

    def test_sections_that_failed_in_canvas_are_skipped(self):
        snapshot = {"captured_at_utc": "2026-11-12T13:00:00Z",
                    "data": {"assignments": "error", "sections": {"announcements": ["forbidden", None],
                                                                  "discussion_topics": {"error": "x"}}}}
        self.assertEqual(recordings.from_snapshot(snapshot), [])
        self.assertEqual(recordings.from_snapshot({}), [])


if __name__ == "__main__":
    unittest.main()
