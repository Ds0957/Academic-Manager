"""academic_app.updates: aviso de versión nueva y descarga verificada del instalador.

Sin red real: la consulta a GitHub (fetch) y la descarga (opener) se simulan; los archivos
descargados van a una carpeta temporal fuera del repositorio.
"""
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

from academic_app import updates

DATA = b"MZ synthetic installer bytes " * 64
SHA = hashlib.sha256(DATA).hexdigest()
ASSET_URL = ("https://github.com/Ds0957/Academic-Manager/releases/download/v0.2.0/"
             "AcademicManager-Setup-0.2.0.exe")
FINAL_URL = "https://release-assets.githubusercontent.com/github-production-release-asset/1/abc?sp=r"


def release_json(tag="v0.2.0", name=None, digest=None, url=ASSET_URL, size=None, extra_assets=()):
    asset = {"name": name or f"AcademicManager-Setup-{tag[1:]}.exe", "size": len(DATA) if size is None else size,
             "browser_download_url": url, "digest": f"sha256:{SHA}" if digest is None else digest}
    if digest is False:
        del asset["digest"]
    return json.dumps({"tag_name": tag, "assets": [*extra_assets, asset]}).encode()


class FakeFetch:
    def __init__(self, body=None, error=None):
        self.body, self.error, self.calls = body, error, []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        if self.error:
            raise self.error
        return self.body


class CheckTests(unittest.TestCase):
    def check(self, body=None, current="0.1.0", error=None):
        self.fetch = FakeFetch(body, error)
        self.logged = []
        return updates.check(current, fetch=self.fetch, log=self.logged.append)

    def test_newer_release_is_reported_with_url_digest_and_size(self):
        release = self.check(release_json())
        self.assertEqual((release.version, release.url, release.sha256, release.size),
                         ("0.2.0", ASSET_URL, SHA, len(DATA)))

    def test_asks_latest_release_of_public_repo_with_headers_and_short_timeout(self):
        self.check(release_json())
        url, headers, timeout = self.fetch.calls[0]
        self.assertEqual(url, "https://api.github.com/repos/Ds0957/Academic-Manager/releases/latest")
        self.assertEqual(headers["Accept"], "application/vnd.github+json")
        self.assertIn("0.1.0", headers["User-Agent"])
        self.assertLessEqual(timeout, 5)

    def test_same_version_is_up_to_date(self):
        self.assertIsNone(self.check(release_json("v0.1.0")))

    def test_older_version_is_not_offered(self):
        self.assertIsNone(self.check(release_json("v0.0.9"), current="0.1.0"))

    def test_versions_compare_as_numbers(self):
        self.assertEqual(self.check(release_json("v0.10.0"), current="0.9.0").version, "0.10.0")

    def test_no_network_timeout_http_error_and_rate_limit_give_nothing(self):
        errors = (urllib.error.URLError("sin red"), TimeoutError("timed out"),
                  urllib.error.HTTPError("u", 404, "Not Found", {}, None),
                  urllib.error.HTTPError("u", 403, "rate limit exceeded", {}, None))
        for error in errors:
            if isinstance(error, urllib.error.HTTPError):
                self.addCleanup(error.close)
            with self.subTest(error=type(error).__name__):
                self.assertIsNone(self.check(error=error))
                self.assertEqual(len(self.logged), 1)
                self.assertIn(type(error).__name__, self.logged[0])
                self.assertNotIn(str(error), self.logged[0])

    def test_invalid_json_gives_nothing(self):
        for body in (b"{", b"[]", b'"x"', json.dumps({"tag_name": "v0.2.0"}).encode()):
            with self.subTest(body=body):
                self.assertIsNone(self.check(body))

    def test_tag_must_be_plain_vX_Y_Z(self):
        for tag in ("0.2.0", "v0.2", "v0.2.0-rc.1", "v0.2.0 ", "release-0.2.0"):
            with self.subTest(tag=tag):
                self.assertIsNone(self.check(release_json(tag, name="AcademicManager-Setup-0.2.0.exe")))

    def test_release_without_the_installer_asset_gives_nothing(self):
        self.assertIsNone(self.check(release_json(name="AcademicManager-Setup-0.1.9.exe")))
        self.assertIsNone(self.check(release_json(name="otro.zip")))

    def test_missing_or_invalid_digest_gives_nothing(self):
        for digest in (False, "", "md5:abc", "sha256:abc", f"sha512:{SHA}", f"sha256:{SHA.upper()}x"):
            with self.subTest(digest=digest):
                self.assertIsNone(self.check(release_json(digest=digest)))

    def test_asset_not_on_https_github_gives_nothing(self):
        for url in (ASSET_URL.replace("https:", "http:"), "https://evil.example/AcademicManager-Setup-0.2.0.exe"):
            with self.subTest(url=url):
                self.assertIsNone(self.check(release_json(url=url)))

    def test_unreadable_current_version_gives_nothing(self):
        self.assertIsNone(self.check(release_json(), current="desarrollo"))


