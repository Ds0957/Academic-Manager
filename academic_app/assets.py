"""Scripts fijos del panel, servidos por el propio programa (sin internet ni CDN).

Cada archivo está fijado por su SHA-256: si el archivo instalado no coincide, no se sirve y los
diagramas quedan como texto. Mermaid es la versión 12.2.0 publicada en npm (licencia MIT, ver
static/mermaid.LICENSE); para actualizarlo hay que cambiar el archivo, su ruta y su hash juntos.
"""
import hashlib
from pathlib import Path

from .pages import Response

STATIC_DIR = Path(__file__).resolve().parent / "static"
MERMAID = "/static/mermaid-12.2.0.min.js"
DIAGRAMS = "/static/diagramas.js"
JS = "text/javascript; charset=utf-8"
ASSETS = {
    MERMAID: ("mermaid.min.js", "a8a6a6f805b074a8261a2825c86f99c8054acb3b35f50bb7e24a4f9f2c3c3b9f",
              "private, max-age=31536000, immutable"),  # la ruta cambia con la versión
    DIAGRAMS: ("diagramas.js", "9db4b827aee3614498562f90f849bf398f97c14e518ea67291cb4b3d8ff6d089",
               "private, max-age=3600"),
}
_verified = {}


def serve(path):
    """Response del script pedido, o None si no existe o no coincide con su hash."""
    found = ASSETS.get(path)
    if found is None:
        return None
    filename, digest, cache = found
    data = _verified.get(path)
    if data is None:
        try:
            data = (STATIC_DIR / filename).read_bytes()
        except OSError:
            return None
        if hashlib.sha256(data).hexdigest() != digest:
            return None
        _verified[path] = data
    return Response(200, data, content_type=JS, cache=cache)


def scripts():
    """Etiquetas que cargan Mermaid y dibujan los diagramas de la página."""
    return f'<script src="{MERMAID}"></script><script src="{DIAGRAMS}"></script>'
