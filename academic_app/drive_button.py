"""Botón «Transcripción de Drive» (odd/tasks/transcripcion-ingles.md, S1).

Un marcador del navegador: con una grabación abierta en Google Drive, pide a Drive la
transcripción automática que muestra el reproductor y la guarda en Descargas como
`<grabación> [<código de Drive>].vtt`. Corre en la página de Drive con la sesión de la persona
y no envía nada a ninguna otra parte; Claude la importa después con «importar_transcripciones».

Drive aplica Trusted Types, así que la lista de transcripciones se lee con una expresión
regular y no con DOMParser. La dirección se arma con el código de la grabación: Drive la
entrega sin el permiso de sesión del reproductor (comprobado el 2026-11-12).
"""
from urllib.parse import quote

from .pages import esc

SCRIPT = r"""(async () => {
const m = location.pathname.match(/\/file\/d\/([-\w]{20,})/);
if (location.hostname !== 'drive.google.com' || !m) { alert('Abra primero la grabación en Google Drive y pulse el botón ahí.'); return; }
const id = m[1];
const user = (location.pathname.match(/\/u\/(\d+)\//) || [])[1] || new URLSearchParams(location.search).get('authuser') || '0';
const base = '/u/' + user + '/timedtext?id=' + id + '&v=' + id;
try {
const list = await (await fetch(base + '&type=list', {credentials: 'include'})).text();
const track = (list.match(/<track\b[^>]*>/) || [])[0];
if (!track) { alert('Esta grabación no tiene transcripción en Drive.'); return; }
const attr = n => (track.match(new RegExp(n + '="([^"]*)"')) || [])[1];
let url = base + '&type=track&fmt=vtt&lang=' + encodeURIComponent(attr('lang_code'));
if (attr('kind')) url += '&kind=' + encodeURIComponent(attr('kind'));
const vtt = await (await fetch(url, {credentials: 'include'})).text();
if (!vtt.startsWith('WEBVTT')) { alert('Drive no entregó la transcripción. Recargue la página e inténtelo otra vez.'); return; }
const a = document.createElement('a');
a.href = URL.createObjectURL(new Blob([vtt], {type: 'text/vtt'}));
a.download = document.title.replace(/ - Google Drive$/, '').replace(/[\\/:*?"<>|]/g, '_').trim() + ' [' + id + '].vtt';
document.body.appendChild(a); a.click(); a.remove();
alert('Listo: la transcripción quedó en Descargas. Pídale a Claude que la importe.');
} catch (e) { alert('No se pudo descargar la transcripción. Recargue la página e inténtelo otra vez.'); }
})();"""

NAME = "Transcripción de Drive"


def href():
    """La dirección del marcador: `javascript:` y el código, sin saltos de línea."""
    return "javascript:" + quote(" ".join(line.strip() for line in SCRIPT.splitlines()), safe="")


def card(copy_box):
    """Tarjeta de «Configuración» → «Claude» con el botón para arrastrar y el código para pegar a mano."""
    return (f'<div class="card"><h2>Botón «{esc(NAME)}»</h2>'
            '<p>Sirve para las grabaciones que Drive no deja descargar (por ejemplo, las de inglés): guarda en '
            'Descargas la transcripción que muestra el reproductor, y Claude crea la clase con ella.</p>'
            '<ol><li>Muestre la barra de marcadores de su navegador (Ctrl+Mayús+B).</li>'
            f'<li>Arrastre este botón hasta la barra: <a class="marcador" href="{esc(href())}">{esc(NAME)}</a>. '
            'Si no se deja arrastrar, cree un marcador nuevo y pegue como dirección el texto de abajo.</li>'
            '<li>Abra la grabación en Google Drive con la cuenta de la universidad y pulse el marcador.</li>'
            '<li>Pídale a Claude: «Importa las transcripciones descargadas de inglés».</li></ol>'
            '<p class="muted">El botón solo lee la transcripción de la grabación abierta y la guarda en su PC; no '
            'envía nada a ninguna otra parte. Aquí no hace nada si lo pulsa: funciona en la página de Drive.</p>'
            + copy_box("drive-button", "Dirección del marcador", href()) + '</div>')
