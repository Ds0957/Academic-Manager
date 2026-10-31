"""Publicación con el inicio de sesión propio (alcance drive) sin tocar la cuenta de servicio.

Todo es sintético: las librerías de Google se sustituyen en sys.modules y en `auth.libraries`.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from canvas_sync import workbook_blocks
from canvas_sync.common import SyncError
from capture.academic_manager import drive_workbook
from capture.models import CaptureError
from capture.sources import auth
from capture.storage.metadata import atomic_json

READ = "https://www.googleapis.com/auth/drive.readonly"
WRITE = "https://www.googleapis.com/auth/drive"


def google_modules():
    """Módulos falsos de google-auth/googleapiclient; devuelve (módulos, service_account, build)."""
    service_account = types.ModuleType("google.oauth2.service_account")
    service_account.Credentials = MagicMock()
    service_account.Credentials.from_service_account_file.return_value = "sa-credentials"
    oauth2 = types.ModuleType("google.oauth2")
    oauth2.service_account = service_account
    google = types.ModuleType("google")
    google.oauth2 = oauth2
    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = MagicMock(return_value="drive-api")
    client = types.ModuleType("googleapiclient")
    client.discovery = discovery
    modules = {"google": google, "google.oauth2": oauth2, "google.oauth2.service_account": service_account,
               "googleapiclient": client, "googleapiclient.discovery": discovery}
    return modules, service_account.Credentials, discovery.build


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        (self.runtime / "control").mkdir(parents=True)
        self.client = self.root / "client.json"
        atomic_json(self.client, {"installed": {
            "client_id": "synthetic.apps.googleusercontent.com", "client_secret": "not-a-real-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}})
        self.credentials = self.make_credentials([READ, WRITE])
        self.Credentials, self.Request, self.Flow = MagicMock(), MagicMock(), MagicMock()
        self.Credentials.from_authorized_user_info.return_value = self.credentials
        self.Flow.from_client_config.return_value.run_local_server.return_value = self.credentials

    @staticmethod
    def make_credentials(scopes):
        credentials = MagicMock()
        credentials.scopes = list(scopes)
        credentials.granted_scopes = list(scopes)
        credentials.refresh_token = "synthetic-refresh-token"
        credentials.valid = True
        credentials.to_json.return_value = json.dumps({
            "token": "synthetic-access-token", "refresh_token": "synthetic-refresh-token",
            "scopes": list(scopes)})
        return credentials

    def libraries(self):
        return patch.object(auth, "libraries", return_value=(self.Credentials, self.Request, self.Flow))

    def login(self, **kw):
        with self.libraries(), contextlib.redirect_stdout(io.StringIO()) as out:
            auth.login(self.runtime, self.client, **kw)
        return out.getvalue()


class WriteLoginTests(Case):
    def test_wizard_login_requests_drive_and_keeps_capture_token(self):
        out = self.login(write=True)
        self.assertEqual(self.Flow.from_client_config.call_args.kwargs["scopes"], [READ, WRITE])
        write_path = auth.write_token_path(self.runtime)
        self.assertEqual(write_path.parent, auth.token_path(self.runtime).parent)
        self.assertEqual(write_path.name, "drive-write-token.dpapi" if os.name == "nt" else "drive-write-token.json")
        for path in (write_path, auth.token_path(self.runtime)):
            self.assertTrue(path.is_file())
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("synthetic-refresh-token", out)
        self.assertIn("guardar", out)

    def test_wizard_login_without_drive_permission_saves_nothing(self):
        self.credentials.granted_scopes = [READ]
        with self.assertRaises(CaptureError) as caught:
            self.login(write=True)
        self.assertIn("guardar archivos de Drive", str(caught.exception))
        self.assertFalse(auth.write_token_path(self.runtime).exists())
        self.assertFalse(auth.token_path(self.runtime).exists())

    def test_console_login_is_unchanged(self):
        """Regresión: --login-drive sigue pidiendo solo lectura y no crea el token de escritura."""
        self.credentials.granted_scopes = [READ]
        out = self.login()
        self.assertEqual(self.Flow.from_client_config.call_args.kwargs["scopes"], [READ])
        self.assertTrue(auth.token_path(self.runtime).is_file())
        self.assertFalse(auth.write_token_path(self.runtime).exists())
        self.assertIn("autorice lectura/descarga de Drive", out)

    def test_existing_read_only_token_keeps_validating(self):
        """Regresión: un token de Capture solo de lectura se sigue aceptando sin volver a iniciar sesión."""
        old = self.make_credentials([READ])
        auth.store_token(auth.token_path(self.runtime), old)
        self.Credentials.from_authorized_user_info.return_value = old
        with self.libraries():
            self.assertIs(auth.load_credentials(self.runtime), old)
            self.assertIsNone(auth.load_write_credentials(self.runtime))
        self.assertEqual(self.Credentials.from_authorized_user_info.call_args.kwargs["scopes"], [READ])

    def test_write_credentials_load_refresh_and_persist(self):
        self.login(write=True)
        path = auth.write_token_path(self.runtime)
        self.credentials.valid = False
        with self.libraries(), patch.object(auth, "store_token", wraps=auth.store_token) as save:
            self.assertIs(auth.load_write_credentials(self.runtime), self.credentials)
        self.assertEqual(self.Credentials.from_authorized_user_info.call_args.kwargs["scopes"], [READ, WRITE])
        save.assert_called_once_with(path, self.credentials)

    def test_write_token_without_drive_scope_is_rejected(self):
        auth.store_token(auth.write_token_path(self.runtime), self.make_credentials([READ]))
        with self.libraries(), self.assertRaises(CaptureError) as caught:
            auth.load_write_credentials(self.runtime)
        self.assertIn("no tiene permiso para guardar en Drive", str(caught.exception))
        self.assertIn("Volver a iniciar sesión", str(caught.exception))
        self.assertNotIn("caducó", str(caught.exception))

    def test_no_oauth_folder_means_no_write_login_and_no_side_effect(self):
        with patch.object(auth, "libraries") as libraries:
            self.assertIsNone(auth.load_write_credentials(self.runtime))
        libraries.assert_not_called()
        self.assertFalse((self.runtime / "control" / "drive-oauth").exists())


class PublicationIdentityTests(Case):
    def service(self):
        modules, sa, build = google_modules()
        with patch.dict(sys.modules, modules), self.libraries():
            try:
                return workbook_blocks._drive_service(self.runtime), sa, build
            except SyncError as exc:
                return exc, sa, build

    def test_service_account_wins_even_with_a_write_login(self):
        """Regresión: con la cuenta de servicio se usa la misma credencial, alcances y archivo de siempre."""
        secret = self.runtime / "control" / "service-account-drive.json"
        secret.write_text("{}", encoding="utf-8")
        self.login(write=True)
        self.Credentials.reset_mock()
        api, sa, build = self.service()
        self.assertEqual(api, "drive-api")
        sa.from_service_account_file.assert_called_once_with(str(secret), scopes=drive_workbook.SCOPES)
        build.assert_called_once_with("drive", "v3", credentials="sa-credentials", cache_discovery=False)
        self.Credentials.from_authorized_user_info.assert_not_called()

    def test_without_service_account_the_write_login_publishes(self):
        self.login(write=True)
        api, sa, build = self.service()
        self.assertEqual(api, "drive-api")
        sa.from_service_account_file.assert_not_called()
        build.assert_called_once_with("drive", "v3", credentials=self.credentials, cache_discovery=False)

    def test_read_only_login_alone_cannot_publish(self):
        self.credentials.granted_scopes = [READ]
        self.login()
        error, sa, build = self.service()
        self.assertIsInstance(error, SyncError)
        self.assertEqual(str(error), "No hay identidad técnica local válida para publicar.")
        build.assert_not_called()

    def test_nothing_configured_keeps_the_same_error(self):
        error, _, build = self.service()
        self.assertEqual(str(error), "No hay identidad técnica local válida para publicar.")
        build.assert_not_called()
        self.assertFalse((self.runtime / "control" / "drive-oauth").exists())

    def test_redirected_service_account_never_falls_back(self):
        self.login(write=True)
        target = self.root / "elsewhere.json"
        target.write_text("{}", encoding="utf-8")
        (self.runtime / "control" / "service-account-drive.json").symlink_to(target)
        error, sa, build = self.service()
        self.assertEqual(str(error), "No hay identidad técnica local válida para publicar.")
        build.assert_not_called()

    def test_expired_write_login_explains_how_to_renew(self):
        self.login(write=True)
        self.credentials.valid = False
        self.credentials.refresh.side_effect = ValueError("synthetic-refresh-token revoked")
        error, _, _ = self.service()
        self.assertIsInstance(error, SyncError)
        self.assertIn("Volver a iniciar sesión", str(error))
        self.assertNotIn("synthetic-refresh-token", str(error))


if __name__ == "__main__":
    unittest.main()
