"""Asistente de configuración de Academic Manager en el navegador (solo este PC)."""
from pathlib import Path

VERSION_FILE = Path(__file__).with_name("VERSION")  # única fuente: la lee también el instalador


def version():
    return VERSION_FILE.read_text(encoding="utf-8").strip()
