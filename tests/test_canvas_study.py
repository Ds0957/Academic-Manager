"""Synthetic tests for the GET-only study synchronizer."""
import hashlib
import io
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from unittest.mock import patch

from canvas_sync.common import SyncError
from canvas_sync.study import (EjeMarkup, SameHostRedirect, extract_document,
                               get_canvas_html, get_institutional_pdf, identify_ejes,
                               official_pdf, parse_eje, safe_reference, sync_study)

MAIN = "https://contenidos.areandina.edu.co/repo/course/eje1/Referente1.pdf"
READ = "https://contenidos.areandina.edu.co/repo/course/eje1/Lectura1.pdf"
HTML = f'''<contenido titulo-modulo="Curso" nombre-autor="Docente"
 referente-completo="{MAIN}" />
<acordeon url-referente="{MAIN}"></acordeon>
<lectura-recomendada titulo="Capítulo" url="{READ}" tipo-archivo="interno" />
<lectura-recomendada titulo="Libro" url="https://biblioteca.example/libro?secret=123" tipo-archivo="externo" />
<modal titulo="Repaso" url="https://externo.example/quiz?session=abc"/>
<video-capsula titulo="Video" video="https://youtube.com/watch?v=xyz"/>'''.encode("utf-8")


class MockAPI:
    def __init__(self, token):
        if token != "fake-token":
            raise AssertionError("Bad synthetic token")
        self.requests = 0
        self.calls = []

    def profile(self):
        self.calls.append("GET profile")
        return 10

    def courses(self):
        self.calls.append("GET courses")
        return [{"id": 82862}, {"id": 91003}]

    def pages(self, path, params=None):
        self.requests += 1
        self.calls.append("GET " + path)
        if "/91003/" in path:
            raise SyncError("Sin permisos para esta consulta (HTTP 403).")
        return [{"id": 100, "display_name": "eje1.html", "content-type": "text/html",
                 "size": len(HTML), "locked": False, "hidden": False},
                {"id": 999, "display_name": "actividad.pdf", "content-type": "application/pdf"}]

    def get(self, url):
        self.requests += 1
        self.calls.append("GET " + url)
        if url.endswith("/files/100"):
            return ({"id": 100, "content-type": "text/html", "size": len(HTML),
                     "locked": False, "url": "https://areandina.instructure.com/files/100/download?t=signed"},
                    "")
        raise AssertionError("Unexpected endpoint")


class StudySyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.auth = patch("canvas_sync.study.valid",
                          return_value=({"account_id": 10, "token": "fake-token"}, None))
        self.auth.start()
        self.addCleanup(self.auth.stop)

        from pypdf import PdfWriter
        output = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(output)
        self.pdf = output.getvalue()

    def test_parsing_study_html_and_sanitizing_external_urls(self):
        result = parse_eje(HTML)
        self.assertEqual(result["title"], "Curso")
        self.assertEqual(result["referente_url"], MAIN)
        self.assertEqual(len(result["readings"]), 2)
        self.assertEqual(result["readings"][0]["kind"], "institutional_pdf")
        self.assertEqual(result["readings"][1]["kind"], "external_link")
        self.assertNotIn("secret", result["readings"][1]["url"])
        self.assertEqual(len(result["media"]), 2)
        self.assertNotIn("session=", str(result["media"]))

    def test_host_allowlist_credentials_and_query_params(self):
        self.assertTrue(official_pdf(MAIN))
        for url in ("http://contenidos.areandina.edu.co/repo/c.pdf",
                    "https://contenidos.areandina.edu.co.evil.example/repo/c.pdf",
                    "https://user:pw@contenidos.areandina.edu.co/repo/c.pdf",
                    MAIN + "?download_token=x",
                    "https://contenidos.areandina.edu.co/other/c.pdf",
                    "https://evil.example/repo/c.pdf"):
            self.assertFalse(official_pdf(url), url)
        self.assertEqual(safe_reference("https://foo.example/book?k=secret#abc"),
                         "https://foo.example/book")
        with self.assertRaises(SyncError):
            SameHostRedirect().redirect_request(None, None, 302, "Redirige", {},
                                                 "https://evil.example/steal")

    def test_unique_reference_enforced(self):
        with self.assertRaises(SyncError):
            parse_eje(b"<contenido referente-completo='https://evil.example/a.pdf'>")
        with self.assertRaises(SyncError):
            parse_eje((f"<contenido referente-completo='{MAIN}' />"
                       "<acordeon url-referente='https://contenidos.areandina.edu.co/repo/b.pdf'/>").encode())

    def test_html_and_pdf_size_limits(self):
        with self.assertRaises(SyncError):
            get_canvas_html("https://evil.example/files/3/download", downloader=lambda _: HTML)
        with self.assertRaises(SyncError):
            get_canvas_html("https://areandina.instructure.com/files/3/download",
                            downloader=lambda _: b"x" * 151000)
        with self.assertRaises(SyncError):
            get_institutional_pdf(MAIN, downloader=lambda _: b"not a PDF")
        with self.assertRaises(SyncError):
            get_institutional_pdf(MAIN, downloader=lambda _: b"%PDF-" + b"x" * 35000000)

    def test_blank_pdf_extracts_without_ocr(self):
        result = extract_document(self.pdf)
        self.assertEqual(result["pages"], 1)
        self.assertEqual(result["status"], "no_text_layer")

    def test_duplicate_html_rejected(self):
        rows = [{"id": 1, "display_name": "eje1.html", "content-type": "text/html",
                 "size": 100}, {"id": 2, "display_name": "EJE1.html",
                 "content-type": "text/html", "size": 100}]
        with self.assertRaises(SyncError):
            identify_ejes(rows)

    def test_realistic_flow_does_not_duplicate_and_respects_403(self):
        clients = []
        def factory(token):
            c = MockAPI(token)
            clients.append(c)
            return c
        pdf_calls = []
        def pdf_fetch(url):
            pdf_calls.append(url)
            return self.pdf
        def html_fetch(url):
            self.assertTrue(url.startswith("https://areandina.instructure.com/files/100/"))
            return HTML
        with patch("builtins.print"):
            first = sync_study(self.root, all_active=True, client_factory=factory,
                               html_downloader=html_fetch, pdf_downloader=pdf_fetch)
            again = sync_study(self.root, all_active=True, client_factory=factory,
                               html_downloader=html_fetch, pdf_downloader=pdf_fetch)
        self.assertEqual(len(first), 2)
        self.assertEqual(first[0]["status"], "ok")
        self.assertTrue(first[0]["changed"])
        self.assertFalse(again[0]["changed"])
        self.assertEqual(first[1]["status"], "forbidden")
        self.assertEqual(len(pdf_calls), 2)
        self.assertEqual(again[0]["pdf_reutilizados"], 2)
        self.assertEqual(again[0]["pdf_descargados"], 0)
        self.assertFalse(again[0]["refresco_completo"])
        directory = self.root/"procesamiento"/"canvas-sync"/"study"/"82862"
        index = json.loads((directory/"index.json").read_text(encoding="utf-8"))
        from canvas_sync.sync import sha
        self.assertEqual(index["sha256"], sha(index["data"]))
        self.assertEqual(index["data"]["ejes"][0]["referente"]["sha256"], hashlib.sha256(self.pdf).hexdigest())
        self.assertEqual(len(list((directory/"snapshots").glob("*.json"))), 1)
        self.assertEqual(len(list((directory/"eje1").glob("referente-*.pdf"))), 1)
        self.assertTrue(all(x.startswith("GET") for c in clients for x in c.calls))
        self.assertNotIn("fake-token", json.dumps(index))
        self.assertNotIn("signed", json.dumps(index))
        self.assertFalse((self.root/"database.xlsx").exists())

    def test_periodic_validation_and_explicit_full_refresh(self):
        fetched=[]
        def pdf_fetch(url):
            fetched.append(url)
            return self.pdf
        options=dict(client_factory=MockAPI, html_downloader=lambda _:HTML,
                     pdf_downloader=pdf_fetch)
        time0=datetime(2026,10,8,10,tzinfo=timezone.utc)
        with patch("builtins.print"):
            first=sync_study(self.root,now_utc=time0,**options)
            two=sync_study(self.root,now_utc=time0+timedelta(days=2),**options)
            aged=sync_study(self.root,now_utc=time0+timedelta(days=8),**options)
            forced=sync_study(self.root,now_utc=time0+timedelta(days=8,minutes=5),
                              full_refresh=True,**options)
        self.assertEqual([x[0]["pdf_descargados"] for x in (first,two,aged,forced)],
                         [2,0,2,2])
        self.assertEqual(len(fetched),6)
        self.assertFalse(two[0]["changed"])
        self.assertFalse(aged[0]["changed"])
        self.assertTrue(aged[0]["refresco_completo"])
        refresh=json.loads((self.root/"procesamiento"/"canvas-sync"/"study"/"82862"/
                           "refresh-state.json").read_text(encoding="utf-8"))
        self.assertEqual(refresh["course_id"],82862)

    def test_cache_corruption_triggers_download(self):
        fetches=[]
        def pdf_fetch(url):
            fetches.append(url)
            return self.pdf
        options=dict(client_factory=MockAPI,html_downloader=lambda _:HTML,
                     pdf_downloader=pdf_fetch)
        with patch("builtins.print"):
            sync_study(self.root,**options)
        pdfs=list((self.root/"procesamiento"/"canvas-sync"/"study"/"82862").rglob("*.pdf"))
        self.assertEqual(len(pdfs),2)
        pdfs[0].write_bytes(b"damaged")
        with patch("builtins.print"):
            result=sync_study(self.root,**options)
        self.assertEqual(result[0]["pdf_descargados"],1)
        self.assertEqual(result[0]["pdf_reutilizados"],1)
        self.assertEqual(len(fetches),3)
        self.assertEqual(hashlib.sha256(pdfs[0].read_bytes()).hexdigest(),
                         hashlib.sha256(self.pdf).hexdigest())

    def test_changed_html_forces_recheck_but_preserves_index_on_failure(self):
        calls=[]
        changed=HTML.replace(b"Curso",b"Nuevo")
        def fetch_html(_):
            return changed
        def fetch_pdf(url):
            calls.append(url)
            if len(calls)==2:
                raise SyncError("Fallo PDF sintético")
            return self.pdf
        opts=dict(client_factory=MockAPI,html_downloader=lambda _:HTML,
                  pdf_downloader=lambda _:self.pdf)
        with patch("builtins.print"):
            sync_study(self.root,**opts)
        index=self.root/"procesamiento"/"canvas-sync"/"study"/"82862"/"index.json"
        before=index.read_bytes()
        with patch("builtins.print"):
            with self.assertRaises(SyncError):
                sync_study(self.root,client_factory=MockAPI,
                           html_downloader=fetch_html,pdf_downloader=fetch_pdf)
        self.assertEqual(index.read_bytes(),before)
        with patch("builtins.print"):
            outcome=sync_study(self.root,client_factory=MockAPI,
                               html_downloader=fetch_html,pdf_downloader=lambda _:self.pdf)
        self.assertTrue(outcome[0]["changed"])
        self.assertEqual(outcome[0]["pdf_descargados"],2)

    def test_approved_only_skips_unconfirmed_canvas_courses(self):
        selected={"courses":[{"course_id":82862,"subject_id":"MAT-GBD"}]}
        fetched=[]
        with patch("canvas_sync.study.load_registry",return_value=selected), \
             patch("builtins.print"):
            outcome=sync_study(self.root, all_active=True, approved_only=True,
                               client_factory=MockAPI, html_downloader=lambda _:HTML,
                               pdf_downloader=lambda url:(fetched.append(url) or self.pdf))
        self.assertEqual([x["course_id"] for x in outcome],[82862])
        self.assertEqual(len(fetched),2)
        with self.assertRaises(SyncError):
            sync_study(self.root,approved_only=True,client_factory=MockAPI)

    def test_legacy_text_verified_offline_before_reuse(self):
        produced={"status":"text","pages":1,"text":"Contenido legítimo",
                  "truncated":False}
        with patch("builtins.print"), \
             patch("canvas_sync.study.extract_document",return_value=produced):
            first=sync_study(self.root,client_factory=MockAPI,
                             html_downloader=lambda _:HTML,
                             pdf_downloader=lambda _:self.pdf)
        folder=self.root/"procesamiento"/"canvas-sync"/"study"/"82862"
        index=folder/"index.json"
        record=json.loads(index.read_text(encoding="utf-8"))
        eje=record["data"]["ejes"][0]
        for meta in (eje["referente"],eje["readings"][0]["document"]):
            meta.pop("text_sha256")
        from canvas_sync.sync import sha
        record["sha256"]=sha(record["data"])
        index.write_text(json.dumps(record),encoding="utf-8")
        refetched=[]
        with patch("builtins.print"), \
             patch("canvas_sync.study.extract_document",return_value=produced):
            second=sync_study(self.root,client_factory=MockAPI,
                              html_downloader=lambda _:HTML,
                              pdf_downloader=lambda url:(refetched.append(url) or self.pdf))
        self.assertEqual(refetched,[])
        self.assertEqual(second[0]["pdf_reutilizados"],2)
        record=json.loads(index.read_text(encoding="utf-8"))
        self.assertTrue(record["data"]["ejes"][0]["referente"]["text_sha256"])

    def test_invalid_prior_index_fails_without_replacement(self):
        opts=dict(client_factory=MockAPI,html_downloader=lambda _:HTML,
                  pdf_downloader=lambda _:self.pdf)
        with patch("builtins.print"):
            sync_study(self.root,**opts)
        path=self.root/"procesamiento"/"canvas-sync"/"study"/"82862"/"index.json"
        record=json.loads(path.read_text(encoding="utf-8"))
        record["sha256"]="0"*64
        path.write_text(json.dumps(record),encoding="utf-8")
        with patch("builtins.print"), self.assertRaises(SyncError):
            sync_study(self.root,**opts)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["sha256"],"0"*64)

    def test_invalid_source_prevents_index_publication(self):
        def malformed(url):
            return b"<contenido referente-completo='https://evil.example/a.pdf'>"
        with self.assertRaises(SyncError):
            sync_study(self.root, client_factory=MockAPI, html_downloader=malformed,
                       pdf_downloader=lambda _: self.pdf)
        self.assertFalse((self.root/"procesamiento"/"canvas-sync"/"study"/"82862"/"index.json").exists())


if __name__ == "__main__":
    unittest.main()
