"""Esquema SQLite, migración verificable desde database.xlsx y lectura del catálogo.

El catálogo conserva la forma que hoy producen los lectores Excel COM:
{hoja: [ {encabezado: valor} ]}, con números como float y fechas como serial de Excel.
"""
import hashlib
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .xlsx import read_sheets

SCHEMA_VERSION = 1
DB_NAME = "academic-manager.sqlite"
DATA_SHEETS = {
    "01 - Materias": "materias",
    "02 - Actividades": "actividades",
    "03 - Clases": "clases",
    "04 - Recursos": "recursos",
    "05 - Semestres": "semestres",
    "06 - Entregables": "entregables",
}
# El tablero no tiene encabezados: se guarda tal cual, por posición, incluida la fila 1.
RAW_SHEETS = {"00 - Dashboard": "dashboard"}
SHEETS = ("00 - Dashboard", *DATA_SHEETS)
# Columnas booleanas del contrato V2, tipadas aunque la hoja aún no tenga filas.
BOOL_COLUMNS = {("02 - Actividades", "procesada"), ("03 - Clases", "procesada"),
                ("04 - Recursos", "procesado")}
# Fuente única de los estados y tipos de los registros del chat (también la usan
# academic_operations.chat, academic_access y las restricciones CHECK de abajo).
TRACK_STATES = ("pendiente", "en_progreso", "entregada", "calificada")
NOTE_TYPES = ("nota", "decision", "aclaracion", "resumen_clase")
TASK_STATES = ("pendiente", "en_progreso", "hecha", "descartada")


def _sql_values(values):
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


# Tablas propias (no heredan la forma de Excel ni aparecen en `_columnas`/`sheetview`):
# fechas ISO YYYY-MM-DD y marcas de tiempo ISO-8601 UTC.
RECORD_TABLES = {
    "seguimiento": (
        "CREATE TABLE IF NOT EXISTS seguimiento (actividad_id TEXT PRIMARY KEY, "
        f"estado TEXT NOT NULL CHECK (estado IN {_sql_values(TRACK_STATES)}), "
        "nota REAL, puntos REAL, comentario TEXT, actualizado_utc TEXT NOT NULL)"),
    "notas": (
        "CREATE TABLE IF NOT EXISTS notas (nota_id TEXT PRIMARY KEY, materia_id TEXT NOT NULL, "
        f"tipo TEXT NOT NULL CHECK (tipo IN {_sql_values(NOTE_TYPES)}), "
        "texto TEXT NOT NULL, clase_id TEXT, creado_utc TEXT NOT NULL)"),
    "tareas": (
        "CREATE TABLE IF NOT EXISTS tareas (tarea_id TEXT PRIMARY KEY, titulo TEXT NOT NULL, "
        "materia_id TEXT, fecha_limite TEXT, detalle TEXT, "
        f"estado TEXT NOT NULL CHECK (estado IN {_sql_values(TASK_STATES)}), "
        "creado_utc TEXT NOT NULL, actualizado_utc TEXT NOT NULL)"),
}


class StoreError(ValueError):
    pass


def _quote(name):
    return '"' + str(name).replace('"', '""') + '"'


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sheet_records(name, grid):
    """Normaliza una hoja: devuelve (encabezados, [(fila_excel, [valores])], columnas_excel)."""
    if name in RAW_SHEETS:
        width = max((len(row) for row in grid), default=0)
        headers = [f"col_{i + 1}" for i in range(width)]
        rows = [(r + 1, list(row) + [None] * (width - len(row)))
                for r, row in enumerate(grid) if any(v not in (None, "") for v in row)]
        return headers, rows, list(range(1, width + 1))
    if not grid:
        raise StoreError(f"Hoja «{name}» sin encabezados.")
    headers = [str(x or "").strip() for x in grid[0]]
    named = [i for i, h in enumerate(headers) if h]
    if not named or named[0] != 0:
        raise StoreError(f"Hoja «{name}»: la primera columna debe ser el identificador.")
    if len({headers[i] for i in named}) != len(named):
        raise StoreError(f"Hoja «{name}»: encabezados duplicados.")
    rows, seen = [], set()
    for r, row in enumerate(grid[1:], start=2):
        row = list(row) + [None] * (len(headers) - len(row))
        unnamed = [i for i, v in enumerate(row) if v not in (None, "") and not headers[i]]
        if unnamed:
            raise StoreError(f"Hoja «{name}», fila {r}: datos en columna sin encabezado.")
        values = [row[i] for i in named]
        if all(v in (None, "") for v in values):
            continue
        key = values[0]
        if key in (None, ""):
            raise StoreError(f"Hoja «{name}», fila {r}: datos sin identificador.")
        if key in seen:
            raise StoreError(f"Hoja «{name}», fila {r}: identificador duplicado «{key}».")
        seen.add(key)
        rows.append((r, values))
    return [headers[i] for i in named], rows, [i + 1 for i in named]


