"""Tools of the Claude Desktop connector, independent of the MCP SDK (docs/43).

Each read tool is a thin adapter over the existing read contract (`academic_access`, docs/27, 34,
35, 36) and the Canvas extras of the panel. The write tools, `guardar_mapa` and
`guardar_indice_clase`, go through the chat write contract (`academic_operations.chat`, docs/33): preview first, save only with the
preview's `plan_id` and an explicit approval. Arguments are checked against the tool's JSON schema
before any reader runs; every failure becomes a `ToolError` with a plain Spanish message for the
person. Reader exception texts are never shown and the log only records the exception type.
"""
import math
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from academic_access import anomalies, brief, core, summary, today
from academic_access.canvas import canvas_extras
from academic_config import paths
from canvas_sync.common import StoreBusy, SyncError
from academic_operations.actions import OperationError

CAP = 20  # Longest list `ponerme_al_dia` returns; the paged tools show the rest.

# Messages for the person; wording follows the panel (academic_app/panel.py).
CONFIG_STEP = "Abra Academic Manager → «Configuración» y revise el paso «Materias»."
NO_DATA = ("Todavía no se pueden leer sus materias: la base de datos de este PC no está lista o no "
           "coincide con la publicada en Drive. " + CONFIG_STEP)
BUSY = ("La base de datos se está actualizando en este momento (por ejemplo, con la sincronización de "
        "Canvas). Espere un momento y vuelva a intentarlo.")
NO_SEMESTER = ("Su base de datos no tiene un semestre activo con materias. Abra Academic Manager → "
               "«Configuración» → «Materias» y elija las materias de este semestre.")
STALE = ("El índice de búsqueda de este PC está desactualizado. Actualícelo en Academic Manager → "
         "«Configuración» → «Materias» y vuelva a intentarlo.")
NO_FILE = ("No se pudo leer un archivo de sus datos. Revise que la carpeta de datos exista y que Google "
           "Drive esté abierto, y vuelva a intentarlo.")
BROKEN = "Los datos de sus materias no se pudieron leer. " + CONFIG_STEP + " Si sigue igual, pida ayuda."
CONFIG = ("El archivo de configuración de Academic Manager no es válido. Abra Academic Manager → "
          "«Configuración» para corregirlo.")
NO_CANVAS = ("Los datos de Canvas de este PC no se pudieron leer; se muestra lo demás. Pruebe "
             "«Sincronizar ahora» en el panel de Academic Manager.")
# Access errors whose own message is already plain, fixed text (no paths or values).
PLAIN_CODES = ("INVALID_ARGUMENT", "NOT_FOUND", "NO_TEXT", "SOURCE_CHANGED")


class ToolError(Exception):
    """A tool failed; the message is plain Spanish, safe to show to the person."""


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    schema: dict
    handler: Callable
    read_only: bool = True
    destructive: bool = False  # True when it can replace data the person already saved


# --- argument schemas ------------------------------------------------------------------
SUBJECT_PATTERN = r"^MAT-[A-Za-z0-9_-]{1,40}$"


def _subject(text="Identificador exacto de la materia (MAT-…), como lo devuelve «materias»."):
    return {"type": "string", "pattern": SUBJECT_PATTERN, "description": text}


def _integer(low, high, text):
    return {"type": "integer", "minimum": low, "maximum": high, "description": text}


def _text(high, text):
    return {"type": "string", "minLength": 1, "maxLength": high, "description": text}


LIMIT = _integer(1, core.MAX_ITEMS, f"Cuántos elementos devolver (1 a {core.MAX_ITEMS}; por defecto 50).")
OFFSET = _integer(0, 1_000_000, "Cuántos elementos saltar para ver la página siguiente (por defecto 0).")


def _schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required),
            "additionalProperties": False}


