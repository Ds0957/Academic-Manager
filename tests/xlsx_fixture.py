"""Escritor XLSX mínimo para construir workbooks de prueba de la migración."""
from xml.sax.saxutils import escape
import zipfile

from academic_store.xlsx import MAIN, REL

PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _column_letters(index):
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _cell_xml(ref, value, date_style):
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, (int, float)):
        style = ' s="1"' if date_style else ""
        return f'<c r="{ref}"{style}><v>{repr(float(value)) if isinstance(value, float) else value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def write_sheets(path, sheets, date_columns=None):
    """Escribe {nombre_hoja: [[valor, ...], ...]}; `date_columns` = {hoja: {índices}}."""
    date_columns = date_columns or {}
    names = list(sheets)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as book:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/'
            f'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(names) + 1))
        book.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f'{overrides}</Types>'))
        book.writestr("_rels/.rels", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{PKG_REL}">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'))
        sheet_list = "".join(
            f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, name in enumerate(names, 1))
        book.writestr("xl/workbook.xml", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>{sheet_list}</sheets></workbook>'))
        rels = "".join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            f'relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(names) + 1))
        rels += (f'<Relationship Id="rId{len(names) + 1}" Type="http://schemas.openxmlformats.org/'
                 'officeDocument/2006/relationships/styles" Target="styles.xml"/>')
        book.writestr("xl/_rels/workbook.xml.rels", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{PKG_REL}">{rels}</Relationships>'))
        book.writestr("xl/styles.xml", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="{MAIN}">'
            '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm"/></numFmts>'
            '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
            '</cellXfs></styleSheet>'))
        for i, name in enumerate(names, 1):
            dates = date_columns.get(name, set())
            rows = []
            for r, row in enumerate(sheets[name]):
                cells = "".join(_cell_xml(f"{_column_letters(c)}{r + 1}", v, r > 0 and c in dates)
                                for c, v in enumerate(row))
                if cells:
                    rows.append(f'<row r="{r + 1}">{cells}</row>')
            book.writestr(f"xl/worksheets/sheet{i}.xml", (
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<worksheet xmlns="{MAIN}"><sheetData>{"".join(rows)}</sheetData></worksheet>'))
