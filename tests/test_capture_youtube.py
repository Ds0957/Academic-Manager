"""Fuente YouTube de Capture con yt-dlp simulado: nunca accede a la red."""
import contextlib
import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from capture.capture import main, parser, run, source_kind
from capture.models import CaptureError
from capture.sources import youtube
from capture.storage.metadata import read_json
from capture.storage.publisher import validate

VIDEO = "dQw4w9WgXcQ"
URL = "https://www.youtube.com/watch?v=" + VIDEO
VTT = """WEBVTT
Kind: captions
Language: es

00:00:01.000 --> 00:00:04.500 align:start position:0%
Hola <c>a todos</c>, bienvenidos.

00:00:05.000 --> 00:00:09.000
La entropía mide el desorden &amp; la información.
"""


def info(**changes):
    data = {"id": VIDEO, "title": "Entropía explicada", "channel": "Canal Docente",
            "uploader": "Canal Docente", "duration": 600, "upload_date": "20260115",
            "webpage_url": URL, "availability": "public", "live_status": "not_live",
            "is_live": False, "age_limit": 0,
            "subtitles": {"es": [{"ext": "json3", "url": "https://subs.example/json3"},
                                 {"ext": "vtt", "url": "https://subs.example/es.vtt"}]},
            "automatic_captions": {"es": [{"ext": "vtt", "url": "https://auto.example/es.vtt"}]}}
    data.update(changes)
    return data


class FakeYoutube:
    """Sustituye yt_dlp.YoutubeDL y registra cada operación solicitada."""

    def __init__(self, data, media=b"audio-bytes", subtitle=VTT):
        self.data, self.media, self.subtitle = data, media, subtitle
        self.calls, self.options = [], []

    def __call__(self, options):
        self.options.append(options)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        self.calls.append(("extract", url, download))
        if isinstance(self.data, Exception):
            raise self.data
        if download:
            Path(self.options[-1]["outtmpl"]["default"]).write_bytes(self.media)
        return dict(self.data)

    def urlopen(self, url):
        self.calls.append(("urlopen", url))
        return io.BytesIO(self.subtitle.encode("utf-8"))


def fake_extract(source, target, *args):
    with wave.open(str(target), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * 16000)
    return 1


class YoutubeSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, *more):
        return parser().parse_args(["--runtime", str(self.runtime), "--semester", "2026-2",
                                    "--subject", "Física", "--source", URL, *more])

    def invoke(self, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            job = run(args)
        return job, out.getvalue()

    def test_youtube_urls_detected_before_drive_and_local(self):
        for url in (URL, "https://youtube.com/watch?v=" + VIDEO + "&t=30s",
                    "https://m.youtube.com/watch?v=" + VIDEO, "https://youtu.be/" + VIDEO + "?si=x",
                    "https://www.youtube.com/shorts/" + VIDEO):
            self.assertEqual(source_kind(url), "youtube", url)
            self.assertEqual(youtube.video_id(url), VIDEO)
        self.assertEqual(source_kind("https://drive.google.com/file/d/1SampleDriveFileIdForTests_000001/view"),
                         "drive")
        self.assertEqual(source_kind("1SampleDriveFileIdForTests_000001"), "drive")
        for path in ("C:/downloads/clase.mp4", "C:\\downloads\\clase.mp4", "relativo/clase.mp3"):
            self.assertEqual(source_kind(path), "local", path)
        for url in ("https://www.youtube.com/playlist?list=PL123",
                    "https://www.youtube.com/watch?v=" + VIDEO + "&list=PL123",
                    "https://www.youtube.com/@canal", "https://youtu.be/corto"):
            self.assertEqual(source_kind(url), "youtube", url)
            with self.assertRaises(CaptureError):
                youtube.video_id(url)

    def test_manual_subtitles_preferred_and_auto_captions_never_used(self):
        fake = FakeYoutube(info())
        with patch.object(youtube, "_client", fake), \
                patch("capture.capture.transcribe", side_effect=AssertionError("no WhisperX")):
            job, output = self.invoke(self.args())
        self.assertEqual(job["status"], "completed")
        self.assertIn(("urlopen", "https://subs.example/es.vtt"), fake.calls)
        self.assertFalse([c for c in fake.calls if "auto.example" in str(c)])
        self.assertFalse(any(call[0] == "extract" and call[2] for call in fake.calls))
        self.assertEqual(job["engine"], {"engine": "youtube_manual_subtitles", "model": None,
                                         "subtitle_language": "es"})
        metadata = job["source_metadata"]
        self.assertEqual((metadata["kind"], metadata["video_id"], metadata["title"], metadata["channel"],
                          metadata["duration_seconds"], metadata["upload_date"], metadata["url"]),
                         ("youtube", VIDEO, "Entropía explicada", "Canal Docente", 600, "2026-01-15", URL))
        self.assertEqual(metadata["text_source"], "manual_subtitles")
        stage = self.runtime / "procesamiento" / job["job_id"] / "artifacts"
        validate(stage, job["job_id"], job["hashes"])
        transcript = read_json(stage / "transcript.json")
        self.assertEqual(transcript["segments"], [
            {"start": 1.0, "end": 4.5, "text": "Hola a todos, bienvenidos."},
            {"start": 5.0, "end": 9.0, "text": "La entropía mide el desorden & la información."}])
        self.assertIn("Fuente: Entropía explicada", (stage / "transcript.md").read_text(encoding="utf-8"))
        self.assertEqual(read_json(stage / "metadata.json")["source"]["video_id"], VIDEO)
        self.assertIn("Subtítulos manuales", output)
        for options in fake.options:
            self.assertTrue(options["noplaylist"])
            self.assertNotIn("cookiefile", options)
            self.assertNotIn("cookiesfrombrowser", options)

    def test_regional_manual_subtitle_matches_language(self):
        fake = FakeYoutube(info(subtitles={"en": [{"ext": "vtt", "url": "https://subs.example/en.vtt"}],
                                           "es-419": [{"ext": "vtt", "url": "https://subs.example/419.vtt"}]}))
        with patch.object(youtube, "_client", fake):
            job, _ = self.invoke(self.args())
        self.assertIn(("urlopen", "https://subs.example/419.vtt"), fake.calls)
        self.assertEqual(job["source_metadata"]["subtitle_language"], "es-419")

    def test_without_manual_subtitles_downloads_bestaudio_for_whisperx(self):
        fake = FakeYoutube(info(subtitles={"en": [{"ext": "vtt", "url": "https://subs.example/en.vtt"}]}))
        raw = {"language": "es", "segments": [{"start": 0, "end": 1, "text": "audio transcrito"}]}
        with patch.object(youtube, "_client", fake), \
                patch("capture.capture.probe", return_value={"duration_seconds": 600}), \
                patch("capture.capture.extract", side_effect=fake_extract), \
                patch("capture.capture.transcribe", return_value=(raw, {"engine": "fake"})) as engine:
            job, output = self.invoke(self.args())
        self.assertEqual(job["status"], "completed")
        engine.assert_called_once()
        self.assertEqual(job["source_metadata"]["text_source"], "audio_whisperx")
        self.assertIsNone(job["source_metadata"]["subtitle_language"])
        self.assertFalse([c for c in fake.calls if c[0] == "urlopen"])
        download = [o for o in fake.options if o.get("format")]
        self.assertEqual(len(download), 1)
        self.assertEqual(download[0]["format"], "bestaudio")
        self.assertEqual(job["media_sha256"], sha256_bytes(b"audio-bytes"))
        self.assertTrue(job["media_cleaned"])
        self.assertFalse((self.runtime / "temporales" / job["job_id"] / "source.media").exists())
        self.assertIn("WhisperX", output)

    def test_resume_neither_redownloads_nor_accepts_changed_audio(self):
        fake = FakeYoutube(info(subtitles={}))
        with patch.object(youtube, "_client", fake), \
                patch("capture.capture.probe", return_value={"duration_seconds": 600}), \
                patch("capture.capture.extract", side_effect=fake_extract), \
                patch("capture.capture.transcribe", side_effect=CaptureError("fallo simulado")):
            with self.assertRaises(CaptureError):
                self.invoke(self.args())
        identity = next((self.runtime / "procesamiento").iterdir()).name
        downloads = sum(1 for c in fake.calls if c[0] == "extract" and c[2])
        resume = parser().parse_args(["--runtime", str(self.runtime), "--resume", identity])
        raw = {"language": "es", "segments": [{"start": 0, "end": 1, "text": "texto"}]}
        with patch.object(youtube, "_client", fake), \
                patch("capture.capture.probe", return_value={"duration_seconds": 600}), \
                patch("capture.capture.transcribe", return_value=(raw, {"engine": "fake"})):
            job, _ = self.invoke(resume)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(sum(1 for c in fake.calls if c[0] == "extract" and c[2]), downloads)
        # A deleted temporary copy is downloaded again only if YouTube returns the same bytes.
        fake2 = FakeYoutube(info(subtitles={}), media=b"other-bytes")
        with patch.object(youtube, "_client", fake2), \
                patch("capture.capture.probe", return_value={"duration_seconds": 600}), \
                patch("capture.capture.extract", side_effect=fake_extract), \
                patch("capture.capture.transcribe", side_effect=CaptureError("fallo simulado")):
            with self.assertRaises(CaptureError):
                self.invoke(self.args())
        second = sorted((p for p in (self.runtime / "procesamiento").iterdir() if p.name != identity))[0].name
        (self.runtime / "temporales" / second / "source.media").unlink()
        fake3 = FakeYoutube(info(subtitles={}), media=b"changed!!")
        with patch.object(youtube, "_client", fake3):
            with self.assertRaisesRegex(CaptureError, "cambió"):
                self.invoke(parser().parse_args(["--runtime", str(self.runtime), "--resume", second]))
        self.assertEqual(read_json(self.runtime / "procesamiento" / second / "job.json")["status"], "failed")

    def rejected(self, data, pattern):
        fake = FakeYoutube(data)
        with patch.object(youtube, "_client", fake):
            with self.assertRaisesRegex(CaptureError, pattern):
                self.invoke(self.args())
        self.assertFalse(any(call[0] == "extract" and call[2] for call in fake.calls))
        self.assertFalse([c for c in fake.calls if c[0] == "urlopen"])

    def test_playlist_live_private_and_long_videos_rejected(self):
        self.rejected(info(_type="playlist", entries=[]), "un solo video")
        self.rejected(info(is_live=True, live_status="is_live"), "directo")
        self.rejected(info(live_status="is_upcoming"), "directo")
        for availability in ("private", "premium_only", "subscriber_only", "needs_auth"):
            self.rejected(info(availability=availability), "iniciar sesión")
        self.rejected(info(age_limit=18), "iniciar sesión")
        self.rejected(info(duration=4 * 3600 + 1), "4 horas")
        self.rejected(info(duration=None), "duración")
        self.rejected(Exception("ERROR: [youtube] x: Sign in to confirm your age"), "iniciar sesión")
        self.rejected(Exception("ERROR: [youtube] x: Video unavailable"), "No se pudo leer")

    def test_unlisted_video_and_exact_limit_accepted(self):
        fake = FakeYoutube(info(availability="unlisted", duration=4 * 3600))
        with patch.object(youtube, "_client", fake):
            job, _ = self.invoke(self.args())
        self.assertEqual(job["status"], "completed")

    def test_subtitles_are_not_trimmed(self):
        fake = FakeYoutube(info())
        with patch.object(youtube, "_client", fake):
            with self.assertRaisesRegex(CaptureError, "subtítulos"):
                self.invoke(self.args("--duration", "60"))

    def test_missing_yt_dlp_is_a_clear_cli_error(self):
        err = io.StringIO()
        with patch.dict("sys.modules", {"yt_dlp": None}), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            code = main(["--runtime", str(self.runtime), "--semester", "2026-2", "--subject", "Física",
                         "--source", URL])
        self.assertEqual(code, 1)
        self.assertIn("yt-dlp", err.getvalue())
        self.assertIn("requirements-capture.txt", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


def sha256_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    unittest.main()