def _check(schema, arguments):
    """Validated arguments without the null ones, or a plain `ToolError`."""
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolError("Los parámetros deben ser un objeto JSON.")
    rules = schema["properties"]
    unknown = sorted(set(arguments) - set(rules))
    if unknown:
        raise ToolError(f"Parámetro no reconocido: {', '.join(unknown)[:80]}.")
    for name in schema["required"]:
        if arguments.get(name) is None:
            raise ToolError(f"Falta el parámetro «{name}».")
    values = {k: v for k, v in arguments.items() if v is not None}
    for name, value in values.items():
        rule = rules[name]
        if rule["type"] == "integer":
            if type(value) is not int or not rule["minimum"] <= value <= rule["maximum"]:
                raise ToolError(f"«{name}» debe ser un número entero entre {rule['minimum']} "
                                f"y {rule['maximum']}.")
        elif rule["type"] == "boolean":
            if type(value) is not bool:
                raise ToolError(f"«{name}» debe ser true o false.")
        elif rule["type"] == "object":
            if not isinstance(value, dict):
                raise ToolError(f"«{name}» debe ser un objeto JSON.")
        elif not isinstance(value, str):
            raise ToolError(f"«{name}» debe ser texto.")
        elif "enum" in rule and value not in rule["enum"]:
            raise ToolError(f"«{name}» debe ser uno de: {', '.join(rule['enum'])}.")
        elif "pattern" in rule and not _matches(rule["pattern"], value):
            if rule["pattern"] != SUBJECT_PATTERN:
                raise ToolError(f"«{name}» no tiene el formato esperado.")
            raise ToolError(f"«{name}» debe ser un identificador de materia como MAT-ABC; "
                            "use la herramienta «materias» para verlos.")
        elif not rule.get("minLength", 0) <= len(value) <= rule.get("maxLength", 10_000):
            raise ToolError(f"«{name}» debe tener entre {rule.get('minLength', 0)} y "
                            f"{rule.get('maxLength')} caracteres.")
    return values


def _matches(pattern, value):
    return re.fullmatch(pattern, value) is not None


# --- error mapping ---------------------------------------------------------------------
def plain_message(exc):
    """Message for the person by failure type; the exception text itself is never used."""
    if isinstance(exc, ToolError):
        return str(exc)
    if isinstance(exc, core.AccessError):
        if exc.code == "INDEX_STALE":
            return STALE
        if exc.code == "SOURCE_UNAVAILABLE":
            return NO_DATA
        if exc.code == "INVALID_SOURCE" and "semestre activo" in str(exc):
            return NO_SEMESTER
        return str(exc) if exc.code in PLAIN_CODES else BROKEN
    if isinstance(exc, paths.ConfigError):
        return CONFIG
    if isinstance(exc, StoreBusy):
        return BUSY
    if isinstance(exc, SyncError):
        return NO_DATA
    if isinstance(exc, OSError):
        return NO_FILE
    if isinstance(exc, StopIteration) or (type(exc) is ValueError and "semestre activo" in str(exc)):
        return NO_SEMESTER
    return BROKEN  # StoreError, ValueError, KeyError, sqlite3.Error and any other unreadable data


def _log(name, exc):
    print(f"academic_mcp: {name} no se pudo leer ({type(exc).__name__}).", file=sys.stderr, flush=True)


def resolve_runtime(explicit=None):
    """Data folder like every other module: --runtime > ACADEMIC_MANAGER_DATA > config > default."""
    try:
        value = paths.runtime(explicit, env=("ACADEMIC_MANAGER_DATA",), default=paths.DEFAULT_RUNTIME)
    except paths.ConfigError:
        raise ToolError(CONFIG) from None
    return Path(value).resolve()


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, date):  # datetime included
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float)):
        return value
    return str(value)


# --- handlers ----------------------------------------------------------------------------
def _page(args):
    return {"limit": args.get("limite", 50), "offset": args.get("offset", 0)}


def _capped(rows):
    rows = list(rows or [])
    return {"total": len(rows), "items": rows[:CAP]}


def _canvas(runtime, warnings):
    """Canvas copies are optional for the overview: unreadable ones become a warning."""
    try:
        return canvas_extras(runtime)
    except Exception as exc:  # noqa: BLE001 - any failure only hides the Canvas part
        _log("canvas", exc)
        warnings.append(NO_CANVAS)
        return None


