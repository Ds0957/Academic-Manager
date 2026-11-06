"""Claude Desktop connector (docs/43): read tools over the existing access contract."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from academic_access import core
from academic_config import paths
from academic_mcp import tools
from canvas_sync.common import StoreBusy, SyncError

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path("/runtime-que-no-existe")
RPC_TIMEOUT = 30  # seconds per request in the stdio tests; a hang fails instead of blocking the suite
READ_TOOLS = {"ponerme_al_dia", "hoy", "resumen", "materias", "materia", "actividades", "clases",
              "notas", "tareas", "buscar", "leer_fuente", "anomalias", "mapa", "indice_clase"}
WRITE_TOOLS = {"guardar_mapa", "guardar_indice_clase"}  # preview → plan_id → approval (tests/test_mcp_map.py)
EXPECTED = READ_TOOLS | WRITE_TOOLS


def rows(n, **extra):
    return [{"n": i, "fecha": date(2027, 3, 1), **extra} for i in range(n)]


class RegistryTests(unittest.TestCase):
    def test_every_tool_is_registered_with_its_access_and_a_closed_schema(self):
        self.assertEqual(set(tools.TOOLS), EXPECTED)
        for name, tool in tools.TOOLS.items():
            self.assertRegex(name, r"^[a-z_]+$")
            self.assertIs(tool.read_only, name in READ_TOOLS, name)
            self.assertGreater(len(tool.description), 40, name)
            self.assertEqual(tool.schema["type"], "object", name)
            self.assertIs(tool.schema["additionalProperties"], False, name)
            json.dumps(tool.schema)

    def test_start_of_chat_tool_says_to_call_it_first(self):
        self.assertIn("al empezar", tools.TOOLS["ponerme_al_dia"].description.lower())

    def test_subject_ids_and_bounds_follow_the_cli(self):
        self.assertEqual(tools.TOOLS["materia"].schema["required"], ["materia"])
        pattern = tools.TOOLS["materia"].schema["properties"]["materia"]["pattern"]
        self.assertRegex("MAT-ING3", pattern)
        self.assertNotRegex("ING3", pattern)
        dias = tools.TOOLS["hoy"].schema["properties"]["dias"]
        self.assertEqual((dias["minimum"], dias["maximum"]), (1, 60))
        limite = tools.TOOLS["actividades"].schema["properties"]["limite"]
        self.assertEqual((limite["minimum"], limite["maximum"]), (1, core.MAX_ITEMS))
        self.assertEqual(tools.TOOLS["buscar"].schema["properties"]["limite"]["maximum"], 30)
        self.assertEqual(tools.TOOLS["leer_fuente"].schema["properties"]["length"]["maximum"],
                         core.MAX_EXCERPT)


class DispatchTests(unittest.TestCase):
    """Each tool calls the existing reader with the CLI's defaults."""

    def check(self, name, arguments, target, expected_args, expected_kwargs):
        with patch(target, return_value={"fecha": date(2027, 3, 10), "items": ()}) as reader:
            result = tools.call(name, arguments, RUNTIME)
        reader.assert_called_once_with(RUNTIME, *expected_args, **expected_kwargs)
        self.assertEqual(result, {"fecha": "2027-03-10", "items": []})
        json.dumps(result, allow_nan=False)

    def test_readers_and_arguments(self):
        cases = [
            ("hoy", {}, "academic_access.today.today", (), {"days": 14}),
            ("hoy", {"dias": 3}, "academic_access.today.today", (), {"days": 3}),
            ("resumen", {}, "academic_access.summary.summary", (), {}),
            ("materias", {}, "academic_access.core.subjects", (), {}),
            ("materia", {"materia": "MAT-A"}, "academic_access.core.subject_detail", ("MAT-A",), {}),
            ("actividades", {"materia": "MAT-A"}, "academic_access.core.activities", (),
             {"subject": "MAT-A", "limit": 50, "offset": 0}),
            ("clases", {"materia": "MAT-A", "limite": 5, "offset": 10}, "academic_access.core.classes",
             (), {"subject": "MAT-A", "limit": 5, "offset": 10}),
            ("notas", {"tipo": "decision"}, "academic_access.core.notes", (),
             {"subject": None, "kind": "decision", "limit": 50, "offset": 0}),
            ("tareas", {"estado": "pendiente", "materia": "MAT-B"}, "academic_access.core.tasks", (),
             {"subject": "MAT-B", "state": "pendiente", "limit": 50, "offset": 0}),
            ("buscar", {"consulta": "normalización", "materia": "MAT-A"}, "academic_access.core.search",
             ("normalización",), {"subject": "MAT-A", "limit": 8}),
            ("leer_fuente", {"fuente": "SRC-1"}, "academic_access.core.source", ("SRC-1",),
             {"subject": None, "offset": 0, "length": 3000}),
            ("anomalias", {"materia": "MAT-A"}, "academic_access.anomalies.anomalies", (),
             {"subject": "MAT-A"}),
        ]
        for case in cases:
            with self.subTest(case[0], arguments=case[1]):
                self.check(*case)

    def test_invalid_arguments_are_plain_tool_errors(self):
        cases = [("materia", {"materia": "ING3"}), ("materia", {}), ("hoy", {"dias": 0}),
                 ("hoy", {"dias": 61}), ("hoy", {"dias": True}), ("hoy", {"dias": "3"}),
                 ("actividades", {"materia": "MAT-A", "limite": 101}),
                 ("actividades", {"materia": "MAT-A", "offset": -1}),
                 ("buscar", {"materia": "MAT-A", "consulta": ""}),
                 ("buscar", {"materia": "MAT-A", "consulta": "x", "limite": 31}),
                 ("leer_fuente", {"fuente": "SRC-1", "length": 6001}),
                 ("notas", {"tipo": "otra"}), ("tareas", {"estado": "lista"}),
                 ("resumen", {"extra": 1}), ("resumen", ["no", "objeto"]), ("borrar_todo", {})]
        for name, arguments in cases:
            with self.subTest(name, arguments=arguments), patch("academic_access.core.subjects") as r:
                with self.assertRaises(tools.ToolError) as caught:
                    tools.call(name, arguments, RUNTIME)
                r.assert_not_called()
                self.assertNotIn("Traceback", str(caught.exception))
                self.assertTrue(str(caught.exception).strip())

    def test_reader_failures_become_plain_messages_without_internal_text(self):
        secret = "/home/someone/private/academic.sqlite3 Traceback"
        cases = [
            (core.AccessError("SOURCE_UNAVAILABLE", secret), tools.NO_DATA),
            (core.AccessError("INDEX_STALE", secret), tools.STALE),
            (core.AccessError("INVALID_SOURCE", "El Excel debe tener un único semestre activo."),
             tools.NO_SEMESTER),
            (core.AccessError("INVALID_SOURCE", secret), tools.BROKEN),
            (core.AccessError("NOT_FOUND", "materia_id no existe en la base."),
             "materia_id no existe en la base."),
            (StoreBusy(secret), tools.BUSY),
            (SyncError(secret), tools.NO_DATA),
            (paths.ConfigError(secret), tools.CONFIG),
            (FileNotFoundError(secret), tools.NO_FILE),
            (ValueError(secret), tools.BROKEN),
            (KeyError(secret), tools.BROKEN),
            (sqlite3.DatabaseError(secret), tools.BROKEN),
            (RuntimeError(secret), tools.BROKEN),
        ]
        for error, message in cases:
            with self.subTest(type(error).__name__, message=message), \
                    patch("academic_access.core.subjects", side_effect=error), \
                    patch("sys.stderr") as stderr:
                with self.assertRaises(tools.ToolError) as caught:
                    tools.call("materias", {}, RUNTIME)
                self.assertEqual(str(caught.exception), message)
                self.assertIsNone(caught.exception.__cause__)
                logged = "".join(str(c) for c in stderr.write.call_args_list)
                self.assertNotIn("private", logged)

    def test_runtime_resolution_follows_the_data_folder_rules(self):
        with patch.dict(os.environ, {"ACADEMIC_MANAGER_DATA": "/datos/am"}), \
                patch("academic_config.paths.load_config", return_value={}):
            self.assertEqual(tools.resolve_runtime(), Path("/datos/am").resolve())
            self.assertEqual(tools.resolve_runtime("/otra"), Path("/otra").resolve())
        with patch("academic_config.paths.load_config", side_effect=paths.ConfigError("/x/config.json")):
            with self.assertRaises(tools.ToolError) as caught:
                tools.resolve_runtime()
        self.assertEqual(str(caught.exception), tools.CONFIG)


