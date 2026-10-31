"""Tests for selective Canvas PDFs. No live requests, no real tokens."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from canvas_sync.api import ORIGIN
from canvas_sync.common import SyncError, atomic_json
from canvas_sync.resources import (
    LimitedRedirects, extract_pdf_text, fetch_pdf, retrieve_pdf, source_catalog, url_host_valid)
from canvas_sync.sync import sha


class DummyAPI:
    def __init__(self, token):
        if token != "fixture-token":
            raise AssertionError("Not the synthetic token")
        self.calls = []

    def profile(self):
        self.calls.append(("GET", "/api/v1/users/self/profile"))
        return 123

    def get(self, url):
        self.calls.append(("GET", url))
        return {
            "id": 22387883,
            "content-type": "application/pdf",
            "size": 20,
            "url": ORIGIN + "/files/22387883/download?verifier=signed-private",
            "locked": False,
        }, ""


class DummyResponse:
    def __init__(self, url, data):
        self.url, self.data = url, data
        self.read_n = None

    def geturl(self):
        return self.url

    def read(self, limit):
        self.read_n = limit
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class DummyOpener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, request, timeout):
        self.calls.append({
            "method": request.get_method(),
            "auth": request.get_header("Authorization"),
            "url": request.full_url,
            "timeout": timeout,
        })
        return self.response


class CanvasPDFTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        catalog = {
            "course": {"id": 82862},
            "sections": {
                "files": {
                    "status": "ok", "data": [
                        {"id": 22387883, "display_name": "Actividad evaluativa.pdf",
                         "size": 20, "content-type": "application/pdf"}]}}}
        path = self.root / "procesamiento" / "canvas-sync" / "v01" / "82862"
        path.mkdir(parents=True)
        atomic_json(path / "latest.json", {"sha256": sha(catalog), "data": catalog})

    def test_catalog_limits_and_hash(self):
        item = source_catalog(self.root, 82862, 22387883)
        self.assertEqual(item["size"], 20)
        for bad in (22387884, -1):
            with self.assertRaises(SyncError):
                source_catalog(self.root, 82862, bad)
        with self.assertRaises(SyncError):
            source_catalog(self.root, 9999, 22387883)
        path = self.root / "procesamiento" / "canvas-sync" / "v01" / "82862" / "latest.json"
        bundle = json.loads(path.read_text(encoding="utf-8"))
        bundle["data"]["sections"]["files"]["data"][0]["size"] = 19
        atomic_json(path, bundle)
        with self.assertRaises(SyncError):
            source_catalog(self.root, 82862, 22387883)

    def test_text_parser_blank_pdf_no_ocr_invented(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("Dependencia opcional no instalada.")
        import io
        out = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=600, height=800)
        writer.write(out)
        result = extract_pdf_text(out.getvalue())
        self.assertEqual(result["status"], "no_text_layer")
        self.assertEqual(result["pages"], 1)
        self.assertEqual(result["text"], "")

    def test_allowlist_and_never_external_token(self):
        self.assertTrue(url_host_valid(ORIGIN + "/files/22387883/download?verifier=123", initial=True))
        self.assertFalse(url_host_valid("https://evil.example/files/22387883/download", initial=True))
        self.assertFalse(url_host_valid("http://areandina.instructure.com/files/3/download", initial=True))
        self.assertFalse(url_host_valid("https://areandina.instructure.com.evil.example/files/3/download", initial=True))
        self.assertFalse(url_host_valid("https://areandina.instructure.com@evil.example/files/3/download", initial=True))
        self.assertTrue(url_host_valid("https://instructure-uploads.s3.amazonaws.com/object?sig=ok"))
        self.assertFalse(url_host_valid("https://example.s3.amazonaws.com/object?sig=ok"))
        self.assertTrue(url_host_valid("https://abc.inscloudgate.net/file"))
        self.assertTrue(url_host_valid("https://a1.cluster51.canvas-user-content.com/file"))
        self.assertFalse(url_host_valid("https://canvas-user-content.com.evil.example/file"))

    def test_redirect_to_attacker_denied(self):
        blocker = LimitedRedirects()
        with self.assertRaises(SyncError):
            blocker.redirect_request(None, None, 302, "Redirect", {},
                                     "https://evil.example/steal")
        with self.assertRaises(SyncError):
            blocker.redirect_request(None, None, 302, "Redirect", {},
                                     "http://areandina.instructure.com/steal")

    def test_pdf_download_is_get_without_bearer(self):
        data = b"%PDF-1.7\n" + b"x" * 11
        url = ORIGIN + "/files/22387883/download?verifier=signed-private"
        opener = DummyOpener(DummyResponse(url, data))
        result = fetch_pdf(url, opener=opener)
        self.assertEqual(result, data)
        self.assertEqual(opener.calls[0]["method"], "GET")
        self.assertIsNone(opener.calls[0]["auth"])
        with self.assertRaises(SyncError):
            fetch_pdf("https://evil.example/object", opener=opener)

    def test_reject_html_or_oversized_file(self):
        url = ORIGIN + "/files/22387883/download?verifier=x"
        with self.assertRaises(SyncError):
            fetch_pdf(url, opener=DummyOpener(DummyResponse(url, b"<html>login</html>")))
        with self.assertRaises(SyncError):
            fetch_pdf(url, max_bytes=12,
                      opener=DummyOpener(DummyResponse(url, b"%PDF-" + b"x" * 20)))

    def test_selective_read_and_local_idempotent_storage(self):
        data = b"%PDF-1.7\n" + b"x" * 11
        self.assertEqual(len(data), 20)
        parsed = {"status": "text", "pages": 1, "pages_read": 1,
                  "text": "Criterios del Eje 2", "truncated": False}
        instances = []
        def make_client(token):
            instance = DummyAPI(token)
            instances.append(instance)
            return instance
        with patch("canvas_sync.resources.valid",
                   return_value=({"token": "fixture-token", "account_id": 123}, None)):
            result = retrieve_pdf(self.root, 82862, 22387883,
                                  client_factory=make_client,
                                  downloader=lambda url: data,
                                  extractor=lambda blob: parsed)
            again = retrieve_pdf(self.root, 82862, 22387883,
                                 client_factory=make_client,
                                 downloader=lambda url: data,
                                 extractor=lambda blob: parsed)
        digest = hashlib.sha256(data).hexdigest()
        dest = self.root / "procesamiento" / "canvas-sync" / "pdfs" / "82862" / "22387883"
        self.assertEqual(result["sha256"], digest)
        self.assertEqual((dest / (digest + ".pdf")).read_bytes(), data)
        self.assertEqual((dest / (digest + ".txt")).read_text(encoding="utf-8").strip(),
                         "Criterios del Eje 2")
        self.assertEqual(len(list(dest.glob("*.pdf"))), 1)
        self.assertEqual(again["sha256"], result["sha256"])
        self.assertNotIn("signed-private", (dest / "latest.json").read_text(encoding="utf-8"))
        self.assertNotIn("fixture-token", (dest / "latest.json").read_text(encoding="utf-8"))
        self.assertFalse((self.root / "database.xlsx").exists())
        self.assertFalse((self.root / "academic-manager.sqlite").exists())
        self.assertTrue(all(method == "GET" for instance in instances
                            for method, _ in instance.calls))

    def test_size_mismatch_does_not_publish(self):
        with patch("canvas_sync.resources.valid",
                   return_value=({"token": "fixture-token", "account_id": 123}, None)):
            with self.assertRaises(SyncError):
                retrieve_pdf(self.root, 82862, 22387883,
                             client_factory=DummyAPI,
                             downloader=lambda url: b"%PDF-1.7",
                             extractor=lambda blob: {})
        self.assertFalse((self.root / "procesamiento" / "canvas-sync" / "pdfs").exists())


if __name__ == "__main__":
    unittest.main()
