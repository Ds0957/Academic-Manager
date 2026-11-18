"""Grabaciones de clase con su fecha en el contenido de Canvas (odd/tasks/clases-automaticas.md, S1).

Los profesores publican las grabaciones de dos formas que se leen igual: una fecha y, debajo,
el enlace de Google Drive («1 de octubre» + enlace, en un foro), o un bloque con «Date: 5th of
August» cuyo enlace está detrás de un texto («Session 0», en una tarea). Cada enlace toma la
última fecha escrita antes que él. Solo lectura: no consulta Canvas ni Drive; decidir si el
archivo es un video y si se puede descargar le toca a quien use el resultado.
"""
import re
from datetime import date, datetime, timedelta

MONTHS = {name: n for n, names in enumerate((
    ("enero", "january", "jan"), ("febrero", "february", "feb"), ("marzo", "march", "mar"),
    ("abril", "april", "apr"), ("mayo", "may"), ("junio", "june", "jun"), ("julio", "july", "jul"),
    ("agosto", "august", "aug"), ("septiembre", "setiembre", "september", "sep", "sept"),
    ("octubre", "october", "oct"), ("noviembre", "november", "nov"), ("diciembre", "december", "dec")), 1)
    for name in names}
MONTH = "(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + ")"
DAY_FIRST = re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s+(?:de\s+|of\s+)?" + MONTH + r"\b", re.I)
MONTH_FIRST = re.compile(r"\b" + MONTH + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?!\d)", re.I)
ISO = re.compile(r"\b(20\d{2})[-/](\d{2})[-/](\d{2})\b")
NUMERIC = re.compile(r"(?<!\d)(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(20\d{2})(?!\d)")  # 21 - 10 - 2026
LABEL = re.compile(r"^\s*(?:date|fecha)\b", re.I)  # «Date: …» que no se entiende no hereda la fecha anterior
DRIVE = re.compile(r"https://(?:drive|docs)\.google\.com/(?:file/d/|open\?id=)([-\w]{20,})")
FUTURE_DAYS = 7  # una fecha más adelante que esto respecto a la sincronización es del año anterior


def _date(line, synced):
    """La primera fecha de la línea, con el año que corresponde a la fecha de sincronización."""
    found = ISO.search(line)
    if found:
        try:
            return date(*(int(x) for x in found.groups()))
        except ValueError:
            return None
    found = NUMERIC.search(line)
    if found:
        try:
            return date(int(found.group(3)), int(found.group(2)), int(found.group(1)))
        except ValueError:
            return None
    for pattern, day_group, month_group in ((DAY_FIRST, 1, 2), (MONTH_FIRST, 2, 1)):
        found = pattern.search(line)
        if found:
            month, day = MONTHS[found.group(month_group).lower()], int(found.group(day_group))
            for year in (synced.year, synced.year - 1):
                try:
                    candidate = date(year, month, day)
                except ValueError:
                    return None
                if candidate <= synced + timedelta(days=FUTURE_DAYS):
                    return candidate
            return None
    return None


def find(content, synced):
    """[{file_id, enlace, fecha}] de un contenido de Canvas ({text, links, anchors?})."""
    if not isinstance(content, dict) or not isinstance(content.get("text"), str):
        return []
    lines = content["text"].split("\n")
    dates, current = [], None
    for line in lines:
        written = _date(line, synced)
        current = written if written or LABEL.match(line) else current
        dates.append(current)
    pairs = []
    for index, line in enumerate(lines):
        pairs += [(index, match.group(1)) for match in DRIVE.finditer(line)]
    cursor = 0
    for anchor in content.get("anchors") or []:
        match = DRIVE.match(str(anchor.get("url") or ""))
        text = str(anchor.get("text") or "").strip()
        if not match or not text:
            continue
        word = re.compile(r"(?<!\w)" + re.escape(text) + r"(?!\w)")
        at = next((i for i in range(cursor, len(lines)) if word.search(lines[i])), None)
        if at is not None:
            pairs.append((at, match.group(1)))
            cursor = at + 1
    found, seen = [], set()
    for index, file_id in sorted(pairs):
        if file_id in seen or dates[index] is None:
            continue
        seen.add(file_id)
        found.append({"file_id": file_id, "enlace": f"https://drive.google.com/file/d/{file_id}/view",
                      "fecha": dates[index].isoformat()})
    return found


def _synced(snapshot):
    try:
        return datetime.strptime(str(snapshot.get("captured_at_utc"))[:10], "%Y-%m-%d").date()
    except ValueError:
        return date.today()


def from_snapshot(snapshot):
    """Grabaciones de una instantánea de curso (`v01/<curso>/latest.json`), cada una con su fuente."""
    data = snapshot.get("data") if isinstance(snapshot, dict) else None
    data = data if isinstance(data, dict) else {}
    sections = data.get("sections") if isinstance(data.get("sections"), dict) else {}

    def rows(value):  # una sección que falló en Canvas se guarda como texto: no tiene grabaciones
        if isinstance(value, dict):  # las secciones se guardan como {"status", "data": [...]}
            value = value.get("data")
        return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []

    items = [(a.get("name"), a.get("instructions")) for a in rows(data.get("assignments"))]
    for key in ("discussion_topics", "announcements", "page_details"):
        items += [(i.get("title"), i.get("instructions")) for i in rows(sections.get(key))]
    synced, found, seen = _synced(snapshot), [], set()
    for source, content in items:
        for item in find(content, synced):
            if item["file_id"] not in seen:
                seen.add(item["file_id"])
                found.append({**item, "fuente": source})
    return found