class FakeResponse(io.BytesIO):
    def __init__(self, data, url, length=None, fail_after=None):
        super().__init__(data)
        self.url, self.fail_after = url, fail_after
        self.headers = {} if length is None else {"Content-Length": str(length)}

    def geturl(self):
        return self.url

    def read(self, size=-1):
        if self.fail_after is not None and self.tell() >= self.fail_after:
            raise ConnectionResetError("cortado")
        return super().read(size)


class FakeOpener:
    def __init__(self, data=DATA, url=FINAL_URL, **kw):
        self.data, self.url, self.kw, self.calls = data, url, kw, []

    def open(self, request, timeout):
        self.calls.append((request.full_url, timeout))
        return FakeResponse(self.data, self.url, **self.kw)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()) / "AcademicManager-actualizacion"
        self.addCleanup(shutil.rmtree, self.folder.parent, True)
        self.release = updates.Release("0.2.0", ASSET_URL, SHA, len(DATA))

    def download(self, release=None, **kw):
        self.opener = FakeOpener(**kw)
        return updates.download(release or self.release, self.folder, opener=self.opener)

    def assert_nothing_left(self):
        self.assertEqual(list(self.folder.glob("*")) if self.folder.exists() else [], [])

    def assert_rejected(self, release=None, **kw):
        with self.assertRaises(updates.UpdateError) as caught:
            self.download(release, **kw)
        self.assert_nothing_left()
        message = str(caught.exception)
        for raw in ("Error", "http", "sha256", "cortado"):
            self.assertNotIn(raw, message)
        return message

    def test_verified_download_is_renamed_to_the_installer(self):
        path = self.download()
        self.assertEqual(path, self.folder / "AcademicManager-Setup-0.2.0.exe")
        self.assertEqual(path.read_bytes(), DATA)
        self.assertEqual([p.name for p in self.folder.iterdir()], [path.name])
        self.assertEqual(self.opener.calls[0][0], ASSET_URL)

    def test_http_or_foreign_asset_url_is_rejected_without_downloading(self):
        for url in (ASSET_URL.replace("https:", "http:"), "https://evil.example/x.exe",
                    "https://github.com.evil.example/x.exe", "https://objects.githubusercontent.com/x.exe"):
            with self.subTest(url=url):
                self.assert_rejected(updates.Release("0.2.0", url, SHA, len(DATA)))
                self.assertEqual(self.opener.calls, [])

    def test_redirect_to_http_or_foreign_host_is_rejected(self):
        for final in ("http://release-assets.githubusercontent.com/x", "https://evil.example/x"):
            with self.subTest(final=final):
                self.assert_rejected(url=final)

    def test_redirect_handler_only_follows_https_allowed_hosts(self):
        handler = updates.SafeRedirect()
        request = updates.urllib.request.Request(ASSET_URL)
        for target in ("http://objects.githubusercontent.com/x", "https://evil.example/x",
                       "https://githubusercontent.com.evil.example/x"):
            with self.subTest(target=target), self.assertRaises(updates.UpdateError):
                handler.redirect_request(request, None, 302, "Found", {}, target)
        for target in ("https://objects.githubusercontent.com/x", "https://release-assets.githubusercontent.com/x"):
            with self.subTest(target=target):
                self.assertEqual(handler.redirect_request(request, None, 302, "Found", {}, target).full_url,
                                 target)

    def test_size_over_the_cap_is_rejected_without_downloading(self):
        self.assert_rejected(updates.Release("0.2.0", ASSET_URL, SHA, updates.MAX_BYTES + 1))
        self.assertEqual(self.opener.calls, [])

    def test_more_bytes_than_announced_is_rejected(self):
        self.assert_rejected(data=DATA + b"extra")
        self.assert_rejected(length=len(DATA) + 1)

    def test_digest_mismatch_deletes_the_file(self):
        message = self.assert_rejected(data=DATA[:-1] + b"X")
        self.assertIn("no se ejecutó", message)

    def test_cut_download_deletes_the_partial_file(self):
        self.assert_rejected(fail_after=100)

    def test_download_folder_is_in_temp_not_in_the_program(self):
        folder = updates.download_folder()
        self.assertEqual(folder.parent, Path(tempfile.gettempdir()))
        self.assertEqual(folder.name, "AcademicManager-actualizacion")


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.exe = self.folder / "AcademicManager-Setup-0.2.0.exe"
        self.exe.write_bytes(DATA)

    def test_opens_the_installer_with_the_normal_window(self):
        start = MagicMock()
        updates.launch(self.exe, start=start)
        start.assert_called_once_with(str(self.exe))

    def test_outside_windows_says_it_only_works_on_windows(self):
        if os.name == "nt":
            self.skipTest("solo fuera de Windows")
        with self.assertRaises(updates.UpdateError) as caught:
            updates.launch(self.exe)
        self.assertIn("solo en Windows", str(caught.exception))

    def test_failure_to_open_is_plain(self):
        with self.assertRaises(updates.UpdateError) as caught:
            updates.launch(self.exe, start=MagicMock(side_effect=OSError(1155, "sin asociación")))
        self.assertNotIn("sin asociación", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
