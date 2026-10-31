"""Botones y formularios para registrar desde el panel (sin scripts: <details> y formularios).

Todos envían a POST /panel/registrar, que muestra la vista previa; nada se escribe sin
pulsar «Aprobar y publicar» en ella. Los valores visibles pasan por esc().
"""
from .pages import csrf_field, esc
from .registrar import NOTE_TYPES, TASK_STATES, TRACK_STATES

ACTIVITY_TYPES = ("actividad_vencida", "actividad_proxima")
TASK_TYPES = ("tarea", "tarea_sin_fecha")


def _hidden(values):
    return "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k, v in values.items()
                   if v not in (None, ""))


def _form(csrf, values, inner, button, *, small=False):
    return (f'<form method="post" action="/panel/registrar" class="inline-form">{csrf_field(csrf)}'
            f'{_hidden(values)}{inner}<button{" class=small" if small else ""}>{esc(button)}</button></form>')


def _options(choices, selected):
    return "".join(f'<option value="{esc(k)}"{" selected" if k == selected else ""}>{esc(v)}</option>'
                   for k, v in choices.items())


def _amount(value):
    if value in (None, ""):
        return ""
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)


def grade_form(csrf, activity, title, back, *, current=None, ident="", summary="Poner nota o cambiar estado"):
    """Estado, nota, puntos y comentario de una actividad (acción `track`)."""
    current = current or {}
    state = current.get("estado") if current.get("estado") in TRACK_STATES else "calificada"
    inner = (f'<label for="estado-{esc(ident)}">Estado</label>'
             f'<select id="estado-{esc(ident)}" name="estado">{_options(TRACK_STATES, state)}</select>'
             '<div class="two">'
             f'<div><label for="nota-{esc(ident)}">Nota</label><input id="nota-{esc(ident)}" type="text" '
             f'inputmode="decimal" name="nota" value="{esc(_amount(current.get("nota")))}" placeholder="4.5"></div>'
             f'<div><label for="puntos-{esc(ident)}">Puntos</label><input id="puntos-{esc(ident)}" type="text" '
             f'inputmode="decimal" name="puntos" value="{esc(_amount(current.get("puntos")))}"></div></div>'
             f'<label for="comentario-{esc(ident)}">Comentario (opcional)</label>'
             f'<textarea id="comentario-{esc(ident)}" name="comentario" rows="2">'
             f'{esc(current.get("comentario") or "")}</textarea>'
             '<p class="muted small">Si deja la nota o los puntos vacíos, se conserva lo que ya estaba.</p>')
    values = {"accion": "track", "actividad_id": activity, "etiqueta": title, "volver": back}
    return f'<details class="reg"><summary>{esc(summary)}</summary>{_form(csrf, values, inner, "Revisar")}</details>'


def delivered_button(csrf, activity, title, back):
    return _form(csrf, {"accion": "track", "actividad_id": activity, "estado": "entregada", "etiqueta": title,
                        "volver": back}, "", "Marcar como entregada", small=True)


def task_buttons(csrf, task, title, back, *, state="pendiente", ident=""):
    """«Marcar como hecha» y, desplegable, cualquier otro estado (acción `task-status`)."""
    base = {"accion": "task-status", "tarea_id": task, "etiqueta": title, "volver": back}
    done = _form(csrf, {**base, "estado": "hecha"}, "", "Marcar como hecha", small=True)
    others = {k: v for k, v in TASK_STATES.items() if k != state}
    inner = (f'<label for="tarea-{esc(ident)}">Nuevo estado</label>'
             f'<select id="tarea-{esc(ident)}" name="estado">{_options(others, "en_progreso")}</select>')
    return done + f'<details class="reg"><summary>Otro estado</summary>{_form(csrf, base, inner, "Revisar")}</details>'


def item_actions(csrf, back):
    """Acciones rápidas de cada tarjeta de pendiente (página «Hoy» y de cada materia)."""
    def actions(item):
        if item.get("tipo") in ACTIVITY_TYPES and item.get("actividad_id"):
            return ('<div class="item-actions">'
                    + delivered_button(csrf, item["actividad_id"], item.get("titulo") or "", back)
                    + grade_form(csrf, item["actividad_id"], item.get("titulo") or "", back,
                                 ident=item["actividad_id"]) + "</div>")
        if item.get("tipo") in TASK_TYPES and item.get("tarea_id"):
            return ('<div class="item-actions">'
                    + task_buttons(csrf, item["tarea_id"], item.get("titulo") or "", back, ident=item["tarea_id"])
                    + "</div>")
        return ""
    return actions


def new_task_form(csrf, subject, back):
    inner = ('<label for="nueva-titulo">Título</label>'
             '<input id="nueva-titulo" type="text" name="titulo" maxlength="200" required>'
             '<label for="nueva-fecha">Fecha límite (opcional)</label>'
             '<input id="nueva-fecha" type="date" name="fecha_limite">'
             '<label for="nueva-detalle">Detalle (opcional)</label>'
             '<textarea id="nueva-detalle" name="detalle" rows="2"></textarea>')
    return _form(csrf, {"accion": "task", "materia_id": subject, "volver": back}, inner, "Revisar")


def new_note_form(csrf, subject, back):
    inner = ('<label for="nota-tipo">Tipo</label>'
             f'<select id="nota-tipo" name="tipo">{_options(NOTE_TYPES, "decision")}</select>'
             '<label for="nota-texto">Texto</label>'
             '<textarea id="nota-texto" name="texto" rows="3" maxlength="4000" required></textarea>')
    return _form(csrf, {"accion": "note", "materia_id": subject, "volver": back}, inner, "Revisar")
