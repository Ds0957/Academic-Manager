"""Índice de la clase: validación del documento y de sus minutos (odd/tasks/indice-clase.md, S2–S3).

El chat (Claude con el conector, o ChatGPT con `academic-act.cmd class-index --input`) propone el
índice leyendo la transcripción; aquí solo se comprueba su forma y que cada minuto exista de verdad:
`inicio` debe ser el inicio de un fragmento de la transcripción y su `cita` debe estar en ese
fragmento. El resultado es JSON canónico, el mismo para la misma entrada.
"""
import re

from academic_access.core import AccessError, source_text, transcript_segments
from .actions import OperationError
from .chat import _fail, _text
from .subject_map import MAX_CONTENT, _flat, _in, _list, _object, dump

VERSION = 1
MAX_SUMMARY = 900
MAX_TOPICS = 40
MAX_MOMENTS = 30
MIN_QUOTE = 12
MAX_QUOTE = 300
# La de `tema` va al final: lo administrativo es lo primero que se busca en el índice.
CATEGORIES = ("instrucciones", "fechas", "evaluacion", "tareas", "tema")
TIME = re.compile(r"\d{2}:[0-5]\d:[0-5]\d")


def _moment(item, topic, number):
    where = f"Tema «{topic}», momento {number}"
    item = _object(item, ("inicio", "texto", "cita"), where)
    start = item.get("inicio")
    if not isinstance(start, str) or not TIME.fullmatch(start):
        _fail("INVALID_ARGUMENT", f"{where}: inicio debe tener el formato HH:MM:SS.")
    quote = _in(where, lambda: _text(item, "cita", required=True, limit=MAX_QUOTE))
    if len(quote) < MIN_QUOTE:
        _fail("INVALID_ARGUMENT", f"{where}: la cita debe tener al menos {MIN_QUOTE} caracteres.")
    return {"inicio": start, "texto": _in(where, lambda: _text(item, "texto", required=True, limit=200)),
            "cita": quote}


def _topic(item, number):
    where = f"Tema {number}"
    item = _object(item, ("titulo", "categoria", "momentos"), where)
    title = _in(where, lambda: _text(item, "titulo", required=True, limit=120))
    category = item.get("categoria")
    category = "tema" if category is None else category
    if category not in CATEGORIES:
        _fail("INVALID_ARGUMENT", f"Tema «{title}»: categoria debe ser una de: {', '.join(CATEGORIES)}.")
    moments = [_moment(found, title, n) for n, found in enumerate(
        _list(item.get("momentos"), f"Tema «{title}»: momentos", MAX_MOMENTS, low=1), start=1)]
    keys = [(m["inicio"], m["texto"]) for m in moments]
    if len(set(keys)) != len(keys):
        _fail("INVALID_ARGUMENT", f"Tema «{title}»: repite un momento.")
    return {"titulo": title, "categoria": category, "momentos": sorted(moments, key=lambda m: m["inicio"])}


def normalize(indice):
    """Contenido canónico v1 del índice; error INVALID_ARGUMENT con la ubicación del problema."""
    indice = _object(indice, ("version", "resumen", "temas"), "indice")
    if indice.get("version") not in (None, VERSION):
        _fail("INVALID_ARGUMENT", f"indice: version debe ser {VERSION}.")
    summary = _in("indice", lambda: _text(indice, "resumen", required=True, limit=MAX_SUMMARY))
    topics = [_topic(item, n) for n, item in
              enumerate(_list(indice.get("temas"), "temas", MAX_TOPICS, low=1), start=1)]
    content = {"version": VERSION, "resumen": summary, "temas": topics}
    if len(dump(content).encode("utf-8")) > MAX_CONTENT:
        _fail("INVALID_ARGUMENT", "El índice supera 200 KB; use menos temas o momentos.")
    return content


def check_moments(transcript, content):
    """Cada `inicio` abre un fragmento y su cita está en él (sin importar espacios ni saltos)."""
    fragments = {}
    for fragment in transcript_segments(transcript):
        fragments[fragment["inicio"]] = f'{fragments.get(fragment["inicio"], "")} {_flat(fragment["texto"])}'
    if not fragments:
        _fail("INVALID_ARGUMENT", "La transcripción de esta clase no tiene tiempos; no se le puede hacer un índice.")
    for topic in content["temas"]:
        for moment in topic["momentos"]:
            where = f'Tema «{topic["titulo"]}», {moment["inicio"]}'
            if moment["inicio"] not in fragments:
                _fail("INVALID_ARGUMENT", f"{where}: ese tiempo no abre ningún fragmento de la transcripción; "
                                          "copie el tiempo inicial tal como lo devuelve leer_fuente.")
            if _flat(moment["cita"]) not in fragments[moment["inicio"]]:
                _fail("INVALID_ARGUMENT", f"{where}: la cita no aparece en ese fragmento; copie el texto tal "
                                          "como lo devuelve leer_fuente.")


def render(content):
    """Texto de la clase publicada, generado del índice: Capture exige ese archivo para publicar."""
    lines = ["# Índice de la clase", "",
             "Generado del índice por minutos, comprobado contra la transcripción. Los tiempos son los de la "
             "grabación original.", "", "## De qué trató", "", content["resumen"], ""]
    for topic in content["temas"]:
        lines += [f'## {topic["titulo"]}', "", f'Categoría: {topic["categoria"]}', ""]
        lines += [f'- [{m["inicio"]}] {m["texto"]}: «{m["cita"]}»' for m in topic["momentos"]]
        lines.append("")
    return "\n".join(lines)


def transcript_of(runtime, subject, class_id):
    """Texto verificado de la transcripción de la clase en el índice de conocimiento."""
    try:
        return source_text(runtime, f"clase:{class_id}:transcripcion", subject=subject)
    except AccessError as exc:
        if exc.code in ("NOT_FOUND", "NO_TEXT"):
            _fail("INVALID_ARGUMENT", "Esta clase no tiene una transcripción indexada; sin ella no se "
                                      "pueden comprobar los minutos.")
        raise OperationError("SOURCE_UNAVAILABLE", "No se pudieron comprobar los minutos con el índice de "
                                                   "búsqueda de este PC. Actualícelo y vuelva a intentarlo.",
                             retryable=True) from None
