"""QR code for pairing the phone, drawn locally as inline SVG (no external service, no scripts)."""


class QRUnavailable(Exception):
    """segno is not installed: the page shows the address as text instead."""


def svg(url):
    """Inline <svg> for url; a regular QR (never Micro QR, which phones do not read)."""
    try:
        import segno
    except ImportError as exc:
        raise QRUnavailable("No se pudo dibujar el código QR en este PC.") from exc
    return segno.make_qr(url, error="m").svg_inline(scale=5, dark="#000", light="#fff")
