"""HTML del asistente: todo valor dinámico pasa por esc(); sin recursos externos y un único script fijo."""
import base64
import hashlib
import html
from dataclasses import dataclass

STATES = {"hecho": "Hecho", "falta": "Falta", "error": "Error"}

STYLE = """
:root { --bg:#f3f4f7; --card:#fff; --soft:#f6f7f9; --text:#1c2230; --muted:#5e6777; --line:#e3e6eb;
        --ok:#1a7f37; --ok-soft:#e7f4ea; --todo:#9a6700; --todo-soft:#fff4dc; --bad:#c62828; --bad-soft:#fdecec;
        --info:#1d5fd1; --info-soft:#e8f0fe; --task:#6d3fd6; --task-soft:#f1ebff;
        --accent:#4f46e5; --accent-soft:#eef0ff; --on-accent:#fff;
        --shadow:0 1px 2px rgba(16,24,40,.04), 0 2px 8px rgba(16,24,40,.05); }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0e1117; --card:#161a22; --soft:#1b2029; --text:#e6e9ef; --muted:#9aa3b2; --line:#272d38;
          --ok:#4cc26a; --ok-soft:#15301e; --todo:#e0a93a; --todo-soft:#33280f; --bad:#ff6b6b; --bad-soft:#3a1a1d;
          --info:#6ea8ff; --info-soft:#17284a; --task:#b69cff; --task-soft:#291f45;
          --accent:#8c8cff; --accent-soft:#23254a; --on-accent:#0e1117;
          --shadow:0 1px 2px rgba(0,0,0,.3); } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font: 16px/1.55 system-ui, "Segoe UI", sans-serif; -webkit-font-smoothing: antialiased; }
main { max-width: 780px; margin: 0 auto; padding: 24px 16px 64px; }
main.wide { max-width: 1140px; }
h1 { font-size: 1.75rem; line-height:1.2; margin: 0; letter-spacing:-.01em; }
h2 { font-size: 1.15rem; margin: 28px 0 10px; } h3 { font-size:1rem; margin:0; }
a { color: var(--accent); }
.topbar { background:var(--card); border-bottom:1px solid var(--line); position:sticky; top:0; z-index:5; }
.topbar-in { max-width:1140px; margin:0 auto; padding:10px 16px; display:flex; align-items:center;
             gap:8px 20px; flex-wrap:wrap; }
.brand { display:flex; align-items:center; gap:9px; font-weight:700; color:var(--text); white-space:nowrap; }
.brand svg { width:28px; height:28px; flex:none; }
.page-head { display:flex; align-items:center; gap:14px; margin: 6px 0 20px; }
.page-head .sub { margin:4px 0 0; color:var(--muted); }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px; box-shadow:var(--shadow);
        padding:18px 20px; margin:14px 0; }
.card > h2:first-child { margin-top:0; }
.step { display:flex; gap:12px; align-items:center; justify-content:space-between; flex-wrap:wrap; }
.badge { display:inline-block; border-radius:999px; padding:1px 10px; font-size:.82rem;
         font-weight:600; border:1px solid currentColor; }
.hecho { color:var(--ok); } .falta { color:var(--todo); } .error { color:var(--bad); }
.muted { color:var(--muted); }
.msg { border-left:4px solid var(--accent); background:var(--accent-soft); box-shadow:none; }
.msg.bad { border-left-color:var(--bad); background:var(--bad-soft); }
.msg p { margin:.3em 0; }
label { display:block; margin:10px 0 4px; font-weight:600; }
input[type=text], input[type=password], input[type=datetime-local] {
  width:100%; padding:9px 10px; font:inherit; border:1px solid var(--line);
  border-radius:9px; background:var(--card); color:var(--text); }
button { font:inherit; font-weight:600; padding:9px 18px; border-radius:9px; border:1px solid var(--accent);
         background:var(--accent); color:var(--on-accent); cursor:pointer; margin-top:12px; }
button:hover { filter:brightness(1.08); }
button.secondary { background:transparent; color:var(--accent); }
ol li, ul li { margin: 6px 0; }
pre { white-space: pre-wrap; background:var(--soft); padding:12px; border-radius:9px; }
table { border-collapse: collapse; width:100%; } td, th { text-align:left; padding:6px 4px;
        border-bottom:1px solid var(--line); vertical-align: top; }
.inline { display:inline; font-weight:normal; margin-right:14px; }
/* --- panel ------------------------------------------------------------------------- */
.panel-nav { display:flex; gap:4px; overflow-x:auto; margin-left:auto; max-width:100%; }
.panel-nav a { text-decoration:none; color:var(--muted); font-weight:600; padding:6px 13px;
               border-radius:999px; white-space:nowrap; }
.panel-nav a:hover { background:var(--soft); color:var(--text); }
.panel-nav a[aria-current=page] { background:var(--accent-soft); color:var(--accent); }
.stats { display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:12px; margin:14px 0; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px;
        box-shadow:var(--shadow); border-top:3px solid var(--line); }
.stat .num { display:block; font-size:1.9rem; font-weight:750; line-height:1.1; }
.stat .lbl { color:var(--muted); font-size:.9rem; }
.stat.k-bad { border-top-color:var(--bad); } .stat.k-bad .num { color:var(--bad); }
.stat.k-info { border-top-color:var(--info); } .stat.k-task { border-top-color:var(--task); }
.stat.k-todo { border-top-color:var(--todo); }
.grid { display:grid; grid-template-columns: minmax(0, 1.7fr) minmax(0, 1fr); gap:0 22px; align-items:start; }
.cards { display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:14px; margin:14px 0; }
.cards .card { margin:0; }
.cards.wide-cards { grid-template-columns:repeat(auto-fill, minmax(340px, 1fr)); }
@media (max-width: 820px) { .grid { grid-template-columns: minmax(0, 1fr); } }
.group-title { display:flex; align-items:center; gap:8px; font-size:.82rem; text-transform:uppercase;
               letter-spacing:.06em; color:var(--muted); margin:26px 0 10px; }
.group-title.k-bad { color:var(--bad); }
.count { background:var(--soft); border:1px solid var(--line); color:var(--text); border-radius:999px;
         padding:0 8px; font-size:.78rem; letter-spacing:0; }
.items { display:flex; flex-direction:column; gap:10px; }
.item { display:flex; gap:14px; align-items:center; background:var(--card); border:1px solid var(--line);
        border-left:4px solid var(--tone, var(--line)); border-radius:12px; padding:12px 16px;
        box-shadow:var(--shadow); }
.item-main { flex:1; min-width:0; }
.item-top { display:flex; flex-wrap:wrap; gap:6px 10px; align-items:center; margin-bottom:3px; }
.item-title { font-size:1.02rem; overflow-wrap:anywhere; }
.item-meta { margin:2px 0 0; color:var(--muted); font-size:.9rem; }
.rel { font-weight:600; font-size:.88rem; color:var(--muted); white-space:nowrap; }
.rel.k-bad { color:var(--bad); } .rel.soon { color:var(--todo); }
.when { flex:none; width:52px; text-align:center; border-radius:10px; background:var(--soft);
        border:1px solid var(--line); padding:4px 0; line-height:1.15; }
.when .w, .when .m { display:block; font-size:.7rem; text-transform:uppercase; color:var(--muted);
                     letter-spacing:.04em; }
.when .d { display:block; font-size:1.25rem; font-weight:750; }
.when.none .d { color:var(--muted); }
.chip { display:inline-flex; align-items:center; gap:6px; border-radius:999px; padding:1px 9px;
        font-size:.78rem; font-weight:650; background:var(--soft); color:var(--muted); }
.chip.k-bad { background:var(--bad-soft); color:var(--bad); }
.chip.k-info { background:var(--info-soft); color:var(--info); }
.chip.k-task { background:var(--task-soft); color:var(--task); }
.chip.k-todo { background:var(--todo-soft); color:var(--todo); }
.chip.k-ok { background:var(--ok-soft); color:var(--ok); }
.subject { display:inline-flex; align-items:center; gap:6px; font-size:.86rem; font-weight:600;
           color:var(--text); text-decoration:none; }
a.subject:hover { text-decoration:underline; }
.dot { width:9px; height:9px; border-radius:50%; background:var(--tone, var(--muted)); flex:none; }
.avatar { width:42px; height:42px; flex:none; border-radius:11px; display:grid; place-items:center;
          font-weight:750; font-size:.95rem; color:#fff; background:var(--tone, var(--muted)); }
.avatar.big { width:54px; height:54px; font-size:1.15rem; border-radius:14px; }
.t0 { --tone:#3b6fe0; } .t1 { --tone:#d6407f; } .t2 { --tone:#119b72; } .t3 { --tone:#d98a0b; }
.t4 { --tone:#7b4ee0; } .t5 { --tone:#0f97b5; } .t6 { --tone:#e0553b; } .t7 { --tone:#5f9b1d; }
.hero { display:flex; flex-wrap:wrap; gap:16px 28px; align-items:center; justify-content:space-between;
        background:linear-gradient(135deg, var(--accent-soft), var(--card) 70%); }
.hero h2 { margin:2px 0 4px; font-size:1.35rem; }
.hero .stats { flex:1 1 380px; margin:0; }
.eyebrow { margin:0; font-size:.78rem; text-transform:uppercase; letter-spacing:.07em; color:var(--accent);
           font-weight:700; }
.rows { list-style:none; padding:0; margin:0; }
.rows li { display:flex; gap:12px; align-items:center; padding:10px 0; margin:0;
           border-bottom:1px solid var(--line); }
.rows li:last-child { border-bottom:0; padding-bottom:0; } .rows li:first-child { padding-top:0; }
.rows .grow { flex:1; min-width:0; overflow-wrap:anywhere; }
.rows a { color:var(--text); font-weight:600; text-decoration:none; } .rows a:hover { color:var(--accent); }
.rows small { display:block; color:var(--muted); font-size:.85rem; }
.timeline { list-style:none; padding:0 0 0 14px; margin:0; border-left:2px solid var(--line); }
.timeline li { position:relative; margin:0 0 14px; }
.timeline li::before { content:""; position:absolute; left:-20px; top:.45em; width:10px; height:10px;
                       border-radius:50%; background:var(--tone, var(--accent)); border:2px solid var(--card); }
.timeline small { display:block; color:var(--muted); font-size:.82rem; }
.subject-card { display:flex; flex-direction:column; gap:12px; text-decoration:none; color:inherit;
                border-top:4px solid var(--tone, var(--line)); transition:transform .12s ease; }
.subject-card:hover { transform:translateY(-2px); border-color:var(--tone); }
.subject-card .head { display:flex; gap:12px; align-items:center; }
.subject-card .foot { display:flex; flex-wrap:wrap; gap:6px; }
.sync { display:flex; gap:10px; align-items:flex-start; }
.sync p { margin:0; } .sync .dot { margin-top:.55em; } .sync.ok .dot { background:var(--ok); } .sync.bad .dot { background:var(--bad); }
.more { display:inline-block; margin-top:12px; font-weight:600; text-decoration:none; }
.empty { text-align:center; padding:28px 20px; color:var(--muted); }
.summary-list { display:grid; grid-template-columns:max-content 1fr; gap:8px 18px; margin:0; }
.summary-list dt { color:var(--muted); font-weight:600; } .summary-list dd { margin:0; overflow-wrap:anywhere;
                                                                             white-space:pre-wrap; }
.actions { display:flex; flex-wrap:wrap; gap:10px 16px; align-items:center; margin-top:16px; }
.actions button { margin-top:0; }
.spinner { width:18px; height:18px; border-radius:50%; border:3px solid var(--line); border-top-color:var(--accent);
           display:inline-block; vertical-align:-3px; margin-right:8px; animation:spin 1s linear infinite; }
@keyframes spin { to { transform:rotate(360deg); } }
select, textarea:not(.copiar), input[type=date] {
  width:100%; padding:8px 10px; font:inherit; border:1px solid var(--line); border-radius:9px;
  background:var(--card); color:var(--text); }
textarea:not(.copiar) { resize:vertical; }
button.small { padding:5px 12px; font-size:.86rem; margin-top:0; }
.item-actions { display:flex; flex-wrap:wrap; gap:8px 12px; align-items:flex-start; margin-top:10px; }
.inline-form { margin:0; } .inline-form label { font-size:.88rem; margin:8px 0 3px; }
.inline-form > button:not(.small) { margin-top:10px; }
details.reg { margin-top:6px; } details.reg[open] { flex-basis:100%; }
details.reg summary { cursor:pointer; color:var(--accent); font-weight:600; font-size:.88rem; }
details.reg .inline-form { background:var(--soft); border:1px solid var(--line); border-radius:10px;
                           padding:6px 12px 12px; margin-top:6px; }
.two { display:grid; grid-template-columns:1fr 1fr; gap:0 10px; }
.small { font-size:.82rem; margin:6px 0 0; }
.msg.ok { border-left-color:var(--ok); background:var(--ok-soft); }
.empty strong { display:block; color:var(--text); font-size:1.1rem; margin-bottom:4px; }
.copy-box label { margin-top:4px; }
button.copiar-boton { margin-top:10px; }
textarea.copiar { width:100%; min-height:12em; padding:12px; border:1px solid var(--line); border-radius:10px;
                  background:var(--soft); color:var(--text); resize:vertical;
                  font:13.5px/1.5 ui-monospace, Consolas, "Cascadia Mono", monospace; }
@media (max-width: 560px) { h1 { font-size:1.45rem; } .stats { grid-template-columns:1fr 1fr; } .item { flex-wrap:wrap; } .rel { width:100%; } }
"""


