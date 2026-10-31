# 36 — `anomalies`: inconsistencias de datos

**Estado:** implementado y probado localmente (WSL); uso real desde ChatGPT pendiente de validar en el PC con Windows.

## Para qué sirve

`anomalies` permite que ChatGPT mencione inconsistencias de los datos académicos al conversar, en lugar de descubrirlas por casualidad. Devuelve una lista determinista de hallazgos del semestre activo.

Es estrictamente de solo lectura: **nunca corrige nada**. No modifica la base, no publica, no sincroniza Canvas, no descarga archivos y no reconstruye el índice. Cada hallazgo es una observación que la persona decide cómo resolver.

## Uso

```powershell
scripts\academic-read.cmd anomalies
scripts\academic-read.cmd anomalies --materia MAT-SER
```

- `--materia MAT-...` es opcional y limita la salida a esa materia. Si la materia no existe en la base, devuelve `NOT_FOUND`; si existe pero no pertenece al semestre activo, la lista sale vacía.
- `--fecha`, `--dias`, `--estado` y `--tipo` no valen en `anomalies` (`INVALID_ARGUMENT`). No hay paginación.

## Control

- Exige que la base local coincida con el hash publicado (`workbook_version`), **no el índice**. Si no coincide, devuelve `SOURCE_UNAVAILABLE` y no muestra datos.
- Solo cuenta las materias del semestre activo (`05 - Semestres`, único `Activo`).
- La salida no incluye rutas locales: identifica archivos por su ID de Canvas y su nombre, y actividades por su `actividad_id`.

## Controles

| `tipo` | Qué detecta | Fuente |
|---|---|---|
| `archivo_duplicado` | Dos o más PDF de una misma materia con el mismo nombre y distinto SHA-256. `ids` son los ID de archivo de Canvas | Evidencia local de PDF de Canvas ([18-recursos-canvas-pdf.md](18-recursos-canvas-pdf.md)) |
| `texto_de_prueba` | Texto extraído de un PDF que contiene «lorem ipsum» o «texto de prueba» (sin distinguir mayúsculas). `ids` es el ID de archivo de Canvas | Evidencia local de PDF de Canvas |
| `actividad_sin_fecha` | Actividad de una materia activa sin `fecha_entrega`. `ids` es el `actividad_id` | `02 - Actividades` de la base publicada |

La evidencia de PDF es la que escribe `canvas_sync` al recuperar un recurso: `procesamiento\canvas-sync\pdfs\<curso>\<archivo>\latest.json` y su texto extraído. La materia se relaciona con su curso por `lms = Canvas` y `curso_externo_id` en `01 - Materias`. Las entradas dañadas o de otro curso se omiten.

Dos PDF con el mismo nombre y el mismo contenido no son una anomalía. Un texto de plantilla no prueba que todo el documento sea inútil: puede tener la consigna real más adelante.

Si falta la carpeta de evidencia de PDF, `archivo_duplicado` y `texto_de_prueba` no devuelven elementos y `fuentes.recursos_pdf` vale `no_disponible`; el comando sigue respondiendo `ok`.

## Orden y forma

Los elementos se ordenan por `tipo` (en el orden de la tabla), luego `materia_id` y luego `ids`. Cada elemento tiene exactamente `tipo`, `materia_id`, `ids` y `detalle` (texto corto en español). `resumen` cuenta los elementos por `tipo`.

```json
{
  "ok": true,
  "schema_version": 1,
  "action": "anomalies",
  "data": {
    "materia_id": "MAT-SER",
    "resumen": {"archivo_duplicado": 1, "texto_de_prueba": 0, "actividad_sin_fecha": 1},
    "fuentes": {"recursos_pdf": "disponible", "actividades": "disponible"},
    "items": [
      {"tipo": "archivo_duplicado", "materia_id": "MAT-SER", "ids": ["22615456", "22615502"],
       "detalle": "Hay 2 archivos «Tarea2.pdf» con contenido distinto (SHA-256 diferente); identifíquelos por ID de Canvas, no por nombre."},
      {"tipo": "actividad_sin_fecha", "materia_id": "MAT-SER", "ids": ["ACT-SER-2"],
       "detalle": "La actividad «Foro sin fecha» no tiene fecha de entrega."}
    ],
    "workbook_sha256": "ce198aa5…"
  }
}
```

## Fuera de alcance

- No se informan recursos sin actividad asociada (`recurso_sin_actividad`).
- No se revisan los textos del material de estudio por ejes (`procesamiento\canvas-sync\study`), solo los recursos PDF.