def _catch_up(runtime, args):
    subject = args.get("materia")
    if subject:
        return _catch_up_subject(runtime, subject)
    day = today.today(runtime)
    overview = summary.summary(runtime)
    warnings = list(overview.get("avisos") or [])
    extras = _canvas(runtime, warnings)
    return {"fecha": day["fecha"], "dias": day["dias"],
            "hoy": {"resumen": day["resumen"], **_capped(day["items"])},
            "semestre": overview.get("semestre"), "materias": overview.get("materias"),
            "reciente": {k: list(v)[:CAP] for k, v in (overview.get("reciente") or {}).items()},
            "canvas": None if extras is None else {
                "actualizado": extras["actualizado"], "notas_nuevas": _capped(extras["notas_nuevas"])},
            "avisos": warnings,
            "nota": f"Las listas muestran como máximo {CAP} elementos; «total» dice cuántos hay."}


def _catch_up_subject(runtime, subject):
    data = brief.subject_brief(runtime, subject)
    recent = core.notes(runtime, subject=subject, limit=CAP)["resultado"]
    warnings = []
    extras = _canvas(runtime, warnings)
    canvas = None
    if extras is not None and subject in extras["materias"]:
        row = extras["materias"][subject]
        canvas = {"actualizado": extras["actualizado"], "nota": row["nota"], "sin_leer": row["sin_leer"],
                  "retroalimentacion": _capped(row["retroalimentacion"]),
                  "notas_nuevas": _capped([r for r in extras["notas_nuevas"] if r["materia_id"] == subject])}
    return {"fecha": data["fecha"], "materia": data["materia"],
            "pendientes": {"resumen": data["pendientes"]["resumen"], **_capped(data["pendientes"]["items"])},
            "entregas": _capped(data["entregas"]), "tareas": _capped(data["tareas"]),
            "decisiones": _capped(data["decisiones"]), "resumenes_clase": _capped(data["resumenes_clase"]),
            "notas_recientes": {"total": recent["total"], "items": recent["items"]},
            "canvas": canvas, "avisos": warnings,
            "nota": f"Las listas muestran como máximo {CAP} elementos; «total» dice cuántos hay."}