@dataclass
class Response:
    status: int
    body: str = ""
    location: str = None
    shutdown: bool = False


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def badge(state):
    return f'<span class="badge {esc(state)}">{esc(STATES.get(state, state))}</span>'


def csrf_field(csrf):
    return f'<input type="hidden" name="csrf" value="{esc(csrf)}">'


def notice(text, bad=False):
    return f'<div class="card msg{" bad" if bad else ""}" role="status"><p>{esc(text)}</p></div>'


# Único script permitido (server.CSP lo autoriza por su hash): copia el cuadro de su botón al
# portapapeles. El botón empieza oculto; sin el script queda el cuadro para copiar a mano.
COPY_SCRIPT = (
    'document.querySelectorAll("button.copiar-boton").forEach(function (b) {'
    ' var box = document.getElementById(b.dataset.copiar); if (!box) { return; } b.hidden = false;'
    ' b.addEventListener("click", function () {'
    ' var done = function (ok) { b.textContent = ok ? "Copiado ✓" :'
    ' "No se pudo copiar: seleccione el texto y use Ctrl+C"; };'
    ' if (navigator.clipboard && navigator.clipboard.writeText) {'
    ' navigator.clipboard.writeText(box.value).then(function () { done(true); },'
    ' function () { box.select(); done(false); });'
    ' } else { box.select(); done(false); } }); });')