class CatchUpTests(unittest.TestCase):
    def extras(self):
        return {"fecha": date(2027, 3, 10), "actualizado": date(2027, 3, 9),
                "materias": {"MAT-A": {"nota": 91.5, "sin_leer": 2,
                                       "retroalimentacion": rows(30, materia_id="MAT-A")}},
                "notas_nuevas": rows(25, materia_id="MAT-A") + rows(3, materia_id="MAT-B")}

    def test_subject_catch_up_joins_brief_canvas_and_recent_notes(self):
        brief = {"fecha": "2027-03-10", "materia": {"materia_id": "MAT-A", "nombre": "Álgebra"},
                 "pendientes": {"items": rows(40), "resumen": {"tarea": 40}},
                 "entregas": rows(3), "tareas": rows(25), "decisiones": rows(2),
                 "resumenes_clase": rows(5), "workbook_sha256": "x"}
        notes = {"resultado": {"total": 70, "items": rows(20)}}
        with patch("academic_access.brief.subject_brief", return_value=brief) as b, \
                patch("academic_access.core.notes", return_value=notes) as n, \
                patch("academic_mcp.tools.canvas_extras", return_value=self.extras()) as c:
            result = tools.call("ponerme_al_dia", {"materia": "MAT-A"}, RUNTIME)
        b.assert_called_once_with(RUNTIME, "MAT-A")
        n.assert_called_once_with(RUNTIME, subject="MAT-A", limit=tools.CAP)
        c.assert_called_once_with(RUNTIME)
        json.dumps(result, allow_nan=False)
        self.assertEqual(result["materia"]["nombre"], "Álgebra")
        self.assertEqual(result["pendientes"]["total"], 40)
        self.assertEqual(len(result["pendientes"]["items"]), tools.CAP)
        self.assertEqual(result["pendientes"]["resumen"], {"tarea": 40})
        self.assertEqual(len(result["tareas"]["items"]), tools.CAP)
        self.assertEqual(result["notas_recientes"]["total"], 70)
        self.assertEqual(result["canvas"]["nota"], 91.5)
        self.assertEqual(result["canvas"]["sin_leer"], 2)
        self.assertEqual(len(result["canvas"]["retroalimentacion"]["items"]), tools.CAP)
        self.assertEqual(result["canvas"]["notas_nuevas"]["total"], 25)
        self.assertEqual(result["canvas"]["actualizado"], "2027-03-09")
        self.assertEqual(result["avisos"], [])

    def test_overview_joins_today_summary_and_new_canvas_grades(self):
        today = {"fecha": "2027-03-10", "dias": 14, "resumen": {"tarea": 30}, "items": rows(30)}
        summary = {"avisos": ["Canvas Sync falló ayer."], "semestre": {"semestre_id": "2027-1"},
                   "materias": [{"materia_id": "MAT-A"}],
                   "reciente": {"notas": rows(25), "tareas": rows(2)}, "sistema": {"x": 1}}
        with patch("academic_access.today.today", return_value=today) as t, \
                patch("academic_access.summary.summary", return_value=summary), \
                patch("academic_mcp.tools.canvas_extras", return_value=self.extras()):
            result = tools.call("ponerme_al_dia", {}, RUNTIME)
        t.assert_called_once_with(RUNTIME)
        json.dumps(result, allow_nan=False)
        self.assertEqual(result["hoy"]["total"], 30)
        self.assertEqual(len(result["hoy"]["items"]), tools.CAP)
        self.assertEqual(result["avisos"], ["Canvas Sync falló ayer."])
        self.assertEqual(result["semestre"], {"semestre_id": "2027-1"})
        self.assertEqual(len(result["reciente"]["notas"]), tools.CAP)
        self.assertNotIn("sistema", result)
        self.assertEqual(result["canvas"]["notas_nuevas"]["total"], 28)
        self.assertEqual(len(result["canvas"]["notas_nuevas"]["items"]), tools.CAP)
        self.assertEqual(result["canvas"]["notas_nuevas"]["items"][0]["fecha"], "2027-03-01")

    def test_unreadable_canvas_copies_do_not_hide_the_rest(self):
        today = {"fecha": "2027-03-10", "dias": 14, "resumen": {}, "items": []}
        summary = {"avisos": [], "semestre": None, "materias": [], "reciente": {}}
        with patch("academic_access.today.today", return_value=today), \
                patch("academic_access.summary.summary", return_value=summary), \
                patch("academic_mcp.tools.canvas_extras", side_effect=SyncError("/secret/path")), \
                patch("sys.stderr"):
            result = tools.call("ponerme_al_dia", {}, RUNTIME)
        self.assertIsNone(result["canvas"])
        self.assertEqual(result["avisos"], [tools.NO_CANVAS])

    def test_failing_store_fails_the_whole_catch_up_plainly(self):
        with patch("academic_access.brief.subject_brief",
                   side_effect=core.AccessError("SOURCE_UNAVAILABLE", "x")), patch("sys.stderr"):
            with self.assertRaises(tools.ToolError) as caught:
                tools.call("ponerme_al_dia", {"materia": "MAT-A"}, RUNTIME)
        self.assertEqual(str(caught.exception), tools.NO_DATA)


