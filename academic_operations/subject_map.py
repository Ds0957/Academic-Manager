"""Mapa de la materia: validación del documento y de sus citas (odd/tasks/mapa-materia.md, S2–S3).

El chat (Claude con el conector, o ChatGPT con `academic-act.cmd map --input`) propone el mapa;
aquí solo se comprueba que su forma sea la del contrato v1 y que cada cita aparezca de verdad en
una fuente indexada de la materia. El resultado es JSON canónico, el mismo para la misma entrada.
"""
import json
import re

from academic_access.core import AccessError, source_text
from .actions import OperationError
from .chat import _fail, _id, _text

VERSION = 1
MAX_TOPICS = 80
MAX_RELATIONS = 150
MAX_DIAGRAMS = 6
MAX_SOURCES = 5
MAX_MERMAID = 12000
MAX_CONTENT = 200 * 1024
TOP_KEYS = ("resumen", "temas", "relaciones", "diagramas")
TOPIC_KEYS = ("id", "titulo", "explicacion", "padre", "eje", "fuentes")
# Tipos de diagrama de Mermaid admitidos (primera línea con contenido).
DIAGRAM_TYPES = ("mindmap", "flowchart", "graph", "timeline", "sequenceDiagram", "stateDiagram",
                 "stateDiagram-v2", "classDiagram", "erDiagram", "quadrantChart")
SOURCE_ID = re.compile(r"[\w:.\-]{1,200}")


def _object(value, keys, where):
    if not isinstance(value, dict):
        _fail("INVALID_ARGUMENT", f"{where} debe ser un objeto.")
    unknown = sorted(k for k in value if k not in keys)
    if unknown:
        _fail("INVALID_ARGUMENT", f"{where} no admite: {', '.join(unknown)}.")
    return value


def _list(value, where, high, *, low=0):
    if value is None:
        value = []
    if not isinstance(value, list) or not low <= len(value) <= high:
        _fail("INVALID_ARGUMENT", f"{where} debe ser una lista de {low} a {high} elementos.")
    return value


def _in(where, check):
    """Antepone la ubicación (p. ej. «Tema T3») al mensaje del error de validación."""
    try:
        return check()
    except OperationError as exc:
        raise OperationError(exc.code, f"{where}: {exc}") from None


def _topic(item, number):
    item = _object(item, TOPIC_KEYS, f"Tema {number}")
    topic_id = _in(f"Tema {number}", lambda: _id(item, "id"))
    where = f"Tema {topic_id}"
    eje = item.get("eje")
    if eje is not None and (type(eje) is not int or not 1 <= eje <= 12):
        _fail("INVALID_ARGUMENT", f"{where}: eje debe ser un número entero de 1 a 12.")
    sources = []
    for n, found in enumerate(_list(item.get("fuentes"), f"{where}: fuentes", MAX_SOURCES), start=1):
        found = _object(found, ("fuente_id", "cita"), f"{where}, fuente {n}")
        fuente_id = _in(where, lambda: _text(found, "fuente_id", required=True, limit=200))
        if not SOURCE_ID.fullmatch(fuente_id):
            _fail("INVALID_ARGUMENT", f"{where}: fuente_id no tiene un formato válido.")
        cita = _in(where, lambda: _text(found, "cita", required=True, limit=400))
        if len(cita) < 12:
            _fail("INVALID_ARGUMENT", f"{where}: la cita debe tener al menos 12 caracteres.")
        sources.append({"fuente_id": fuente_id, "cita": cita})
    return {"id": topic_id,
            "titulo": _in(where, lambda: _text(item, "titulo", required=True, limit=120)),
            "explicacion": _in(where, lambda: _text(item, "explicacion", limit=1500)),
            "padre": _in(where, lambda: _id(item, "padre", required=False)),
            "eje": eje, "fuentes": sources}


def _diagram(item, number):
    item = _object(item, ("titulo", "mermaid"), f"Diagrama {number}")
    where = f"Diagrama {number}"
    title = _in(where, lambda: _text(item, "titulo", required=True, limit=120))
    code = item.get("mermaid")
    if not isinstance(code, str) or not code.strip() or len(code) > MAX_MERMAID or "\x00" in code:
        _fail("INVALID_ARGUMENT", f"{where}: mermaid debe ser texto de 1 a {MAX_MERMAID} caracteres.")
    if "%%{" in code:
        _fail("INVALID_ARGUMENT", f"{where}: no se admiten directivas %%{{...}}%% de Mermaid.")
    lines = [line.strip() for line in code.strip().splitlines()]
    first = next((line for line in lines if line and not line.startswith("%%")), "")
    if first.split(" ")[0] not in DIAGRAM_TYPES:
        _fail("INVALID_ARGUMENT",
              f"{where}: la primera línea debe ser un tipo de diagrama ({', '.join(DIAGRAM_TYPES)}).")
    return {"titulo": title, "mermaid": code.strip()}


