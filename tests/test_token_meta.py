"""S7a: metadatos NO secretos del vencimiento del token Canvas (sin DPAPI real, sin red)."""
import contextlib
from datetime import date, datetime, timedelta
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from canvas_sync import auth
from canvas_sync.__main__ import main
from canvas_sync.common import SyncError
from canvas_sync.routine import token_block

SECRET = "synthetic-secret-TOKEN-0123456789"


def fake_dpapi(data, decrypt=False):
    # Transformación reversible para pruebas fuera de Windows; no es cifrado.
    return bytes(b ^ 0x5A for b in data)


class Client:
    def __init__(self, token, identity=123):
        self.token = token

    def profile(self):
        return 123


class TokenMetaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "runtime"
        self.root.mkdir()
        self.now = datetime.now().astimezone().replace(second=0, microsecond=0)
        for target in (patch.object(auth, "os", SimpleNamespace(name="nt")),
                       patch.object(auth, "dpapi", fake_dpapi)):
            target.start()
            self.addCleanup(target.stop)

    @property
    def meta(self):
        return self.root / "control" / "canvas-sync" / auth.META_FILE

    def authorize(self, days, token=SECRET, renew=False):
        expiry = (self.now + timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            auth.authorize(self.root, renew=renew, now=self.now, input_fn=lambda prompt: expiry,
                           secret_fn=lambda: token, client_factory=Client)
        self.assertNotIn(token, out.getvalue())
        return datetime.strptime(expiry, "%Y-%m-%d %H:%M").replace(tzinfo=self.now.tzinfo)

    def assert_meta(self, expiry, token=SECRET):
        raw = self.meta.read_bytes()
        self.assertNotIn(token.encode(), raw)
        value = json.loads(raw)
        self.assertEqual(set(value), {"schema_version", "expires_at", "saved_at"})
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(datetime.fromisoformat(value["expires_at"]), expiry)
        self.assertIsNotNone(datetime.fromisoformat(value["saved_at"]).tzinfo)
        self.assertEqual(auth.read_meta(self.root), expiry)

    def test_login_writes_expiry_metadata_without_token(self):
        expiry = self.authorize(40)
        self.assert_meta(expiry)
        self.assertEqual(auth.load(self.root)[0]["token"], SECRET)

    def test_renew_refreshes_metadata(self):
        self.authorize(40)
        replacement = "replacement-TOKEN-abcdef987654"
        expiry = self.authorize(80, token=replacement, renew=True)
        self.assert_meta(expiry, token=replacement)
        self.assertNotIn(SECRET.encode(), self.meta.read_bytes())

    def test_status_backfills_missing_metadata(self):
        expiry = self.authorize(40)
        self.meta.unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(auth.status(self.root, client_factory=Client, now=self.now))
        self.assert_meta(expiry)

    def test_status_cli_backfills_and_keeps_exit_code(self):
        expiry = self.authorize(40)
        self.meta.write_text("{broken", encoding="utf-8")
        with patch("canvas_sync.api.Client.profile", return_value=123), contextlib.redirect_stdout(io.StringIO()) as out:
            code = main(["status", "--runtime", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("API Canvas: autorizada", out.getvalue())
        self.assert_meta(expiry)

    def test_expired_token_still_fails_validation(self):
        self.authorize(1)
        with self.assertRaisesRegex(SyncError, "Token vencido"):
            auth.valid(self.root, now=self.now + timedelta(days=2))


class TokenNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "runtime"
        self.root.mkdir()
        self.now = datetime(2026, 10, 22, 9, 0).astimezone()

    def write_meta(self, expiry):
        folder = self.root / "control" / "canvas-sync"
        folder.mkdir(parents=True, exist_ok=True)
        auth.save_meta(self.root, expiry)

    def test_more_than_15_days_has_no_warning(self):
        self.write_meta(self.now + timedelta(days=40))
        block = token_block(self.root, now=self.now)
        self.assertEqual(block, {"vence": (self.now + timedelta(days=40)).date().isoformat(),
                                 "dias_restantes": 40, "aviso": None})

    def test_15_days_or_less_warns(self):
        self.write_meta(self.now + timedelta(days=15))
        block = token_block(self.root, now=self.now)
        self.assertEqual(block["dias_restantes"], 15)
        self.assertIn("15 días", block["aviso"])
        self.write_meta(self.now + timedelta(days=16))
        self.assertIsNone(token_block(self.root, now=self.now)["aviso"])

    def test_expired_warns(self):
        self.write_meta(self.now - timedelta(hours=1))
        block = token_block(self.root, now=self.now)
        self.assertEqual(block["dias_restantes"], 0)
        self.assertIn("vencido", block["aviso"])
        self.write_meta(self.now - timedelta(days=3))
        self.assertEqual(token_block(self.root, now=self.now)["dias_restantes"], -3)

    def test_missing_metadata_is_reported_without_creating_directories(self):
        block = token_block(self.root, now=self.now)
        self.assertEqual((block["vence"], block["dias_restantes"]), (None, None))
        self.assertIn("status", block["aviso"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_malformed_metadata_is_reported(self):
        folder = self.root / "control" / "canvas-sync"
        folder.mkdir(parents=True)
        for content in ("{broken", json.dumps({"schema_version": 1, "expires_at": "2026-11-01T00:00"}),
                        json.dumps({"schema_version": 2, "expires_at": "2026-11-01T00:00:00-05:00"})):
            (folder / auth.META_FILE).write_text(content, encoding="utf-8")
            block = token_block(self.root, now=self.now)
            self.assertEqual((block["vence"], block["dias_restantes"]), (None, None))
            self.assertIn("ilegibles", block["aviso"])

    def test_notice_counts_calendar_days_in_expiry_offset(self):
        expiry = datetime.fromisoformat("2026-11-06T23:30:00-05:00")
        self.assertEqual(auth.notice(expiry, date(2026, 10, 22)),
                         {"vence": "2026-11-06", "dias_restantes": 15,
                          "aviso": "Token Canvas vence en 15 días: prepare la renovación con renew."})


if __name__ == "__main__":
    unittest.main()