class SourcePathTests(unittest.TestCase):
    def test_leer_fuente_never_returns_the_local_file_path(self):
        found = {"fuente_id": "SRC-1", "ruta_texto": "C:\\AcademicManagerData\\x.txt", "texto": "Hola",
                 "hay_mas": False}
        with patch("academic_access.core.source", return_value=found):
            result = tools.call("leer_fuente", {"fuente": "SRC-1"}, RUNTIME)
        self.assertNotIn("ruta_texto", result)
        self.assertNotIn("AcademicManagerData", json.dumps(result))
        self.assertEqual(result["texto"], "Hola")


def _rpc(process, message):
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    if "id" not in message:
        return None
    lines = []
    reader = threading.Thread(target=lambda: lines.append(process.stdout.readline()), daemon=True)
    reader.start()
    reader.join(RPC_TIMEOUT)
    if reader.is_alive() or not lines or not lines[0]:
        process.kill()
        raise AssertionError(f"The MCP server did not answer {message.get('method')} within {RPC_TIMEOUT} s.")
    return json.loads(lines[0])


@unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP SDK not installed (only in the app runtime)")
class StdioServerTests(unittest.TestCase):
    """`python -m academic_mcp` speaks MCP over stdio with a real (temporary) data folder."""

    def setUp(self):
        from academic_store import records, store
        from capture.storage.metadata import sha256
        from tests.test_today import SHEETS
        from tests.xlsx_fixture import write_sheets
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime = Path(tmp.name) / "runtime"
        (self.runtime / "control").mkdir(parents=True)
        source = Path(tmp.name) / "source.xlsx"
        write_sheets(source, SHEETS)
        db = self.runtime / store.DB_NAME
        store.migrate(source, db)
        records.apply(db, [{"tabla": "tareas", "accion": "insertar", "registro": {
            "tarea_id": "TAR-1", "titulo": "Leer capítulo 3", "materia_id": "MAT-A",
            "estado": "pendiente", "fecha_limite": "2027-03-11"}}])
        state = {"status": "free", "lock": None, "published": {"sha256": sha256(db)}}
        (self.runtime / "control" / "database-work-state.json").write_text(json.dumps(state), "utf-8")
        self.env = {**os.environ, "ACADEMIC_MANAGER_DATA": str(self.runtime),
                    "ACADEMIC_MANAGER_CONFIG": str(Path(tmp.name) / "no-config.json"),
                    "PYTHONPATH": str(ROOT)}

    def test_handshake_list_and_calls(self):
        process = subprocess.Popen([sys.executable, "-m", "academic_mcp"], cwd=ROOT, env=self.env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8")
        try:
            init = _rpc(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"}}})
            self.assertEqual(init["result"]["serverInfo"]["name"], "academic-manager")
            self.assertIn("tools", init["result"]["capabilities"])
            _rpc(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            listed = _rpc(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            listed = {t["name"]: t for t in listed["result"]["tools"]}
            self.assertEqual(set(listed), EXPECTED)
            for tool in listed.values():
                read = tool["name"] in READ_TOOLS
                self.assertIs(tool["annotations"]["readOnlyHint"], read)
                self.assertIs(tool["annotations"]["destructiveHint"], not read)
                self.assertIs(tool["inputSchema"]["additionalProperties"], False)
            done = _rpc(process, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                  "params": {"name": "tareas", "arguments": {"estado": "pendiente"}}})
            self.assertFalse(done["result"].get("isError"))
            payload = json.loads(done["result"]["content"][0]["text"])
            self.assertEqual([t["tarea_id"] for t in payload["resultado"]["items"]], ["TAR-1"])
            bad = _rpc(process, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                 "params": {"name": "materia", "arguments": {"materia": "ING3"}}})
            self.assertTrue(bad["result"]["isError"])
            self.assertIn("MAT-", bad["result"]["content"][0]["text"])
            failed = _rpc(process, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                    "params": {"name": "materias", "arguments": {}}})
            self.assertTrue(failed["result"]["isError"])
            text = failed["result"]["content"][0]["text"]
            self.assertNotIn(str(self.runtime), text)
            self.assertNotIn("Traceback", text)
        finally:
            process.stdin.close()
            rest = process.stdout.read()
            process.wait(timeout=10)
            errors = process.stderr.read()
            process.stdout.close()
            process.stderr.close()
        self.assertEqual(rest.strip(), "")  # nothing but JSON-RPC replies on stdout
        self.assertNotIn(str(self.runtime), errors)

    def test_stateless_revision_without_handshake(self):
        meta = {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                          "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
                          "io.modelcontextprotocol/clientCapabilities": {}}}
        process = subprocess.Popen([sys.executable, "-m", "academic_mcp"], cwd=ROOT, env=self.env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        try:
            found = _rpc(process, {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": meta})
            self.assertIn("2026-07-28", found["result"]["supportedVersions"])
            listed = _rpc(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": meta})
            self.assertEqual({t["name"] for t in listed["result"]["tools"]}, EXPECTED)
            done = _rpc(process, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                **meta, "name": "tareas", "arguments": {}}})
            payload = json.loads(done["result"]["content"][0]["text"])
            self.assertEqual(payload["resultado"]["total"], 1)
        finally:
            process.stdin.close()
            process.wait(timeout=10)
            process.stdout.close()


if __name__ == "__main__":
    unittest.main()
