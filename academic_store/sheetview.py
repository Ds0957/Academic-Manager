"""Vista de solo lectura de la base con la interfaz COM que usan los planificadores V2.

Reproduce `Worksheets.Item(hoja)`, `UsedRange.Value2` y `Cells.Item(fila, columna)` con
filas y columnas de Excel, para que los planificadores conserven su validación intacta.
"""
from .store import DATA_SHEETS, RAW_SHEETS, StoreError, _reading, _quote, _table


class _Cell:
    def __init__(self, value):
        self.Value = self.Value2 = value


class _Cells:
    def __init__(self, grid):
        self._grid = grid

    def Item(self, row, column):
        if row < 1 or column < 1 or row > len(self._grid):
            return _Cell(None)
        line = self._grid[row - 1]
        return _Cell(line[column - 1] if column <= len(line) else None)


class _UsedRange:
    def __init__(self, grid):
        self.Value2 = tuple(tuple(line) for line in grid)


class _Sheet:
    def __init__(self, name, grid):
        self.Name = name
        self.Cells = _Cells(grid)
        self.UsedRange = _UsedRange(grid)


class _Worksheets:
    def __init__(self, sheets):
        self._sheets = sheets
        self.Count = len(sheets)

    def Item(self, key):
        if isinstance(key, int):
            return list(self._sheets.values())[key - 1]
        if key not in self._sheets:
            raise StoreError(f"Hoja desconocida: {key}")
        return self._sheets[key]


class _Book:
    def __init__(self, sheets):
        self.Worksheets = _Worksheets(sheets)

    def Close(self, save=False):
        if save:
            raise StoreError("La vista de la base es de solo lectura.")


class _App:
    def Quit(self):
        pass


def _grid(conn, name):
    table = _table(name)
    columns = conn.execute(
        "SELECT nombre, tipo, columna FROM _columnas WHERE tabla = ? ORDER BY posicion",
        (table,)).fetchall()
    width = max((c for _, _, c in columns), default=0)
    header = [None] * width
    if name in DATA_SHEETS:
        for header_name, _, column in columns:
            header[column - 1] = header_name
    rows = {}
    names = ", ".join(_quote(n) for n, _, _ in columns)
    for fila, *values in conn.execute(f"SELECT _fila, {names} FROM {_quote(table)} ORDER BY _fila"):
        line = [None] * width
        for (_, kind, column), value in zip(columns, values):
            line[column - 1] = bool(value) if kind == "bool" and value is not None else value
        rows[fila] = line
    height = max(rows, default=1)
    grid = [rows.get(r, [None] * width) for r in range(1, height + 1)]
    if name in DATA_SHEETS:
        grid[0] = header
    return grid


def open_book(db_path):
    """Devuelve (app, book) con la forma que esperan los planificadores V2."""
    with _reading(db_path) as conn:
        sheets = {name: _Sheet(name, _grid(conn, name))
                  for name in ("00 - Dashboard", *DATA_SHEETS) if name in RAW_SHEETS or name in DATA_SHEETS}
    return _App(), _Book(sheets)
