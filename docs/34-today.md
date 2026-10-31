# `today`: qué hacer primero

`today` responde a la pregunta «¿qué debería hacer primero?» con una lista ordenada y determinista, calculada a partir de la base publicada. Es una consulta de solo lectura: no escribe, no sincroniza Canvas ni Drive y no recomienda nada que no esté en los datos.

## Uso

```powershell
scripts\academic-read.cmd today
scripts\academic-read.cmd today --dias 7
scripts\academic-read.cmd today --fecha 2027-03-10 --dias 30
```

| Opción | Valor | Por defecto |
|---|---|---|
| `--fecha` | Fecha de referencia `YYYY-MM-DD` | Hoy en `America/Bogota` |
| `--dias` | Horizonte de próximas entregas y tareas, entero de 1 a 60 | 14 |

Una fecha mal formada o inexistente, o `--dias` fuera de 1–60, devuelve `INVALID_ARGUMENT`. `--fecha` y `--dias` solo valen en `today`; en otras consultas también son `INVALID_ARGUMENT`.

La consulta exige que la base local coincida con el hash publicado (`workbook_version`), no el índice de conocimiento: nunca devuelve `INDEX_STALE`. Si la base no coincide, devuelve `SOURCE_UNAVAILABLE`. Si no hay un único semestre activo, devuelve `INVALID_SOURCE`.

Solo cuenta el **semestre activo**: actividades, clases y tareas de materias de semestres cerrados no aparecen (siguen guardadas; simplemente no son pendientes). Las tareas personales sin materia siempre cuentan.

## Qué entra en la lista

Cada elemento tiene `tipo`, `prioridad` (0 = lo más urgente), `motivo` (razón breve), `fecha` (ISO `YYYY-MM-DD` o `null`), `materia_id`, `titulo` y el identificador que corresponda (`actividad_id`, `tarea_id` o `clase_id`). En `materia_descuidada`, el identificador es `materia_id` y `titulo` es el nombre de la materia.

| Prioridad | `tipo` | Regla |
|---|---|---|
| 0 | `actividad_vencida` | Fecha de entrega anterior a la fecha de referencia, sin hacer, y sin `fecha_cierre` o con `fecha_cierre` igual o posterior a la fecha de referencia |
| 0 | `tarea` | Tarea personal `pendiente` o `en_progreso` con fecha límite ya pasada |
| 1 | `actividad_proxima`, `tarea` | Vence dentro de 0–2 días |
| 2 | `actividad_proxima`, `tarea` | Vence dentro de 3–7 días |
| 3 | `actividad_proxima`, `tarea` | Vence después de 7 días y dentro de `--dias` |
| 4 | `clase_sin_procesar` | Clase con `procesada` falso y fecha dentro de los últimos 21 días |
| 5 | `materia_descuidada` | Materia del semestre activo sin clases, notas ni seguimiento en los últimos 14 días; `fecha` es el último movimiento (o `null` si no hay ninguno) |
| 6 | `tarea_sin_fecha` | Tarea personal `pendiente` o `en_progreso` sin fecha límite |

Lo que vence después de `--dias` no aparece.

Una actividad está **hecha** si su `seguimiento.estado` es `entregada` o `calificada`, o si Canvas Sync registró `estado_entrega = entregada` o `estado_calificacion = calificada`. Las actividades hechas, las tareas `hecha` o `descartada` y las actividades sin fecha de entrega no aparecen.

## Orden

1. `prioridad` ascendente.
2. `fecha` ascendente; `null` al final.
3. Identificador ascendente.

La misma base y los mismos parámetros siempre producen la misma respuesta.

## Fechas

Las fechas de las hojas (`fecha_entrega`, `fecha_cierre`, `fecha` de clase) son seriales de Excel en hora local de Bogotá y se comparan como fecha local: una entrega a las 23:59 cuenta para ese día. Las marcas de tiempo UTC de `notas` y `seguimiento` se convierten a fecha local de Bogotá antes de compararlas.

## Respuesta

`data` incluye la `fecha` y los `dias` usados, `resumen` con el número de elementos por `tipo`, `items` y `workbook_sha256`.

```json
{
  "ok": true,
  "schema_version": 1,
  "action": "today",
  "data": {
    "fecha": "2027-03-10",
    "dias": 14,
    "fechas_zona": "America/Bogota",
    "resumen": {"actividad_vencida": 1, "actividad_proxima": 1, "tarea": 0,
                "clase_sin_procesar": 1, "materia_descuidada": 1, "tarea_sin_fecha": 1},
    "items": [
      {"tipo": "actividad_vencida", "prioridad": 0,
       "motivo": "Venció hace 2 días y aún no consta como entregada",
       "fecha": "2027-03-08", "materia_id": "MAT-SO2", "actividad_id": "ACT-SO2-004",
       "titulo": "Taller 3"},
      {"tipo": "actividad_proxima", "prioridad": 1, "motivo": "Se entrega mañana",
       "fecha": "2027-03-11", "materia_id": "MAT-SER", "actividad_id": "ACT-SER-010",
       "titulo": "Foro 2"},
      {"tipo": "clase_sin_procesar", "prioridad": 4, "motivo": "Clase hace 1 día sin procesar",
       "fecha": "2027-03-09", "materia_id": "MAT-SER", "clase_id": "CLS-SER-20270309-01",
       "titulo": "Clase 5"},
      {"tipo": "materia_descuidada", "prioridad": 5,
       "motivo": "Sin clases, notas ni seguimiento en los últimos 14 días",
       "fecha": "2027-02-23", "materia_id": "MAT-ING3", "titulo": "Inglés III"},
      {"tipo": "tarea_sin_fecha", "prioridad": 6, "motivo": "Tarea abierta sin fecha límite",
       "fecha": null, "materia_id": null, "tarea_id": "TAR-0001", "titulo": "Repasar apuntes"}
    ],
    "workbook_sha256": "…"
  }
}
```

Los textos de `motivo` sirven de apoyo para leer la lista; para decidir, use `tipo`, `prioridad` y `fecha`.
