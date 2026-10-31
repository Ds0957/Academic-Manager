"""Lectura XLSX mínima con la biblioteca estándar, solo para migrar database.xlsx.

`read_sheets` devuelve los valores como `UsedRange.Value2`: números como float,
texto como str, booleanos como bool, celdas vacías como None. Las fechas quedan como serial de Excel.
"""
import re
import zipfile
from xml.etree import ElementTree as ET

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"m": MAIN, "r": REL}
_REF = re.compile(r"^([A-Z]+)(\d+)$")


class XlsxError(ValueError):
    pass


def _column_index(letters):
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    return value - 1



def _text(node):
    # Texto de <si>/<is>: concatena <t> directos y de cada <r>, ignorando la fonética <rPh>.
    parts = [t.text or "" for t in node.findall("m:t", NS)]
    parts += [t.text or "" for t in node.findall("m:r/m:t", NS)]
    return "".join(parts)


def _cell_value(cell, shared):
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        node = cell.find("m:is", NS)
        return _text(node) if node is not None else None
    raw = cell.find("m:v", NS)
    if raw is None or raw.text is None:
        return None
    if kind == "s":
        return shared[int(raw.text)]
    if kind in ("str", "d"):
        return raw.text
    if kind == "n":
        return float(raw.text)
    if kind == "b":
        return raw.text == "1"
    raise XlsxError(f"Celda {cell.get('r')}: valor de error o tipo «{kind}» no soportado.")


def _sheet_targets(book):
    workbook = ET.fromstring(book.read("xl/workbook.xml"))
    rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}
    result = []
    for sheet in workbook.find("m:sheets", NS):
        target = targets[sheet.get(f"{{{REL}}}id")].lstrip("/")
        result.append((sheet.get("name"), target if target.startswith("xl/") else "xl/" + target))
    return result


def read_sheets(path):
    """Devuelve {nombre_hoja: [[valor, ...], ...]} como una cuadrícula desde A1."""
    with zipfile.ZipFile(path) as book:
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            shared = [_text(si) for si in root.findall("m:si", NS)]
        sheets = {}
        for name, target in _sheet_targets(book):
            cells = {}
            for cell in ET.fromstring(book.read(target)).iter(f"{{{MAIN}}}c"):
                match = _REF.match(cell.get("r") or "")
                if not match:
                    raise XlsxError(f"Hoja «{name}»: celda sin referencia.")
                value = _cell_value(cell, shared)
                if value is not None:
                    cells[(int(match.group(2)) - 1, _column_index(match.group(1)))] = value
            height = max((r for r, _ in cells), default=-1) + 1
            width = max((c for _, c in cells), default=-1) + 1
            sheets[name] = [[cells.get((r, c)) for c in range(width)] for r in range(height)]
        return sheets