COPY_SCRIPT_HASH = "sha256-" + base64.b64encode(hashlib.sha256(COPY_SCRIPT.encode("utf-8")).digest()).decode()


LOGO = ('<svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="9" fill="#4f46e5"/>'
        '<path d="M16 8 5 13.5 16 19l11-5.5L16 8Z" fill="#fff"/>'
        '<path d="M9.5 16.2v4.6c0 1.6 3 3.2 6.5 3.2s6.5-1.6 6.5-3.2v-4.6L16 19.5l-6.5-3.3Z" fill="#c7c9ff"/></svg>')


def layout(title, body, *, back=True, nav="", subtitle="", icon="", wide=False, head=""):
    """Página completa: barra superior (marca y, en el panel, sus pestañas) y encabezado."""
    link = '<p><a href="/">← Volver al resumen</a></p>' if back else ""
    script = f"<script>{COPY_SCRIPT}</script>" if 'class="copiar-boton"' in body else ""
    sub = f'<p class="sub">{esc(subtitle)}</p>' if subtitle else ""
    main = '<main class="wide">' if wide else "<main>"
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'{head}<title>{esc(title)} · Academic Manager</title><style>{STYLE}</style></head>'
            f'<body><header class="topbar"><div class="topbar-in"><span class="brand">{LOGO}'
            f'Academic Manager</span>{nav}</div></header>'
            f'{main}{link}<div class="page-head">{icon}<div>'
            f'<h1>{esc(title)}</h1>{sub}</div></div>{body}</main>{script}</body></html>')


def message_page(text):
    return layout("Academic Manager", notice(text, bad=True), back=False)