def _table(name):
    return DATA_SHEETS.get(name) or RAW_SHEETS[name]


def _column_types(name, headers, rows):
    # SQLite no distingue bool de int: una columna es booleana si está declarada como tal en V2
    # o si todos sus valores lo son.
    types = []
    for i, header in enumerate(headers):
        values = [values[i] for _, values in rows if values[i] not in (None, "")]
        bools = sum(isinstance(v, bool) for v in values)
        declared = (name, header) in BOOL_COLUMNS
        if (bools or declared) and bools != len(values):
            raise StoreError(f"Hoja «{name}», columna «{header}»: mezcla booleanos con otros valores.")
        types.append("bool" if bools or declared else None)
    return types


def ensure_records(conn):
    """Crea las tablas propias si faltan (bases migradas antes de existir)."""
    for ddl in RECORD_TABLES.values():
        conn.execute(ddl)


def _create(conn, name, headers, types, excel_columns):
    table = _table(name)
    columns = ", ".join(_quote(h) for h in headers)
    unique = "" if name in RAW_SHEETS else f", UNIQUE ({_quote(headers[0])})"
    not_null = "" if name in RAW_SHEETS else f", CHECK ({_quote(headers[0])} IS NOT NULL)"
    conn.execute(f"CREATE TABLE {_quote(table)} (_fila INTEGER NOT NULL, {columns}{unique}{not_null})")
    conn.executemany(
        "INSERT INTO _columnas (tabla, hoja, posicion, nombre, tipo, columna) VALUES (?, ?, ?, ?, ?, ?)",
        [(table, name, i, h, t, c) for i, (h, t, c) in enumerate(zip(headers, types, excel_columns))])


def _insert(conn, name, headers, rows):
    marks = ", ".join("?" for _ in range(len(headers) + 1))
    columns = ", ".join(_quote(h) for h in headers)
    conn.executemany(f"INSERT INTO {_quote(_table(name))} (_fila, {columns}) VALUES ({marks})",
                     [(fila, *[None if v == "" else v for v in values]) for fila, values in rows])


