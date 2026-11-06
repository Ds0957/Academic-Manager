"""MCP server over stdio for Claude Desktop, built on the official SDK (`mcp`, docs/43).

The SDK negotiates the protocol (the legacy `initialize` handshake and the stateless 2026-07-28
revision); this module only lists the read tools of `tools.py` and runs them. stdout carries the
protocol only: the SDK points fd 1 at stderr while serving, and our own log lines go to stderr
with the exception type and nothing else.
"""
import argparse
import json
import sys
from pathlib import Path

import anyio
import mcp_types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from academic_config import paths
from . import tools

NAME = "academic-manager"
VERSION_FILE = Path(__file__).resolve().parents[1] / "academic_app" / "VERSION"


def _version():
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _tool(tool):
    return types.Tool(name=tool.name, title=tool.title, description=tool.description,
                      input_schema=tool.schema,
                      annotations=types.ToolAnnotations(title=tool.title, read_only_hint=tool.read_only,
                                                        destructive_hint=tool.destructive, idempotent_hint=True,
                                                        open_world_hint=False))


def _error(message):
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], is_error=True)


def build(explicit_runtime=None):
    """Server bound to the tool registry; the data folder is resolved on each call."""
    listed = [_tool(t) for t in tools.TOOLS.values()]

    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=listed)

    async def call_tool(ctx, params):
        try:
            runtime = tools.resolve_runtime(explicit_runtime)
            result = await anyio.to_thread.run_sync(tools.call, params.name, params.arguments, runtime)
            text = json.dumps(result, ensure_ascii=False, allow_nan=False)
        except tools.ToolError as exc:
            return _error(str(exc))
        except Exception as exc:  # noqa: BLE001 - never send internal text to the client
            print(f"academic_mcp: fallo inesperado ({type(exc).__name__}).", file=sys.stderr, flush=True)
            return _error(tools.BROKEN)
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    def input_schema(name):
        tool = tools.TOOLS.get(name)
        return tool.schema if tool else None

    return Server(NAME, version=_version(), title="Academic Manager",
                  description=("Consulta de los datos académicos de Academic Manager y guardado, con aprobación, "
                               "del mapa de estudio de cada materia y del índice de cada clase."),
                  get_tool_input_schema=input_schema, on_list_tools=list_tools, on_call_tool=call_tool)


async def serve(explicit_runtime=None):
    server = build(explicit_runtime)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m academic_mcp",
                                     description="Conector de Academic Manager para Claude Desktop "
                                                 "(MCP por stdio; solo escribe mapas de materia e índices de clase, con aprobación).",
                                     epilog=paths.DATA_HELP)
    parser.add_argument("--runtime", help="Carpeta de datos (si no, la de siempre).")
    args = parser.parse_args(argv)
    try:
        anyio.run(serve, args.runtime)
    except KeyboardInterrupt:
        pass
    return 0
