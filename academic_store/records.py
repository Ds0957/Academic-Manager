"""Tablas propias escritas desde el chat: `seguimiento`, `notas`, `tareas`, `mapas` e `indices_clase`.

Son tablas limpias (fechas ISO, marcas de tiempo ISO-8601 UTC, ids de texto) y no forman
parte de la vista por hojas. Cada lote se aplica en una sola transacción que incrementa
`_meta.revision`, igual que `store.apply_cells`.
"""
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .store import NOTE_TYPES, TASK_STATES, TRACK_STATES  # noqa: F401  (fuente única)

KEYS = {"seguimiento": "actividad_id", "notas": "nota_id", "tareas": "tarea_id", "mapas": "materia_id",
        "indices_clase": "clase_id"}
FIELDS = {
    "seguimiento": ("actividad_id", "estado", "nota", "puntos", "comentario"),
    "notas": ("nota_id", "materia_id", "tipo", "texto", "clase_id"),
    "tareas": ("tarea_id", "titulo", "materia_id", "fecha_limite", "detalle", "estado"),
    "mapas": ("materia_id", "contenido"),
    "indices_clase": ("clase_id", "materia_id", "contenido"),
}


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read(db_path, table):
    """Registros de una tabla propia como dicts; una base anterior sin la tabla devuelve []."""
    if table not in KEYS:
        raise store.StoreError(f"Tabla propia desconocida: {table}")
    with store._reading(db_path) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                              (table,)).fetchone()
        if not exists:
            return []
        cursor = conn.execute(f"SELECT * FROM {table} ORDER BY {KEYS[table]}")
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor]


def find(db_path, table, key):
    return next((r for r in read(db_path, table) if r[KEYS[table]] == key), None)


def _value(value, where):
    if value is None or isinstance(value, str) and not value.startswith("="):
        return value
    if isinstance(value, str):
        raise store.StoreError(f"{where}: no se admiten fórmulas.")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    raise store.StoreError(f"{where}: tipo de valor no soportado.")


def _record(table, record, fields):
    unknown = set(record) - set(fields)
    if unknown or KEYS[table] not in record:
        raise store.StoreError(f"Tabla {table}: campos no válidos.")
    return {k: _value(record[k], f"{table}.{k}") for k in fields if k in record}


def apply(db_path, changes):
    """Aplica en una transacción una lista de cambios del planificador de chat.

    Cambios admitidos (dicts):
      {"tabla": "clases", "accion": "celdas", "celdas": [[hoja, fila, columna, antes, después], ...]}
      {"tabla": "notas"|"tareas", "accion": "insertar", "registro": {...}}
      {"tabla": "seguimiento"|"mapas"|"indices_clase", "accion": "guardar", "registro": {...}}
      {"tabla": "tareas", "accion": "estado", "registro": {"tarea_id": ..., "estado": ...}}
    """
    if not changes:
        return store.revision(db_path)
    stamp = now_utc()
    conn = sqlite3.connect(Path(db_path))
    try:
        with conn:
            store.ensure_records(conn)
            for change in changes:
                table, action = change.get("tabla"), change.get("accion")
                if table == "clases" and action == "celdas":
                    store.apply_cells_in(conn, [tuple(c) for c in change["celdas"]])
                elif action == "insertar" and table in ("notas", "tareas"):
                    row = _record(table, change["registro"], FIELDS[table])
                    row["creado_utc"] = stamp
                    if table == "tareas":
                        row["actualizado_utc"] = stamp
                    try:
                        conn.execute(f"INSERT INTO {table} ({', '.join(row)}) "
                                     f"VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
                    except sqlite3.IntegrityError as exc:
                        raise store.StoreError(f"Tabla {table}: identificador duplicado.") from exc
                elif table in ("seguimiento", "mapas", "indices_clase") and action == "guardar":
                    row = _record(table, change["registro"], FIELDS[table])
                    row["actualizado_utc"] = stamp
                    key = KEYS[table]
                    updates = ", ".join(f"{k} = excluded.{k}" for k in row if k != key)
                    conn.execute(f"INSERT INTO {table} ({', '.join(row)}) "
                                 f"VALUES ({', '.join('?' for _ in row)}) "
                                 f"ON CONFLICT ({key}) DO UPDATE SET {updates}",
                                 tuple(row.values()))
                elif table == "tareas" and action == "estado":
                    row = _record(table, change["registro"], ("tarea_id", "estado"))
                    updated = conn.execute(
                        "UPDATE tareas SET estado = ?, actualizado_utc = ? WHERE tarea_id = ?",
                        (row.get("estado"), stamp, row["tarea_id"])).rowcount
                    if updated != 1:
                        raise store.StoreError("La tarea no existe en la base.")
                else:
                    raise store.StoreError("Cambio de chat no reconocido.")
            store.bump_revision(conn)
    except sqlite3.Error as exc:
        raise store.StoreError(f"No se pudo escribir la base SQLite: {exc}") from exc
    finally:
        conn.close()
    return store.revision(db_path)
