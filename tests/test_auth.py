import contextlib
import io
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from capture.capture import parser, run
from capture.models import CaptureError
from capture.sources import auth
from capture.storage.metadata import atomic_json


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("CAPTURE_TEST_ROOT"))
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.client = self.root / "client.json"
        atomic_json(self.client, {"installed": {
            "client_id": "synthetic.apps.googleusercontent.com", "client_secret": "not-a-real-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}})
        self.credentials = MagicMock()
        self.credentials.scopes = list(auth.SCOPES)
        self.credentials.granted_scopes = None
        self.credentials.refresh_token = "synthetic-refresh-token"
        self.credentials.valid = True
        self.credentials.to_json.return_value = json.dumps({
            "token": "synthetic-access-token", "refresh_token": "synthetic-refresh-token",
            "scopes": auth.SCOPES})
        self.Credentials, self.Request, self.Flow = MagicMock(), MagicMock(), MagicMock()
        self.Credentials.from_authorized_user_info.return_value = self.credentials

    def tearDown(self):
        self.tmp.cleanup()

    def libraries(self):
        return patch.object(auth, "libraries", return_value=(self.Credentials, self.Request, self.Flow))

    def test_cli_login_does_not_create_a_job(self):
        self.Flow.from_client_config.return_value.run_local_server.return_value = self.credentials
        with self.libraries(), contextlib.redirect_stdout(io.StringIO()):
            result = run(parser().parse_args(["--runtime", str(self.runtime), "--login-drive",
                                             "--oauth-client", str(self.client)]))
        self.assertIsNone(result)
        self.assertTrue(auth.token_path(self.runtime).is_file())
        self.assertEqual(list((self.runtime / "procesamiento").iterdir()), [])
        options = self.Flow.from_client_config.call_args.kwargs
        self.assertEqual(options["scopes"], auth.SCOPES)
        self.assertTrue(options["autogenerate_code_verifier"])
        options = self.Flow.from_client_config.return_value.run_local_server.call_args.kwargs
        self.assertEqual(options["host"], "127.0.0.1")
        self.assertEqual(options["port"], 0)
        self.assertTrue(options["open_browser"])
        self.assertEqual(options["access_type"], "offline")

    def test_default_token_reused_without_browser(self):
        path = auth.token_path(self.runtime)
        auth.store_token(path, self.credentials)
        with self.libraries():
            result = auth.load_credentials(self.runtime)
        self.assertIs(result, self.credentials)
        self.Flow.from_client_config.assert_not_called()
        self.credentials.refresh.assert_not_called()

    def test_expired_token_refreshes_and_is_persisted(self):
        path = auth.token_path(self.runtime)
        auth.store_token(path, self.credentials)
        self.credentials.valid = False
        with self.libraries(), patch.object(auth, "store_token", wraps=auth.store_token) as save:
            auth.load_credentials(self.runtime)
        self.credentials.refresh.assert_called_once_with(self.Request.return_value)
        save.assert_called_once_with(path, self.credentials)

    def test_rejected_refresh_does_not_expose_tokens_or_replace_previous(self):
        path = auth.token_path(self.runtime)
        auth.store_token(path, self.credentials)
        previous = path.read_bytes()
        self.credentials.valid = False
        self.credentials.refresh.side_effect = ValueError("synthetic-refresh-token should not be logged")
        with self.libraries(), self.assertRaises(CaptureError) as raised:
            auth.load_credentials(self.runtime)
        self.assertNotIn("synthetic-refresh-token", str(raised.exception))
        self.assertEqual(path.read_bytes(), previous)

    def test_missing_client_and_missing_grant(self):
        with self.assertRaises(CaptureError):
            auth.login(self.runtime, None)
        self.Flow.from_client_config.return_value.run_local_server.return_value = self.credentials
        self.credentials.granted_scopes = []
        with self.libraries(), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(CaptureError):
            auth.login(self.runtime, self.client)
        self.assertFalse(auth.token_path(self.runtime).exists())

    def test_missing_refresh_token_does_not_save(self):
        self.Flow.from_client_config.return_value.run_local_server.return_value = self.credentials
        self.credentials.refresh_token = None
        with self.libraries(), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(CaptureError):
            auth.login(self.runtime, self.client)
        self.assertFalse(auth.token_path(self.runtime).exists())

    def test_untrusted_client_endpoint_never_opens_browser(self):
        atomic_json(self.client, {"installed": {
            "client_id": "synthetic.apps.googleusercontent.com",
            "auth_uri": "https://evil.example/auth",
            "token_uri": "https://oauth2.googleapis.com/token"}})
        with self.libraries(), self.assertRaises(CaptureError):
            auth.login(self.runtime, self.client)
        self.Flow.from_client_config.assert_not_called()

    def test_storage_protection_and_roundtrip(self):
        path = auth.token_path(self.runtime)
        auth.store_token(path, self.credentials)
        if os.name == "nt":
            self.assertNotIn(b"synthetic-refresh-token", path.read_bytes())
            content = auth.dpapi(path.read_bytes(), decrypt=True)
        else:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            content = path.read_bytes()
        self.assertEqual(json.loads(content)["refresh_token"], "synthetic-refresh-token")
        self.assertEqual([p.name for p in path.parent.iterdir()], [path.name])

    def test_absent_default_authorization_returns_none(self):
        with patch.object(auth, "libraries") as libraries:
            self.assertIsNone(auth.load_credentials(self.runtime))
        libraries.assert_not_called()



    def test_official_library_contract_without_network(self):
        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            self.skipTest("Instale requirements-drive.txt para probar el contrato con Google.")
        from urllib.parse import parse_qs, urlparse
        flow = InstalledAppFlow.from_client_config(
            json.loads(self.client.read_text(encoding="utf-8")),
            scopes=auth.SCOPES, autogenerate_code_verifier=True)
        flow.redirect_uri = "http://127.0.0.1:48765/"
        url, state = flow.authorization_url(access_type="offline", prompt="consent")
        query = parse_qs(urlparse(url).query)
        self.assertEqual(urlparse(url).hostname, "accounts.google.com")
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["scope"], [" ".join(auth.SCOPES)])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertTrue(state)
        self.assertTrue(query["code_challenge"])
        credentials = Credentials(
            token="synthetic-access-token", refresh_token="synthetic-refresh-token",
            token_uri="https://oauth2.googleapis.com/token",
            client_id="synthetic.apps.googleusercontent.com", client_secret="not-a-real-secret",
            scopes=auth.SCOPES,
            expiry=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1))
        path = auth.token_path(self.runtime)
        auth.store_token(path, credentials)
        with patch("google.auth.transport.requests.Request", side_effect=AssertionError("Unexpected network access")) as request:
            loaded = auth.load_credentials(self.runtime)
        request.assert_not_called()
        self.assertEqual(loaded.refresh_token, "synthetic-refresh-token")
        self.assertTrue(loaded.valid)


if __name__ == "__main__":
    unittest.main()