_TOOLS = [
    Tool("ponerme_al_dia", "Ponerme al día",
         "Llámela al empezar cada conversación, antes de responder: da el contexto académico actual. "
         "Sin «materia»: lo que hay que hacer hoy y en los próximos días, avisos del sistema, materias "
         "del semestre, notas y tareas recientes y notas nuevas de Canvas. Con «materia»: pendientes, "
         "entregas, tareas, decisiones, resúmenes de clase y notas recientes de esa materia, más su "
         "nota actual, mensajes sin leer y comentarios del profesor en Canvas. Las listas vienen "
         f"recortadas a {CAP} elementos; use las demás herramientas para ver más.",
         _schema({"materia": _subject("Materia (MAT-…) si la conversación es de una sola materia.")}),
         _catch_up),
    Tool("hoy", "Qué hacer hoy",
         "Lista priorizada de lo que hay que hacer: actividades vencidas y próximas, tareas personales, "
         "clases sin procesar y materias sin movimiento, del semestre activo. Fechas en hora de Colombia.",
         _schema({"dias": _integer(1, today.MAX_DAYS, f"Horizonte en días (1 a {today.MAX_DAYS}; por "
                                                       f"defecto {today.DEFAULT_DAYS}).")}),
         lambda runtime, a: today.today(runtime, days=a.get("dias", today.DEFAULT_DAYS))),
    Tool("resumen", "Resumen del semestre",
         "Resumen general: avisos del sistema (Canvas Sync, índice, token), semestre activo, materias "
         "con su última clase, lo urgente, notas y tareas recientes y el estado de los datos.",
         _schema(), lambda runtime, a: summary.summary(runtime)),
    Tool("materias", "Materias",
         "Materias del semestre activo con su identificador MAT-…, nombre y bloque. Úsela para saber qué "
         "identificador pasar a las demás herramientas.",
         _schema(), lambda runtime, a: core.subjects(runtime)),
    Tool("materia", "Detalle de una materia",
         "Datos de una materia y la cobertura de su material de estudio (qué fuentes tienen texto "
         "disponible para buscar y citar).",
         _schema({"materia": _subject()}, ["materia"]),
         lambda runtime, a: core.subject_detail(runtime, a["materia"])),
    Tool("actividades", "Actividades de una materia",
         "Actividades de Canvas de una materia (fechas de apertura, entrega y cierre, puntos, estado), "
         "paginadas.",
         _schema({"materia": _subject(), "limite": LIMIT, "offset": OFFSET}, ["materia"]),
         lambda runtime, a: core.activities(runtime, subject=a["materia"], **_page(a))),
    Tool("clases", "Clases de una materia",
         "Clases registradas de una materia (fecha, título, tema principal, estado de procesamiento), "
         "paginadas.",
         _schema({"materia": _subject(), "limite": LIMIT, "offset": OFFSET}, ["materia"]),
         lambda runtime, a: core.classes(runtime, subject=a["materia"], **_page(a))),
    Tool("notas", "Notas guardadas",
         "Notas guardadas por la persona (decisiones, resúmenes de clase, notas), de la más reciente a la "
         "más antigua, paginadas. Opcionalmente de una materia o de un tipo.",
         _schema({"materia": _subject(), "tipo": {"type": "string", "enum": list(core.NOTE_TYPES),
                                                  "description": "Tipo de nota."},
                  "limite": LIMIT, "offset": OFFSET}),
         lambda runtime, a: core.notes(runtime, subject=a.get("materia"), kind=a.get("tipo"), **_page(a))),
    Tool("tareas", "Tareas personales",
         "Tareas personales de la persona, por fecha límite, paginadas. Opcionalmente por estado o "
         "materia.",
         _schema({"estado": {"type": "string", "enum": list(core.TASK_STATES),
                             "description": "Estado de la tarea."},
                  "materia": _subject(), "limite": LIMIT, "offset": OFFSET}),
         lambda runtime, a: core.tasks(runtime, subject=a.get("materia"), state=a.get("estado"),
                                       **_page(a))),
    Tool("buscar", "Buscar en el material",
         "Busca palabras en el material de estudio autorizado de una materia y devuelve las fuentes y "
         "fragmentos que coinciden. Para citar o leer más, use «leer_fuente» con el «fuente_id».",
         _schema({"consulta": _text(200, "Palabras a buscar."), "materia": _subject(),
                  "limite": _integer(1, 30, "Cuántos resultados (1 a 30; por defecto 8).")},
                 ["consulta", "materia"]),
         lambda runtime, a: core.search(runtime, a["consulta"], subject=a["materia"],
                                        limit=a.get("limite", 8))),
    Tool("leer_fuente", "Leer una fuente",
         "Lee un fragmento del texto de una fuente de estudio (por su «fuente_id» de «buscar»). Úsela "
         "para citar el material real en lugar de suponer su contenido; «hay_mas» indica si sigue.",
         _schema({"fuente": _text(200, "Identificador de la fuente («fuente_id»)."),
                  "materia": _subject("Materia a la que debe pertenecer la fuente (opcional)."),
                  "offset": _integer(0, 100_000_000, "Carácter desde el que leer (por defecto 0)."),
                  "length": _integer(1, core.MAX_EXCERPT, f"Cuántos caracteres leer (1 a "
                                                          f"{core.MAX_EXCERPT}; por defecto 3000).")},
                 ["fuente"]),
         lambda runtime, a: _without_local_path(core.source(runtime, a["fuente"], subject=a.get("materia"),
                                                            offset=a.get("offset", 0),
                                                            length=a.get("length", 3000)))),
    Tool("anomalias", "Anomalías de los datos",
         "Problemas detectados en los datos del semestre activo (archivos duplicados, textos de prueba, "
         "actividades sin fecha). Opcionalmente de una materia.",
         _schema({"materia": _subject()}),
         lambda runtime, a: anomalies.anomalies(runtime, subject=a.get("materia"))),
]

# --- subject map (odd/tasks/mapa-materia.md) ---------------------------------------------
PLAN_PATTERN = r"^[0-9a-f]{64}$"
SAVE_UNCERTAIN = ("No se sabe si {thing} quedó guardado (la publicación se cortó a mitad). Consulte "
                  "«{reader}» antes de volver a intentarlo.")
SAVE_FAILED = ("{thing} no se guardó: la base publicada en Drive no estaba disponible. Revise que "
               "Google Drive esté abierto y vuelva a intentarlo en un rato.")
# Códigos cuyo mensaje ya es texto sencillo para la persona (se quitan rutas por si acaso).
PLAIN_OPERATION_CODES = ("INVALID_ARGUMENT", "NOT_FOUND", "CONFLICT", "SOURCE_UNAVAILABLE",
                         "STORE_BUSY", "APPROVAL_REQUIRED")


