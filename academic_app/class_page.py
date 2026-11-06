"""Índice de la clase en el panel (odd/tasks/indice-clase.md, S6).

Un resumen muy corto y, como contenido principal, el índice: cada tema con los minutos en que se
habló de él. Cada minuto abre la transcripción en ese fragmento. Todo es HTML simple: se lee sin
JavaScript, también desde el celular.
"""
from . import panel
from .pages import esc

STYLE = ("<style>.indice-momentos{list-style:none;margin:0;padding:0}.indice-momentos li{display:flex;"
         "gap:.75rem;padding:.35rem 0;align-items:baseline}.indice-momentos small{display:block}"
         ".indice-momentos a,.transcripcion a{"
         "font-variant-numeric:tabular-nums;white-space:nowrap}.transcripcion p{margin:.5rem 0;"
         "padding:.35rem .5rem;border-radius:6px}.transcripcion p:target{background:rgba(250,204,21,.28)}"
         "</style>")
LABELS = {"instrucciones": "Instrucciones", "fechas": "Fechas", "evaluacion": "Evaluación", "tareas": "Tareas"}
HOW_TO = ("En Claude Desktop, con el conector de Academic Manager, pídale: «Arma el índice de la clase del "
          "{day}». Claude le mostrará una vista previa y solo lo guardará si usted lo aprueba.")


def anchor(start):
    return "t-" + start.replace(":", "-")


def _moment(moment, link):
    target = f'{link}#{anchor(moment["inicio"])}'
    return (f'<li><a href="{esc(target)}">{esc(moment["inicio"])}</a><div>{esc(moment["texto"])}'
            f'<small class="muted">«{esc(moment["cita"])}»</small></div></li>')


def _topic(topic, link):
    label = LABELS.get(topic["categoria"])
    chip = f' {panel.chip(label, "k-info")}' if label else ""
    moments = "".join(_moment(m, link) for m in topic["momentos"])
    return (f'<div class="card"><h2>{esc(topic["titulo"])}{chip}</h2>'
            f'<ul class="indice-momentos">{moments}</ul></div>')


def page(result, link):
    """Cuerpo de la página de la clase; `link` es la dirección de su transcripción."""
    indice = result["indice"]
    if indice is None:
        day = panel.day_text(result.get("fecha")) or "esta fecha"
        return panel.empty("Esta clase todavía no tiene índice", HOW_TO.format(day=day))
    # Lo administrativo va primero; dentro de cada grupo se respeta el orden del índice.
    topics = sorted(indice["temas"], key=lambda t: t["categoria"] == "tema")
    return (STYLE + f'<div class="card"><h2>De qué trató</h2><p>{esc(indice["resumen"])}</p></div>'
            + "".join(_topic(t, link) for t in topics))


def transcript(fragments):
    """Transcripción con un ancla por fragmento: el minuto del índice abre justo ahí."""
    if not fragments:
        return panel.empty("Esta transcripción no tiene tiempos", "Solo se puede leer el texto completo.")
    rows = "".join(f'<p id="{esc(anchor(f["inicio"]))}"><a href="#{esc(anchor(f["inicio"]))}">{esc(f["inicio"])}'
                   f'</a> {esc(f["texto"])}</p>' for f in fragments)
    return STYLE + f'<div class="card transcripcion">{rows}</div>'


def card(rows, base):
    """Tarjeta de la página de la materia: las clases que ya tienen índice."""
    if not rows:
        text = ('<p class="muted">Todavía no hay índices. Puede pedirle a Claude que arme el de una clase '
                "con su transcripción.</p>")
    else:
        items = "".join(
            f'<li><div class="grow"><a href="{esc(base + r["clase_id"])}">'
            f'{esc(panel.day_text(r["fecha"]) or r["clase_id"])}</a><small>'
            f'{esc(panel.count(r["temas"], "tema", "temas"))}, '
            f'{esc(panel.count(r["momentos"], "momento", "momentos"))}</small></div></li>' for r in rows)
        text = f'<ul class="rows">{items}</ul>'
    return f'<div class="card"><h2>Índices de clase</h2>{text}</div>'
