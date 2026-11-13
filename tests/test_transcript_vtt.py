"""Transcripción del reproductor de Drive (.vtt) importada a Capture (odd/tasks/transcripcion-ingles.md, S2)."""
import json
import tempfile
import unittest
from pathlib import Path

from capture.models import CaptureError
from capture.transcription.formatter import import_transcript, markdown, normalize

# Same shape as Drive's automatic captions: each cue repeats the previous line, new words carry
# <time><c> tags (a single word has none), and ~10 ms cues repeat the line just said. Drive writes a
# line with a single space after many timings; «␣» marks it here so editors keep it.
ROLLING = """WEBVTT
Kind: captions
Language: en

00:00:21.439 --> 00:00:24.790 align:start position:0%
␣
Hello<00:00:21.680><c> guys.</c><00:00:22.000><c> Good</c><00:00:22.160><c> evening.</c>

00:00:24.790 --> 00:00:24.800 align:start position:0%
Hello guys. Good evening.
␣

00:00:24.800 --> 00:00:27.990 align:start position:0%
Hello guys. Good evening.
Hello.<00:00:25.119><c> Hello.</c>

00:00:27.990 --> 00:00:28.000 align:start position:0%
Hello. Hello.
␣

00:00:28.000 --> 00:00:31.990 align:start position:0%
Hello. Hello.
Hello.

00:00:31.990 --> 00:00:32.000 align:start position:0%
␣
␣

00:00:40.000 --> 00:00:44.470 align:start position:0%
␣
&gt;&gt;<00:00:40.300><c> Hello</c><00:00:41.000><c> teacher.</c>

00:00:44.470 --> 00:00:44.480 align:start position:0%
&gt;&gt; Hello teacher.
␣

00:00:55.000 --> 00:00:58.000 align:start position:0%
&gt;&gt; Hello teacher.
Welcome<00:00:55.440><c> back.</c>

00:00:58.000 --> 00:00:58.010 align:start position:0%
Welcome back.
␣
""".replace("␣", " ")

PLAIN = """WEBVTT

1
00:01:00.000 --> 00:01:04.000
Primera frase de la clase.

2
00:01:04.000 --> 00:01:09.500
Segunda frase,
en dos líneas.
"""


def imported(text, name="clase.vtt"):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / name
        path.write_text(text, encoding="utf-8")
        return import_transcript(path)


class DriveCaptionsTest(unittest.TestCase):
    def test_rolling_captions_keep_each_phrase_once_without_tags_in_30_second_fragments(self):
        raw = imported(ROLLING)
        self.assertEqual(raw["language"], "en")
        self.assertEqual(raw["segments"], [
            {"start": 21.439, "end": 44.47,
             "text": "Hello guys. Good evening. Hello. Hello. Hello. >> Hello teacher."},
            {"start": 55.0, "end": 58.0, "text": "Welcome back."}])

    def test_imported_vtt_passes_capture_validation_with_its_times(self):
        transcript = normalize(imported(ROLLING))
        self.assertFalse(transcript["timestamps_unavailable"])
        text = markdown(transcript, "English III - 2026/11/04")
        self.assertIn("[00:00:21–00:00:44] Hello guys. Good evening. Hello. Hello. Hello. >> Hello teacher.", text)
        self.assertIn("[00:00:55–00:00:58] Welcome back.", text)

    def test_plain_vtt_without_word_tags_imports_each_line_once(self):
        raw = imported(PLAIN)
        self.assertIsNone(raw["language"])
        self.assertEqual(raw["segments"], [
            {"start": 60.0, "end": 69.5, "text": "Primera frase de la clase. Segunda frase, en dos líneas."}])

    def test_fragment_closes_after_30_seconds(self):
        cues = "".join(f"\n00:00:{s:02d}.000 --> 00:00:{s + 4:02d}.000\nFrase {s}.\n" for s in range(0, 60, 5))
        segments = imported("WEBVTT\n" + cues)["segments"]
        self.assertEqual([(s["start"], s["end"]) for s in segments], [(0.0, 29.0), (30.0, 59.0)])
        self.assertTrue(segments[0]["text"].startswith("Frase 0. Frase 5."))

    def test_vtt_without_cues_is_refused(self):
        with self.assertRaises(CaptureError):
            imported("WEBVTT\nKind: captions\n\n")

    def test_json_and_text_imports_keep_their_previous_behavior(self):
        segments = {"segments": [{"start": 0, "end": 2, "text": "hola"}], "language": "es"}
        self.assertEqual(imported(json.dumps(segments), "t.json"), segments)
        self.assertEqual(imported("  Texto sin tiempos.\n", "t.md"),
                         {"segments": [{"start": None, "end": None, "text": "Texto sin tiempos."}],
                          "timestamps_unavailable": True, "language": None})


if __name__ == "__main__":
    unittest.main()