def _operation_message(exc, thing, reader):
    from academic_operations.chat import _public_message
    if getattr(exc, "published", "no") == "incierto":
        return SAVE_UNCERTAIN.format(thing=thing, reader=reader)
    if exc.code == "PLAN_CHANGED":
        return (f"{thing.capitalize()} o la base cambiaron desde la vista previa. Pida una vista previa nueva "
                "(llame sin «aprobar») y vuelva a pedir aprobación.")
    if exc.code in PLAIN_OPERATION_CODES:
        return _public_message(exc)
    return SAVE_FAILED.format(thing=thing.capitalize())


def _save(runtime, args, action, request, *, thing, reader, argument, show):
    """Vista previa sin «aprobar»; con el «plan_id» y aprobar: true, guarda."""
    from academic_operations import chat
    if args.get("aprobar") is True:
        if "plan_id" not in args:
            raise ToolError("Para guardar indique el «plan_id» de la vista previa junto con aprobar: true.")
        try:
            done = chat.apply(runtime, action, request, plan_id=args["plan_id"], approved=True)
        except OperationError as exc:
            raise ToolError(_operation_message(exc, thing, reader)) from None
        return {"guardado": True, "ya_guardado": done["already_applied"], "aviso": done.get("warning")}
    if "plan_id" in args or "aprobar" in args:
        raise ToolError(f"Para guardar, llame con el mismo «{argument}», su «plan_id» y aprobar: true; sin "
                        "ellos solo se genera la vista previa.")
    try:
        preview = chat.preview(runtime, action, request)
    except OperationError as exc:
        raise ToolError(_operation_message(exc, thing, reader)) from None
    return {"guardado": False, "plan_id": preview["plan_id"], "detalle": preview.get("detalle"),
            "ya_guardado": preview["already_applied"],
            "siguiente_paso": (f"Muestre a la persona {show} y pregúntele si lo guarda. Solo si lo aprueba de "
                               f"forma explícita, llame otra vez con el mismo «{argument}», este «plan_id» y "
                               "aprobar: true.")}


def _read_map(runtime, args):
    result = core.subject_map(runtime, args["materia"])
    if result["mapa"] is None:
        result["aviso"] = ("Esta materia todavía no tiene mapa. Puede crearlo con «guardar_mapa» a "
                           "partir del material real («buscar» y «leer_fuente»).")
    return result


def _save_map(runtime, args):
    return _save(runtime, args, "map", {"materia_id": args["materia"], "mapa": args["mapa"]},
                 thing="el mapa", reader="mapa", argument="mapa",
                 show="el resumen del mapa (temas y diagramas)")


MAP_SCHEMA = {
    "type": "object",
    "description": ("Mapa completo de la materia. Campos: «resumen» (opcional, hasta 1000 caracteres); "
                    "«temas» (1 a 80) con «id» único, «titulo», «explicacion» opcional, «padre» (id de "
                    "otro tema; solo el tema raíz va sin padre), «eje» opcional (1 a 12) y «fuentes» "
                    "(hasta 5 objetos {«fuente_id», «cita»}); «relaciones» (hasta 150 objetos {«de», "
                    "«a», «texto»}); «diagramas» (hasta 6 objetos {«titulo», «mermaid»})."),
}

