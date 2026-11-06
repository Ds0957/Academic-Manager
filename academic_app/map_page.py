"""Mapa de la materia en el panel (odd/tasks/mapa-materia.md, S6).

El árbol de temas, sus citas y las relaciones son HTML simple: se leen sin JavaScript, también
desde el celular. Solo los diagramas necesitan Mermaid (academic_app.assets); sin él quedan como
texto.
"""
from . import assets, panel
from .pages import esc

STYLE = ("<style>.mapa-arbol,.mapa-arbol ul{list-style:none;margin:0;padding-left:1.1rem;"
         "border-left:2px solid var(--line,#e5e7eb)}.mapa-arbol{padding-left:.4rem}"
         ".mapa-arbol li{margin:.55rem 0}.mapa-arbol details{margin-top:.25rem}"
         ".mapa-arbol blockquote{margin:.3rem 0 .3rem .4rem;padding-left:.6rem;"
         "border-left:3px solid #c7c9ff;font-style:italic}"
         ".mapa-diagrama{overflow-x:auto}.mapa-diagrama pre.mermaid{background:none;white-space:pre}"
         "</style>")
HOW_TO = ("En Claude Desktop, con el conector de Academic Manager, pídale: «Arma el mapa de {name} con "
          "su material». Claude le mostrará una vista previa y solo lo guardará si usted lo aprueba.")


def _children(mapa):
    by_parent = {}
    for topic in mapa["temas"]:
        by_parent.setdefault(topic.get("padre"), []).append(topic)
    return by_parent


def _topic(topic, by_parent):
    eje = f' {panel.chip("Eje " + str(topic["eje"]), "k-info")}' if topic.get("eje") else ""
    text = f'<small>{esc(topic["explicacion"])}</small>' if topic.get("explicacion") else ""
    sources = topic.get("fuentes") or []
    cited = ("<details><summary>" + esc(panel.count(len(sources), "fuente", "fuentes")) + "</summary>"
             + "".join(f'<blockquote>{esc(s["cita"])}</blockquote><small class="muted">{esc(s["fuente_id"])}'
                       "</small>" for s in sources)
             + "</details>") if sources else ""
    below = by_parent.get(topic["id"]) or []
    nested = ("<ul>" + "".join(_topic(t, by_parent) for t in below) + "</ul>") if below else ""
    return f'<li><strong>{esc(topic["titulo"])}</strong>{eje}{text}{cited}{nested}</li>'


def tree(mapa):
    """Temas anidados por «padre», en el orden en que vienen en el mapa."""
    by_parent = _children(mapa)
    return '<ul class="mapa-arbol">' + "".join(_topic(t, by_parent) for t in by_parent.get(None, [])) + "</ul>"


def relations(mapa):
    titles = {t["id"]: t["titulo"] for t in mapa["temas"]}
    rows = "".join(f'<li><div class="grow"><strong>{esc(titles[r["de"]])}</strong> {esc(r["texto"])} '
                   f'<strong>{esc(titles[r["a"]])}</strong></div></li>' for r in mapa.get("relaciones") or [])
    return f'<div class="card"><h2>Cómo se relacionan</h2><ul class="rows">{rows}</ul></div>' if rows else ""


def diagrams(mapa):
    return "".join(f'<div class="card mapa-diagrama"><h2>{esc(d["titulo"])}</h2>'
                   f'<pre class="mermaid">{esc(d["mermaid"])}</pre></div>' for d in mapa.get("diagramas") or [])


def page(result, name):
    """Cuerpo de la página del mapa; los scripts solo se cargan si hay diagramas."""
    mapa = result["mapa"]
    if mapa is None:
        return panel.empty("Esta materia todavía no tiene mapa", HOW_TO.format(name=name))
    updated = panel.utc_day_text(result.get("actualizado_utc"))
    intro = (f'<p>{esc(mapa["resumen"])}</p>' if mapa.get("resumen") else "") + (
        f'<p class="muted">Actualizado el {esc(updated)}.</p>' if updated else "")
    drawn = diagrams(mapa)
    return (STYLE + intro + drawn
            + f'<div class="card"><h2>Temas</h2>{tree(mapa)}</div>' + relations(mapa)
            + (assets.scripts() if drawn else ""))


def card(result, link):
    """Tarjeta de la página de la materia: cuántos temas tiene y el enlace al mapa."""
    mapa = result["mapa"] if result else None
    if mapa is None:
        text = ('<p class="muted">Todavía no tiene mapa. Puede pedirle a Claude que lo arme con el '
                "material de la materia.</p>")
    else:
        text = (f'<p>{esc(panel.count(len(mapa["temas"]), "tema", "temas"))}, '
                f'{esc(panel.count(len(mapa.get("diagramas") or []), "diagrama", "diagramas"))}.</p>')
    return (f'<div class="card"><h2>Mapa de la materia</h2>{text}'
            f'<p><a href="{esc(link)}">Ver el mapa</a></p></div>')
