"""Canvas Sync V0: sin red, sin tokens reales y sin tocar el workbook."""
import contextlib
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from canvas_sync.api import Client, check_url, next_page, ORIGIN
from canvas_sync.auth import (authorize, expiry_from_input, load, status, token_path)
from canvas_sync.common import SyncError
from canvas_sync.sync import compare, slim_assignment, sync, sha


class FakeClient:
    def __init__(self, token, identity=123):
        self.token = token
        self.identity = identity

    def profile(self):
        if self.token == "bad-token":
            raise SyncError("401")
        return self.identity

    def course(self, course_id):
        return {"id": course_id, "name": "Curso simulado", "course_code": "SIM"}

    def assignments(self, course_id):
        return [{"id": 22, "name": "Trabajo", "points_possible": 20,
                 "due_at": None, "submission": {
                     "submitted_at": "2026-09-26T12:05:00-05:00",
                     "score": 5.7, "workflow_state": "graded"}}]

    def courses(self):
        return [{"id": 82862, "name": "Curso simulado"}]


class PageResponse:
    status = 200

    def __init__(self, data, link=""):
        self.data = json.dumps(data).encode()
        self.headers = {"Content-Type": "application/json", "Link": link}

    def read(self, size):
        return self.data[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.methods = []

    def open(self, request, timeout):
        self.urls.append(request.full_url)
        self.methods.append(request.get_method())
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class CanvasSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "runtime"
        self.root.mkdir()
        self.now = datetime.now().astimezone()
        self.expiry = (self.now + timedelta(days=12)).strftime("%Y-%m-%d %H:%M")
        self.good = lambda tok: FakeClient(tok)

    def tearDown(self):
        self.temp.cleanup()

    def authorize(self, token="synthetic-secret", renew=False, identity=123):
        with contextlib.redirect_stdout(io.StringIO()):
            authorize(self.root, renew=renew, now=self.now,
                      input_fn=lambda prompt: self.expiry,
                      secret_fn=lambda: token,
                      client_factory=lambda t: FakeClient(t, identity))

    def test_dpapi_encrypted_and_no_credentials_in_stdout(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            authorize(self.root, now=self.now, input_fn=lambda prompt: self.expiry,
                      secret_fn=lambda: "synthetic-secret", client_factory=self.good)
        saved = token_path(self.root).read_bytes()
        self.assertNotIn(b"synthetic-secret", saved)
        self.assertNotIn("synthetic-secret", out.getvalue())
        self.assertEqual(load(self.root)[0]["token"], "synthetic-secret")

    def test_login_does_not_override_existing(self):
        self.authorize()
        existing = token_path(self.root).read_bytes()
        with self.assertRaises(SyncError):
            self.authorize("new-secret")
        self.assertEqual(existing, token_path(self.root).read_bytes())

    def test_renew_validates_then_replaces(self):
        self.authorize()
        self.authorize("replacement-secret", renew=True)
        self.assertEqual(load(self.root)[0]["token"], "replacement-secret")

    def test_rejected_renew_preserves_old_token(self):
        self.authorize()
        original = token_path(self.root).read_bytes()
        with self.assertRaises(SyncError):
            self.authorize("bad-token", renew=True)
        self.assertEqual(token_path(self.root).read_bytes(), original)

    def test_account_switch_preserves_old_token(self):
        self.authorize()
        original = token_path(self.root).read_bytes()
        with self.assertRaises(SyncError):
            self.authorize("alternate-secret", renew=True, identity=456)
        self.assertEqual(token_path(self.root).read_bytes(), original)

    def test_expiry_limit_and_warning(self):
        with self.assertRaises(SyncError):
            expiry_from_input((self.now + timedelta(days=91)).strftime("%Y-%m-%d %H:%M"), self.now)
        with self.assertRaises(SyncError):
            expiry_from_input("bad-date", self.now)
        self.authorize()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertTrue(status(self.root, client_factory=self.good, now=self.now))
        self.assertIn("15 días", out.getvalue())

    def test_allowlisted_api_host_and_no_external_links(self):
        for url in ("http://areandina.instructure.com/api/v1/courses",
                    "https://attacker.example/api/v1/courses",
                    "https://areandina.instructure.com.evil/api/v1/courses",
                    "https://areandina.instructure.com@evil.example/api/v1/courses",
                    "https://areandina.instructure.com/other"):
            with self.assertRaises(SyncError):
                check_url(url)
        self.assertEqual(check_url(ORIGIN + "/api/v1/courses"), ORIGIN + "/api/v1/courses")

    def test_page_link_and_loop_prevention(self):
        next_url = ORIGIN + "/api/v1/courses?page=2"
        first = PageResponse([{"id": 1}], f'<{next_url}>; rel="next"')
        opener = FakeOpener([first, PageResponse([{"id": 2}])])
        client = Client("synthetic-secret", opener=opener)
        self.assertEqual([x["id"] for x in client.pages("/api/v1/courses")], [1, 2])
        self.assertEqual(opener.methods, ["GET", "GET"])
        self.assertEqual(len(opener.urls), 2)
        self.assertEqual(next_page(f'<{next_url}>; rel="prev"'), None)
        with self.assertRaises(SyncError):
            next_page('<https://evil.example/api/v1/courses>; rel="next"')

    def test_http401_error_is_redacted(self):
        opener = FakeOpener([HTTPError(ORIGIN + "/api/v1/courses", 401,
                                       "invalid synthetic-secret", {}, None)])
        with self.assertRaises(SyncError) as error:
            Client("synthetic-secret", opener=opener).get(ORIGIN + "/api/v1/courses")
        self.assertNotIn("synthetic-secret", str(error.exception))
        self.assertIn("401", str(error.exception))

    def test_api_does_not_follow_redirect(self):
        from canvas_sync.api import BlockRedirect
        opener = Client("secret").opener
        blocker = next(h for h in opener.handlers if isinstance(h, BlockRedirect))
        with self.assertRaises(SyncError):
            blocker.redirect_request(
                None, None, 302, "redirect", {}, "https://evil.example")

    def test_sync_diff_and_idempotency(self):
        self.authorize()
        with contextlib.redirect_stdout(io.StringIO()):
            result = sync(self.root, 82862, client_factory=self.good)
            second = sync(self.root, 82862, client_factory=self.good)
        self.assertTrue(result["changed"])
        self.assertFalse(second["changed"])
        current = json.loads((self.root / "procesamiento" / "canvas-sync" / "82862" / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(current["data"]["assignments"][0]["submission"]["submitted_at"],
                         "2026-09-26T12:05:00-05:00")
        self.assertEqual(current["sha256"], sha(current["data"]))
        self.assertEqual(len(list((self.root / "procesamiento" / "canvas-sync" / "82862" / "snapshots").glob("*.json"))), 1)
        self.assertFalse((self.root / "database.xlsx").exists())
        self.assertFalse((self.root / "academic-manager.sqlite").exists())
        self.assertNotIn("synthetic-secret", json.dumps(current))

    def test_sync_does_not_write_on_invalid_assignment(self):
        self.authorize()
        class Invalid(FakeClient):
            def assignments(self, course_id):
                return [{"name": "No id"}]
        with self.assertRaises(SyncError):
            sync(self.root, 82862, client_factory=Invalid)
        self.assertFalse((self.root / "procesamiento" / "canvas-sync").exists())

    def test_sync_diff_modified_and_removed(self):
        original = {"course": {"id": 1}, "assignments": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]}
        updated = {"course": {"id": 1}, "assignments": [{"id": 2, "name": "new"}, {"id": 3, "name": "c"}]}
        delta = compare(original, updated)
        self.assertEqual(delta["added"], [3])
        self.assertEqual(delta["removed"], [1])
        self.assertEqual(delta["modified"], [2])
        self.assertFalse(delta["course_changed"])


if __name__ == "__main__":
    unittest.main()