MAP_TOOLS = [
    Tool("mapa", "Mapa de la materia",
         "Devuelve el mapa de estudio guardado de una materia: temas con su explicación y sus fuentes "
         "citadas, relaciones entre temas y diagramas en Mermaid. Úsela para explicar la materia, "
         "repasar o como base antes de actualizar el mapa con «guardar_mapa».",
         _schema({"materia": _subject()}, ["materia"]), _read_map),
    Tool("guardar_mapa", "Guardar el mapa de la materia",
         "Guarda el mapa de estudio de una materia, construido a partir de su material real. Cómo "
         "armarlo: 1) lea el material con «materia», «clases», «buscar» y «leer_fuente» (ejes y clases); "
         "2) defina un tema raíz con el nombre de la materia y entre 15 y 40 temas más con su «padre», "
         "cada uno con una «explicacion» breve y clara en español; 3) cada tema que no sea la raíz lleva "
         "de 1 a 3 «fuentes»: el «fuente_id» y una «cita» copiada literal (12 a 400 caracteres) del texto "
         "que devolvió leer_fuente; las citas se comprueban y una cita inventada hace fallar la vista "
         "previa; 4) añada relaciones entre temas con un verbo corto («usa», «es un tipo de», «depende "
         "de»); 5) añada 1 a 3 diagramas mermaid: un «mindmap» de toda la materia y un «flowchart» con "
         "las relaciones clave, con etiquetas cortas y sin directivas %%{ }%%. Primero llámela sin "
         "«aprobar»: valida el mapa, devuelve un resumen y un «plan_id» y no guarda nada. Muestre el "
         "resumen a la persona y solo si ella lo aprueba de forma explícita, llame otra vez con el mismo "
         "«mapa», ese «plan_id» y aprobar: true. Guardar reemplaza el mapa anterior de la materia: para "
         "cambiar uno existente, léalo con «mapa», modifíquelo y guárdelo completo.",
         _schema({"materia": _subject(), "mapa": MAP_SCHEMA,
                  "plan_id": {"type": "string", "pattern": PLAN_PATTERN,
                              "description": "«plan_id» que devolvió la vista previa (solo para guardar)."},
                  "aprobar": {"type": "boolean",
                              "description": "true solo cuando la persona aprobó guardar este mapa."}},
                 ["materia", "mapa"]),
         _save_map, read_only=False, destructive=True),
]

# --- class index (odd/tasks/indice-clase.md) ----------------------------------------------
CLASS_PATTERN = r"^CLS-[A-Za-z0-9_-]{1,70}$"


def _class(text="Identificador exacto de la clase (CLS-…), como lo devuelve «clases»."):
    return {"type": "string", "pattern": CLASS_PATTERN, "description": text}


def _read_index(runtime, args):
    result = core.class_index(runtime, args["clase"])
    if result["indice"] is None:
        result["aviso"] = ("Esta clase todavía no tiene índice. Puede crearlo con «guardar_indice_clase» a "
                           "partir de su transcripción («leer_fuente»).")
    return result


def _save_index(runtime, args):
    return _save(runtime, args, "class-index", {"clase_id": args["clase"], "indice": args["indice"]},
                 thing="el índice", reader="indice_clase", argument="indice",
                 show="el resumen del índice (temas y momentos)")


INDEX_SCHEMA = {
    "type": "object",
    "description": ("Índice completo de la clase. Campos: «resumen» (3 a 5 líneas, hasta 900 caracteres: de qué "
                    "trató la clase a grandes rasgos); «temas» (1 a 40) con «titulo», «categoria» opcional "
                    "(instrucciones, fechas, evaluacion, tareas o tema) y «momentos» (1 a 30 objetos {«inicio», "
                    "«texto», «cita»})."),
}

INDEX_TOOLS = [
    Tool("indice_clase", "Índice de la clase",
         "Devuelve el índice guardado de una clase: un resumen muy corto y, por cada tema, los minutos en "
         "que se habló de él. Úsela para responder en qué parte de la clase se dijo algo o para llevar a la "
         "persona a ese minuto.",
         _schema({"clase": _class()}, ["clase"]), _read_index),
    Tool("guardar_indice_clase", "Guardar el índice de la clase",
         "Guarda el índice de una clase: dónde se encuentra cada cosa que se habló, con sus minutos. Cómo "
         "armarlo: 1) lea toda la transcripción con «leer_fuente», fuente_id «clase:<clase>:transcripcion» "
         "(por partes, con «offset», hasta que «hay_mas» sea false); 2) escriba un «resumen» de 3 a 5 líneas "
         "con lo que se trató a grandes rasgos; 3) agrupe por tema todo lo que dijo el profesor: un tema "
         "reúne todos sus momentos aunque estén separados en la clase; 4) use las categorías fijas para lo "
         "administrativo: «instrucciones» (cómo hacer o entregar algo), «fechas», «evaluacion» y «tareas»; "
         "el contenido de la materia va en «tema»; 5) cada momento lleva «inicio» (el tiempo inicial del "
         "fragmento, HH:MM:SS, copiado tal cual de la transcripción), «texto» (qué se dice ahí, una línea) "
         "y «cita» (de 12 a 300 caracteres copiados literalmente de ese mismo fragmento). Los minutos se "
         "comprueban: un tiempo o una cita que no coincidan hacen fallar la vista previa. Primero llámela "
         "sin «aprobar»: valida el índice, devuelve un resumen y un «plan_id» y no guarda nada. Muéstrelo a "
         "la persona y solo si ella lo aprueba de forma explícita, llame otra vez con el mismo «indice», ese "
         "«plan_id» y aprobar: true. Guardar reemplaza el índice anterior de la clase y no toca su análisis.",
         _schema({"clase": _class(), "indice": INDEX_SCHEMA,
                  "plan_id": {"type": "string", "pattern": PLAN_PATTERN,
                              "description": "«plan_id» que devolvió la vista previa (solo para guardar)."},
                  "aprobar": {"type": "boolean",
                              "description": "true solo cuando la persona aprobó guardar este índice."}},
                 ["clase", "indice"]),
         _save_index, read_only=False, destructive=True),
]


