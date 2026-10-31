"""HTML del asistente: todo valor dinámico pasa por esc(); sin JavaScript ni recursos externos."""
import html
from dataclasses import dataclass

STATES = {"hecho": "Hecho", "falta": "Falta", "error": "Error"}

STYLE = """
:root { --bg:#f7f7f5; --card:#fff; --text:#1f2328; --muted:#59636e; --line:#d8dee4;
        --ok:#1a7f37; --todo:#9a6700; --bad:#cf222e; --accent:#0969da; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --card:#161b22; --text:#e6edf3; --muted:#9198a1; --line:#30363d;
          --ok:#3fb950; --todo:#d29922; --bad:#f85149; --accent:#4493f8; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font: 17px/1.55 system-ui, "Segoe UI", sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 1.6rem; margin: 0 0 4px; } h2 { font-size: 1.25rem; margin-top: 28px; }
a { color: var(--accent); }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:16px 18px; margin:14px 0; }
.step { display:flex; gap:12px; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; }
.badge { display:inline-block; border-radius:999px; padding:1px 10px; font-size:.85rem;
         font-weight:600; border:1px solid currentColor; }
.hecho { color:var(--ok); } .falta { color:var(--todo); } .error { color:var(--bad); }
.muted { color:var(--muted); }
.msg { border-left:4px solid var(--accent); } .msg.bad { border-left-color:var(--bad); }
label { display:block; margin:10px 0 4px; font-weight:600; }
input[type=text], input[type=password], input[type=datetime-local] {
  width:100%; padding:9px 10px; font:inherit; border:1px solid var(--line);
  border-radius:8px; background:var(--card); color:var(--text); }
button { font:inherit; padding:9px 16px; border-radius:8px; border:1px solid var(--accent);
         background:var(--accent); color:#fff; cursor:pointer; margin-top:12px; }
button.secondary { background:transparent; color:var(--accent); }
ol li, ul li { margin: 6px 0; }
pre { white-space: pre-wrap; background:var(--bg); padding:12px; border-radius:8px; }
table { border-collapse: collapse; width:100%; } td, th { text-align:left; padding:6px 4px;
        border-bottom:1px solid var(--line); vertical-align: top; }
.inline { display:inline; font-weight:normal; margin-right:14px; }
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


def layout(title, body, *, back=True):
    link = '<p><a href="/">← Volver al resumen</a></p>' if back else ""
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{esc(title)} · Academic Manager</title><style>{STYLE}</style></head>'
            f'<body><main>{link}<h1>{esc(title)}</h1>{body}</main></body></html>')


def message_page(text):
    return layout("Academic Manager", notice(text, bad=True), back=False)
