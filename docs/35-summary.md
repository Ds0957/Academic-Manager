# 35 — `summary`: resumen funcional para empezar un chat

**Estado:** implementado y probado localmente (WSL); uso real desde ChatGPT pendiente de validar en el PC con Windows.

## Para qué sirve

`summary` sustituye la lectura de `docs/` al empezar un chat normal de ChatGPT. Devuelve, en un solo JSON corto y determinista, el estado actual generado desde los datos reales de la base publicada: semestre y materias activas, lo más urgente, lo que se ha registrado hace poco y el estado del sistema. No hay narrativa mantenida a mano.

Es solo lectura: no publica, no sincroniza Canvas, no reconstruye el índice y no descifra el token de Canvas.

## Uso

```powershell
scripts\academic-read.cmd summary
scripts\academic-read.cmd summary --fecha 2027-03-10
```

- `--fecha YYYY-MM-DD` es opcional; por defecto, hoy en `America/Bogota`. Es la misma fecha de referencia que usa `today`.
- `--dias`, `--estado` y `--tipo` no valen en `summary` (`INVALID_ARGUMENT`); `--fecha` sigue sin valer en las demás acciones salvo `today`.

## Control

- Solo cuenta el **semestre activo**, como en `today` ([34-today.md](34-today.md)): las notas y tareas de materias de semestres cerrados no aparecen en `reciente`; las tareas sin materia sí (issue #16). Siguen disponibles con `notes`, `tasks` y `subject-brief`.
- Exige que la base local coincida con el hash publicado (`workbook_version`), **no el índice**. Si no coincide, devuelve `SOURCE_UNAVAILABLE` y no muestra datos.
- Las fuentes opcionales que falten (índice de conocimiento, log de la tarea diaria de Canvas Sync, autorización de Canvas, `drive_modified_time`) se degradan a `null` o `"unknown"`; el comando sigue respondiendo `ok`.
- La salida no incluye rutas locales ni material del token.

## Secciones de `data`

| Campo | Contenido |
|---|---|
| `avisos` | Primer campo. Lista de textos que el chat debe decir antes que nada (vacía si todo va bien): Canvas Sync con 2 o más ejecuciones fallidas seguidas, o sin ejecutarse desde hace 2 días o más; el aviso del token de Canvas; índice desactualizado |
| `fecha`, `fechas_zona` | Fecha de referencia usada y su zona (`America/Bogota`) |
| `semestre` | `semestre_id`, `nombre` y `bloque_activo` del semestre activo (`bloque_activo` es `null` si no está confirmado) |
| `materias` | Materias del semestre activo: `materia_id`, `nombre` y `ultima_clase` (fecha de la última clase registrada hasta la fecha de referencia, o `null`) |
| `urgente` | `items`: los 5 primeros elementos de `today` para la misma fecha; `resumen`: los conteos por tipo de `today` ([34-today.md](34-today.md)) |
| `reciente.notas` | Las 5 notas más recientes de materias del semestre activo: `nota_id`, `materia_id`, `tipo`, `texto` (recortado a 200 caracteres) y `fecha` (fecha local de creación). El texto completo se lee con `notes` |
| `reciente.tareas` | Tareas personales abiertas (`pendiente`, `en_progreso`) de materias del semestre activo o sin materia, como máximo 10, ordenadas por `fecha_limite` (sin fecha al final) y luego por id |
| `sistema.ultimo_canvas_sync` | Último resultado de la tarea diaria (`status` `ok`/`error`/`unknown`, `timestamp_local`, `exit_code`), igual que en `health` |
| `sistema.canvas_sync_historial` | Racha leída de los logs diarios `procesamiento\canvas-sync\logs\canvas-AAAA-MM-DD.log` (últimos 30 días): `ultimo_ok` y `ultimo_intento` (hora local), `fallos_seguidos` desde el último éxito, `fallando_desde` (primer fallo de la racha) y `ultimo_error` (última línea del mensaje, sin rutas locales). Todo `null` si no hay logs |
| `sistema.indice` | `fresh`: `true` si el índice corresponde a la base actual, `false` si está desactualizado, `"unknown"` si falta o no valida; `generated_at_utc` |
| `sistema.token_canvas` | `vence` (fecha ISO), `dias_restantes` (respecto a la fecha de referencia), `aviso` (texto si quedan 15 días o menos o está vencido; si no, `null`) y `motivo`. Se leen de los metadatos no secretos `control\canvas-sync\token-meta.json` que guardan `login`, `renew` y `status` de Canvas Sync ([15-canvas-sync.md](15-canvas-sync.md)); la credencial cifrada no se descifra. Si faltan o están dañados, los tres primeros son `null` y `motivo` explica la causa (por ejemplo, ejecutar `scripts\canvas-sync.cmd status` en el PC) |
| `sistema.publicado` | `sha256` (12 primeros caracteres del hash publicado), `drive_modified_time` y `revision` de la base |

## Ejemplo

```json
{
  "ok": true,
  "schema_version": 1,
  "action": "summary",
  "data": {
    "fecha": "2027-03-10",
    "fechas_zona": "America/Bogota",
    "semestre": {"semestre_id": "2027-1", "nombre": "Semestre 1 - 2027", "bloque_activo": 2},
    "materias": [
      {"materia_id": "MAT-SO2", "nombre": "Sistemas Operativos II", "ultima_clase": "2027-03-09"},
      {"materia_id": "MAT-ING3", "nombre": "Inglés III", "ultima_clase": null}
    ],
    "urgente": {
      "items": [
        {"tipo": "actividad_vencida", "prioridad": 0, "motivo": "Venció hace 2 días y aún no consta como entregada",
         "fecha": "2027-03-08", "materia_id": "MAT-SO2", "actividad_id": "ACT-SO2-004", "titulo": "Taller 2"}
      ],
      "resumen": {"actividad_vencida": 1, "actividad_proxima": 3, "tarea": 1,
                  "clase_sin_procesar": 0, "materia_descuidada": 1, "tarea_sin_fecha": 0}
    },
    "reciente": {
      "notas": [
        {"nota_id": "NOTA-…", "materia_id": "MAT-SO2", "tipo": "decision",
         "texto": "El parcial se presenta en parejas.", "fecha": "2027-03-09"}
      ],
      "tareas": [
        {"tarea_id": "TAR-…", "titulo": "Preparar parcial", "materia_id": "MAT-SO2",
         "fecha_limite": "2027-03-14", "estado": "pendiente"}
      ]
    },
    "sistema": {
      "ultimo_canvas_sync": {"status": "ok", "timestamp_local": "2027-03-10 06:00:00", "exit_code": 0},
      "indice": {"fresh": true, "generated_at_utc": "2027-03-10T11:05:00Z"},
      "token_canvas": {"vence": "2027-03-20", "dias_restantes": 10,
                       "aviso": "Token Canvas vence en 10 días: prepare la renovación con renew.",
                       "motivo": null},
      "publicado": {"sha256": "3f9a1c0b7e22", "drive_modified_time": "2027-03-10T11:00:12.000Z", "revision": 41}
    }
  }
}
```

## Instrucción recomendada para ChatGPT

Añadir a las instrucciones del chat o del proyecto:

> Al empezar un chat, ejecuta `scripts\academic-read.cmd summary` mediante Remote Desktop Commander y úsalo como estado actual (no leas `docs/` para eso). Si responde `ok: false`, informa el `error.code` y no inventes el estado. Para detalle, usa `today`, `notes`, `tasks` o `tracking`.