def normalize(mapa):
    """Contenido canónico v1 del mapa; error INVALID_ARGUMENT con la ubicación del problema."""
    mapa = _object(mapa, TOP_KEYS, "mapa")
    topics = [_topic(item, n) for n, item in
              enumerate(_list(mapa.get("temas"), "temas", MAX_TOPICS, low=1), start=1)]
    ids = [t["id"] for t in topics]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        _fail("INVALID_ARGUMENT", f"temas: id repetido: {', '.join(repeated)}.")
    parents = {t["id"]: t["padre"] for t in topics}
    for topic in topics:
        where = f"Tema {topic['id']}"
        if topic["padre"] is not None and topic["padre"] not in parents:
            _fail("INVALID_ARGUMENT", f"{where}: padre {topic['padre']} no es un tema del mapa.")
        seen, current = set(), topic["id"]
        while current is not None:
            if current in seen:
                _fail("INVALID_ARGUMENT", f"{where}: los padres forman un ciclo.")
            seen.add(current)
            current = parents[current]
        if topic["padre"] is not None and not topic["fuentes"]:
            _fail("INVALID_ARGUMENT", f"{where}: indique al menos una fuente con su cita.")
    relations, keys = [], set()
    for n, item in enumerate(_list(mapa.get("relaciones"), "relaciones", MAX_RELATIONS), start=1):
        item = _object(item, ("de", "a", "texto"), f"Relación {n}")
        where = f"Relación {n}"
        relation = {"de": _in(where, lambda: _id(item, "de")), "a": _in(where, lambda: _id(item, "a")),
                    "texto": _in(where, lambda: _text(item, "texto", required=True, limit=80))}
        for end in ("de", "a"):
            if relation[end] not in parents:
                _fail("INVALID_ARGUMENT", f"{where}: {relation[end]} no es un tema del mapa.")
        if relation["de"] == relation["a"]:
            _fail("INVALID_ARGUMENT", f"{where}: une un tema consigo mismo.")
        key = (relation["de"], relation["a"], relation["texto"])
        if key in keys:
            _fail("INVALID_ARGUMENT", f"{where}: repite otra relación.")
        keys.add(key)
        relations.append(relation)
    diagrams = [_diagram(item, n) for n, item in
                enumerate(_list(mapa.get("diagramas"), "diagramas", MAX_DIAGRAMS), start=1)]
    content = {"version": VERSION, "resumen": _in("mapa", lambda: _text(mapa, "resumen", limit=1000)),
               "temas": topics, "relaciones": relations, "diagramas": diagrams}
    if len(dump(content).encode("utf-8")) > MAX_CONTENT:
        _fail("INVALID_ARGUMENT", "El mapa supera 200 KB; divídalo en menos temas o diagramas.")
    return content


def dump(content):
    """JSON canónico guardado en `mapas.contenido`."""
    return json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _flat(text):
    return " ".join(text.split())


def check_citations(runtime, subject, content):
    """Cada cita debe aparecer (sin importar espacios ni saltos de línea) en su fuente."""
    texts = {}
    for topic in content["temas"]:
        for found in topic["fuentes"]:
            fuente_id, where = found["fuente_id"], f"Tema {topic['id']}"
            if fuente_id not in texts:
                try:
                    texts[fuente_id] = _flat(source_text(runtime, fuente_id, subject=subject))
                except AccessError as exc:
                    if exc.code in ("NOT_FOUND", "NO_TEXT"):
                        _fail("INVALID_ARGUMENT", f"{where}: la fuente {fuente_id} no es un texto "
                                                  "indexado de esta materia.")
                    raise OperationError("SOURCE_UNAVAILABLE",
                                         "No se pudieron comprobar las citas con el índice de "
                                         "búsqueda de este PC. Actualícelo y vuelva a intentarlo.",
                                         retryable=True) from None
            if _flat(found["cita"]) not in texts[fuente_id]:
                _fail("INVALID_ARGUMENT", f"{where}: la cita no aparece en la fuente {fuente_id}; "
                                          "copie el texto tal como lo devuelve leer_fuente.")