# --- process a class from Claude (odd/tasks/capture-desde-claude.md) ------------------------
JOB_ID_PATTERN = r"^[0-9a-f]{32}$"
DRIVE_LINK_PATTERN = r"^https://(drive|docs)\.google\.com/\S{1,300}$"
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


def _operation(step):
    """Runs one step; these messages are already plain and carry no paths."""
    try:
        return step()
    except OperationError as exc:
        raise ToolError(str(exc)) from None


def _half_approval(args, argument):
    if args.get("aprobar") is True and "plan_id" not in args:
        raise ToolError("Para empezar indique el «plan_id» de la vista previa junto con aprobar: true.")
    if args.get("aprobar") is not True and ("plan_id" in args or "aprobar" in args):
        raise ToolError(f"Para empezar, llame con {argument}, su «plan_id» y aprobar: true; sin ellos solo se "
                        "genera la vista previa.")


def _process(runtime, args):
    from academic_operations import class_capture as cc
    _half_approval(args, "el mismo enlace, materia y fecha")
    target = (runtime, args["enlace"], args["materia"], args["fecha"])
    if args.get("aprobar") is True:
        started = _operation(lambda: cc.start(*target, plan_id=args["plan_id"], approved=True))
        return {"iniciado": True, **started,
                "siguiente_paso": ("Capture sigue en este PC aunque se cierre la conversación. Consulte el avance "
                                   "con «capturas»; la grabación se transcribe en varios minutos.")}
    preview = _operation(lambda: cc.plan(*target))
    return {"iniciado": False, **preview,
            "siguiente_paso": ("Muestre a la persona la grabación, la materia y la fecha y pregúntele si la procesa. "
                               "Solo si lo aprueba de forma explícita, llame otra vez con los mismos datos, este "
                               "«plan_id» y aprobar: true.")}


def _complete(runtime, args):
    from academic_operations import class_capture as cc
    _half_approval(args, "el mismo «analisis»")
    if args.get("aprobar") is True:
        started = _operation(lambda: cc.complete_start(runtime, args["trabajo"], args["analisis"],
                                                       plan_id=args["plan_id"], approved=True))
        return {"iniciado": True, **started, "siguiente_paso": "Consulte el resultado con «capturas»."}
    preview = _operation(lambda: cc.complete_preview(runtime, args["trabajo"], args["analisis"]))
    return {"iniciado": False, **preview,
            "siguiente_paso": ("Muestre a la persona el análisis y los pasos, y pregúntele si lo publica y registra. "
                               "Solo si lo aprueba de forma explícita, llame otra vez con el mismo «analisis», este "
                               "«plan_id» y aprobar: true.")}


def _jobs(runtime, args):
    from academic_operations import class_capture as cc
    return {"en_curso": cc.status(runtime),
            "trabajos": core.capture_jobs(runtime, limit=args.get("limite", 15))["trabajos"]}


def _read_transcript(runtime, args):
    from academic_operations import class_capture as cc
    return _operation(lambda: cc.transcript(runtime, args["trabajo"], offset=args.get("offset", 0),
                                            length=args.get("length", 12_000)))


def _job(text="Identificador del trabajo, como lo devuelve «capturas»."):
    return {"type": "string", "pattern": JOB_ID_PATTERN, "description": text}