@contextmanager
def _reading(path):
    """Conexión de solo lectura; los errores de SQLite se convierten en StoreError."""
    path = Path(path)
    if not path.is_file():
        raise StoreError(f"No existe la base SQLite: {path}")
    conn = None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        yield conn
    except sqlite3.Error as exc:
        raise StoreError(f"Base SQLite ilegible o sin el esquema esperado: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()


def migrate(xlsx_path, db_path):
    """Crea una base nueva desde el workbook y la verifica antes de dejarla en su sitio."""
    xlsx_path, db_path = Path(xlsx_path), Path(db_path)
    if db_path.exists():
        raise StoreError(f"La base ya existe; no se sobrescribe: {db_path}")
    grids = read_sheets(xlsx_path)
    return _build(grids, db_path, [("source_sha256", _sha256(xlsx_path))])


def create_empty(db_path, grids):
    """Base nueva con la estructura de las hojas (encabezados) y ninguna fila de datos.

    Usa la misma construcción y verificación que `migrate`; `grids` trae solo encabezados.
    """
    db_path = Path(db_path)
    if db_path.exists():
        raise StoreError(f"La base ya existe; no se sobrescribe: {db_path}")
    if any(len(grids.get(name) or []) > 1 for name in DATA_SHEETS):
        raise StoreError("Una base vacía solo lleva encabezados.")
    return _build(grids, db_path, [("origen", "vacia")])


def _build(grids, db_path, extra_meta):
    missing = [s for s in SHEETS if s not in grids]
    if missing:
        raise StoreError("Faltan hojas en el workbook: " + ", ".join(missing))
    extra = [s for s in grids if s not in SHEETS]
    if extra:
        raise StoreError("Hojas no reconocidas en el workbook: " + ", ".join(extra))
    tmp = db_path.with_name(f"{db_path.name}.tmp-{os.getpid()}")
    tmp.unlink(missing_ok=True)
    try:
        conn = sqlite3.connect(tmp)
        try:
            with conn:
                conn.execute("CREATE TABLE _meta (clave TEXT PRIMARY KEY, valor TEXT)")
                conn.execute("CREATE TABLE _columnas (tabla TEXT NOT NULL, hoja TEXT NOT NULL, "
                             "posicion INTEGER NOT NULL, nombre TEXT NOT NULL, tipo TEXT, "
                             "columna INTEGER NOT NULL, "
                             "PRIMARY KEY (tabla, posicion))")
                for name in SHEETS:
                    headers, rows, columns = sheet_records(name, grids[name])
                    _create(conn, name, headers, _column_types(name, headers, rows), columns)
                    _insert(conn, name, headers, rows)
                ensure_records(conn)
                conn.executemany("INSERT INTO _meta (clave, valor) VALUES (?, ?)", [
                    ("schema_version", str(SCHEMA_VERSION)),
                    ("revision", "1"),
                    *extra_meta,
                    ("migrated_at_utc", datetime.now(timezone.utc).isoformat(timespec="seconds")),
                ])
        finally:
            conn.close()
        report = _compare(grids, tmp)
        if not report["ok"]:
            raise StoreError("La verificación de la migración falló: " + "; ".join(
                f"{s}: {d}" for s, r in report["sheets"].items() for d in r["differences"][:3]))
        os.replace(tmp, db_path)
    finally:
        tmp.unlink(missing_ok=True)
    report["db"] = str(db_path)
    return report


def read_catalog(db_path, sheets=None):
    """Devuelve {hoja: [dict]} en el orden original de filas y columnas."""
    sheets = tuple(sheets or DATA_SHEETS)
    with _reading(db_path) as conn:
        catalog = {}
        for name in sheets:
            if name not in DATA_SHEETS and name not in RAW_SHEETS:
                raise StoreError(f"Hoja desconocida: {name}")
            table = _table(name)
            described = conn.execute(
                "SELECT nombre, tipo FROM _columnas WHERE tabla = ? ORDER BY posicion", (table,)).fetchall()
            headers = [h for h, _ in described]
            bools = [t == "bool" for _, t in described]
            columns = ", ".join(_quote(h) for h in headers)
            catalog[name] = [
                {h: bool(v) if is_bool and v is not None else v
                 for h, v, is_bool in zip(headers, row, bools)}
                for row in conn.execute(f"SELECT {columns} FROM {_quote(table)} ORDER BY _fila")]
        return catalog


def _same(current, before):
    # Misma regla que el escritor Excel anterior: vacío equivale a None y los números se comparan por valor.
    if current is None and before in (None, ""):
        return True
    if isinstance(current, bool) or isinstance(before, bool):
        return type(current) is type(before) and current == before
    return current == before


def _stored(value, kind, where):
    if value == "":
        value = None
    if value is None:
        return None
    if kind == "bool":
        if not isinstance(value, bool):
            raise StoreError(f"{where}: la columna booleana solo admite verdadero/falso.")
        return int(value)
    if isinstance(value, bool):
        raise StoreError(f"{where}: booleano en una columna que no es booleana.")
    if isinstance(value, int):
        return float(value)  # forma Value2: todo número es float
    if isinstance(value, str) and value.startswith("="):
        raise StoreError(f"{where}: no se admiten fórmulas.")
    if not isinstance(value, (str, float)):
        raise StoreError(f"{where}: tipo de valor no soportado.")
    return value


def apply_cells(db_path, changes):
    """Aplica diffs (hoja, fila, columna_excel, antes, después) en una sola transacción.

    Cada `antes` debe coincidir con el valor guardado; si no, no se aplica nada.
    """
    if not changes:
        return revision(db_path)
    conn = sqlite3.connect(Path(db_path))
    try:
        with conn:
            apply_cells_in(conn, changes)
            bump_revision(conn)
    except sqlite3.Error as exc:
        raise StoreError(f"No se pudo escribir la base SQLite: {exc}") from exc
    finally:
        conn.close()
    return revision(db_path)


def bump_revision(conn):
    conn.execute("UPDATE _meta SET valor = CAST(valor AS INTEGER) + 1 WHERE clave = 'revision'")


def apply_cells_in(conn, changes):
    """Cuerpo de `apply_cells` dentro de una transacción abierta por quien llama."""
    layout, inserts = {}, {}
    for name, row, column, before, after in changes:
        if name not in DATA_SHEETS:
            raise StoreError(f"Hoja no editable: {name}")
        table = _table(name)
        if table not in layout:
            layout[table] = {c: (n, t) for n, t, c in conn.execute(
                "SELECT nombre, tipo, columna FROM _columnas WHERE tabla = ?", (table,))}
        where = f"«{name}» fila {row}, columna {column}"
        if column not in layout[table]:
            raise StoreError(f"{where}: columna desconocida.")
        header, kind = layout[table][column]
        if row == 1:
            if after != header:
                raise StoreError(f"{where}: los encabezados no se modifican desde un diff.")
            continue
        value = _stored(after, kind, where)
        found = conn.execute(f"SELECT {_quote(header)} FROM {_quote(table)} WHERE _fila = ?",
                             (row,)).fetchone()
        if found is None:
            if before not in (None, ""):
                raise StoreError(f"{where}: el registro cambió desde la previsualización.")
            inserts.setdefault((table, row), {})[header] = value
            continue
        current = bool(found[0]) if kind == "bool" and found[0] is not None else found[0]
        if not _same(current, before):
            raise StoreError(f"{where}: el registro cambió desde la previsualización.")
        conn.execute(f"UPDATE {_quote(table)} SET {_quote(header)} = ? WHERE _fila = ?",
                     (value, row))
    for (table, row), values in inserts.items():
        key = conn.execute("SELECT nombre FROM _columnas WHERE tabla = ? AND posicion = 0",
                           (table,)).fetchone()[0]
        if values.get(key) is None:
            raise StoreError(f"Tabla {table}, fila {row}: alta sin identificador.")
        columns = ", ".join(_quote(h) for h in values)
        marks = ", ".join("?" for _ in range(len(values) + 1))
        try:
            conn.execute(f"INSERT INTO {_quote(table)} (_fila, {columns}) VALUES ({marks})",
                         (row, *values.values()))
        except sqlite3.IntegrityError as exc:
            raise StoreError(f"Tabla {table}, fila {row}: identificador duplicado.") from exc


def validate(db_path):
    """Comprueba que el archivo es una base Academic Manager íntegra y con el esquema esperado."""
    try:
        meta = info(db_path)
    except (StoreError, KeyError, ValueError) as exc:
        raise StoreError(f"El archivo no tiene el esquema de Academic Manager: {exc}") from exc
    if meta["schema_version"] != SCHEMA_VERSION:
        raise StoreError(f"Versión de esquema no soportada: {meta['schema_version']}.")
    with _reading(db_path) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise StoreError("La base SQLite no supera la comprobación de integridad.")
        tables = {t for (t,) in conn.execute("SELECT DISTINCT tabla FROM _columnas")}
    missing = {_table(s) for s in SHEETS} - tables
    if missing:
        raise StoreError("Faltan tablas en el esquema: " + ", ".join(sorted(missing)))
    return meta


def column_map(db_path, sheet):
    """{encabezado: columna de Excel (1..n)} de una hoja."""
    with _reading(db_path) as conn:
        return dict(conn.execute("SELECT nombre, columna FROM _columnas WHERE tabla = ?",
                                 (_table(sheet),)))


def rows_in_use(db_path, sheet):
    """Filas de Excel ocupadas por registros de la hoja."""
    with _reading(db_path) as conn:
        return {fila for (fila,) in conn.execute(f"SELECT _fila FROM {_quote(_table(sheet))}")}


def headers(db_path, sheet):
    """Encabezados de una hoja en su orden original, aunque la hoja no tenga filas."""
    with _reading(db_path) as conn:
        return [h for (h,) in conn.execute(
            "SELECT nombre FROM _columnas WHERE tabla = ? ORDER BY posicion", (_table(sheet),))]


def info(db_path):
    with _reading(db_path) as conn:
        meta = dict(conn.execute("SELECT clave, valor FROM _meta"))
        counts = {name: conn.execute(f"SELECT COUNT(*) FROM {_quote(_table(name))}").fetchone()[0]
                  for name in SHEETS}
    return {"schema_version": int(meta["schema_version"]), "revision": int(meta["revision"]),
            "source_sha256": meta.get("source_sha256"), "migrated_at_utc": meta.get("migrated_at_utc"),
            "rows": counts}


def revision(db_path):
    return info(db_path)["revision"]


def verify(xlsx_path, db_path):
    """Compara hoja por hoja (filas, encabezados y valores) el workbook con la base."""
    return _compare(read_sheets(xlsx_path), db_path)


def _compare(grids, db_path):
    stored = read_catalog(db_path, SHEETS)
    report = {"ok": True, "sheets": {}}
    for name in SHEETS:
        headers, rows, _ = sheet_records(name, grids.get(name, []))
        expected = [dict(zip(headers, [None if v == "" else v for v in values]))
                    for _, values in rows]
        actual = stored[name]
        differences = []
        if len(expected) != len(actual):
            differences.append(f"filas esperadas {len(expected)}, guardadas {len(actual)}")
        for i, (want, got) in enumerate(zip(expected, actual)):
            if list(want) != list(got):
                differences.append(f"registro {i + 1}: encabezados distintos")
                continue
            for key in want:
                if want[key] != got[key] or type(want[key]) is not type(got[key]):
                    differences.append(f"registro {i + 1}, «{key}»: {want[key]!r} ≠ {got[key]!r}")
        report["sheets"][name] = {"rows": len(actual), "ok": not differences,
                                  "differences": differences}
        report["ok"] = report["ok"] and not differences
    return report