PLAN_ARGUMENTS = {"plan_id": {"type": "string", "pattern": PLAN_PATTERN,
                              "description": "«plan_id» que devolvió la vista previa (solo para empezar)."},
                  "aprobar": {"type": "boolean",
                              "description": "true solo cuando la persona aprobó de forma explícita."}}

CAPTURE_TOOLS = [
    Tool("capturas", "Clases en proceso",
         "Muestra el proceso en curso y los últimos trabajos de Capture: materia, fecha, fase, avance, y si ya "
         "tienen análisis y están publicados y registrados. Úsela para seguir «procesar_clase» y para encontrar "
         "clases transcritas que falta completar (database_registered false).",
         _schema({"limite": _integer(1, 100, "Cuántos trabajos mostrar (por defecto 15).")}), _jobs),
    Tool("leer_transcripcion", "Leer la transcripción de un trabajo",
         "Lee por partes la transcripción de un trabajo de Capture, aunque la clase aún no esté registrada. "
         "Siga con «siguiente_offset» hasta que «hay_mas» sea false.",
         _schema({"trabajo": _job(), "offset": _integer(0, 100_000_000, "Carácter desde el que leer."),
                  "length": _integer(1, 20_000, "Cuántos caracteres leer (por defecto 12000).")}, ["trabajo"]),
         _read_transcript),
    Tool("procesar_clase", "Procesar una clase",
         "Procesa una grabación de clase de Google Drive con Capture: la descarga y la transcribe en este PC, en "
         "un proceso aparte. Primero llámela sin «aprobar»: no empieza nada y devuelve la grabación, la materia "
         "y un «plan_id». Muéstrelo a la persona y solo si ella lo aprueba de forma explícita, llame otra vez "
         "con los mismos datos, ese «plan_id» y aprobar: true. Se procesa una clase a la vez: si hay otra, "
         "espere con «capturas». Al terminar, lea la transcripción con «leer_transcripcion» y siga con "
         "«completar_clase». Una clase nunca se registra sin ese paso.",
         _schema({"enlace": {"type": "string", "pattern": DRIVE_LINK_PATTERN,
                             "description": "Enlace de Google Drive de la grabación."},
                  "materia": _subject(),
                  "fecha": {"type": "string", "pattern": DATE_PATTERN,
                            "description": "Fecha de la clase, YYYY-MM-DD, según la grabación o la persona."},
                  **PLAN_ARGUMENTS}, ["enlace", "materia", "fecha"]),
         _process, read_only=False),
    Tool("completar_clase", "Completar una clase",
         "Termina una clase transcrita: incorpora el análisis, la publica en Drive y la registra. Escriba "
         "«analisis» en Markdown con encabezados, a partir de TODA la transcripción («leer_transcripcion»), sin "
         "inventar nada y marcando lo que no se entienda. Primero llámela sin «aprobar»: guarda el borrador, "
         "comprueba la clase y devuelve los pasos y un «plan_id». Muéstrelo a la persona y solo si ella lo "
         "aprueba de forma explícita, llame otra vez con el mismo «analisis», ese «plan_id» y aprobar: true. "
         "Corre en segundo plano: consulte el resultado con «capturas».",
         _schema({"trabajo": _job(),
                  "analisis": {"type": "string", "maxLength": 1_000_000,
                               "description": "Análisis completo en Markdown (hasta 1 000 000 de caracteres)."},
                  **PLAN_ARGUMENTS}, ["trabajo", "analisis"]),
         _complete, read_only=False),
]


def _without_local_path(found):
    """Claude cites by «fuente_id»; the text file's absolute path on this PC is not needed."""
    if isinstance(found, dict):
        found = {k: v for k, v in found.items() if k != "ruta_texto"}
    return found


TOOLS = {tool.name: tool for tool in [*_TOOLS, *MAP_TOOLS, *INDEX_TOOLS, *CAPTURE_TOOLS]}


def call(name, arguments, runtime):
    """Runs one tool: JSON-serializable result, or `ToolError` with a plain message."""
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError("Esa herramienta no existe en Academic Manager.")
    args = _check(tool.schema, arguments)
    try:
        result = tool.handler(Path(runtime), args)
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 - nothing internal may reach the person
        _log(name, exc)
        raise ToolError(plain_message(exc)) from None
    return _jsonable(result)
