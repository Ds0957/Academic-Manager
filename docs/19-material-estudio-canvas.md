# 19 — Materiales de estudio de Canvas: referentes y lecturas por eje

**Fecha de exploración:** 2026-10-07.  
**Estado:** materiales obtenidos y validados; **sincronización permanente `sync-study` implementada y ejecutada posteriormente**. Las evidencias y scripts de la exploración se conservan, pero ahora existe código probado en el repositorio y comando estable. Canvas y Excel permanecen en solo lectura / sin modificación.

## Hallazgo principal

La lista ordinaria `GET /api/v1/courses/:id/files` contiene HTML `eje1.html` a `eje4.html` para cada una de **cuatro** materias. Esos HTML incluyen etiquetas personalizadas `<contenido referente-completo=...>`, `<acordeon url-referente=...>`, `<lectura-recomendada>`, `<video-capsula>` y `<modal>`, cuyos atributos enlazan a referentes de pensamiento completos, lecturas y multimedia/interactivos, principalmente en `https://contenidos.areandina.edu.co`. La extracción anterior de `docs/18-recursos-canvas-pdf.md` se limitaba al inventario PDF de Canvas, por eso **no cubrió los 16 referentes de pensamiento** enlazados desde los HTML.

Se recuperaron por GET las respuestas de los **16 HTML de eje** y se identificó **un referente PDF principal por cada eje**. Las URLs firmadas de descarga temporal de Canvas no fueron persistidas. Los PDF institucionales enlazados se descargaron sin token/cookies, mediante HTTPS GET exclusivamente del dominio exacto `contenidos.areandina.edu.co`, con redirecciones limitadas al mismo dominio, tamaño máximo controlado y sin ejecutar contenidos interactivos.

## Material principal efectivamente obtenido

| Materia / course ID | E1 | E2 | E3 | E4 | Páginas | Estado |
|---|---:|---:|---:|---:|---:|---|
| Gestión de Bases de Datos / 82862 | 16 | 17 | 22 | 18 | **73** | PDF + texto completos |
| Sistemas Transaccionales / 82892 | 20 | 19 | 18 | 21 | **78** | PDF + texto completos |
| Sistemas Operativos II / 91004 | 22 | 23 | 23 | 26 | **94** | PDF + texto completos |
| SER (BE-IT) / 91005 | 16 | 16 | 17 | 15 | **64** | PDF + texto completos |
| **Total** | **74** | **75** | **80** | **80** | **309** | **16/16 referentes** |

El texto extraído de los 16 referentes contiene en conjunto alrededor de **536.237 caracteres**. Se validó offline para cada uno la procedencia `course_id`/`eje`/`file_id` del HTML de Canvas, el SHA-256 del PDF original, longitud de bytes y archivos TXT. Los referentes PDF no son meros contenedores de enlaces; contienen el texto teórico real de los ejes y sus índices.

Ejemplos de títulos/secciones principales observados **en los PDF**:
- GBD: E1 «Generalidades de las Bases de Datos»; E2 «Seguridad en Bases de Datos»; E3 «Aplicación de Bases de Datos»; E4 «Administración de Bases de Datos».
- SO2: E1 «Vamos a gestionar el hardware»; E2 «Necesito una Red Segura»; E3 «El sistema operativo de mi móvil»; E4 «Mi móvil se bloqueó, ¿ahora qué hago?».
- SER: E1 «¿Qué es un ser humano?»; E2 «Socialización primaria»; E3 «ética del cuidado»; E4 «La importancia de lo inútil». Estos son títulos/encabezados, no resúmenes inventados.

## Lecturas y materiales complementarios

El inventario HTML de los 16 ejes contiene:
- **26 referencias a lecturas recomendadas**.
- **14 PDF de lecturas** servidos directamente por `contenidos.areandina.edu.co`; **14/14 se descargaron y verificaron** localmente, con **1.995 páginas** entre ellos.
- **11/14** lecturas institucionales produjeron texto extraído, unos **1.301.267 caracteres**. Las otras **3** son documentos extensos (**738**, **331** y **427** páginas) cuyo **PDF original sí está guardado**, pero su texto completo quedó marcado `page_limit` en el primer pase; no se presenta como procesado. No aplicar extracción masiva de textos de libros sin revisar qué capítulos/páginas son relevantes para el eje.
- De las otras **12 referencias de lectura**, **11** apuntan a sitios/bibliotecas externos y **una** no resolvió a una URL HTTPS de PDF institucional según las reglas del descubrimiento. No se intentó sortear sus accesos ni usar credenciales de Canvas en esos sistemas.
- **84 referencias a recursos multimedia/interactivos** (`modal`, videos, controles de lectura, podcasts, infografías, etc.). Se inventariaron como enlaces/títulos; **su contenido interno no fue descargado ni ejecutado**. Los enlaces pueden requerir JavaScript, una sesión específica u otro permiso.

Una de las lecturas de SO2 E2 tiene tamaño **25.495.849 bytes**: el primer límite experimental de 25 MB bloqueó su descarga, se comprobó que el servidor entregaba realmente un PDF válido (HTTP 200, `application/pdf`) y se repitió exclusivamente ese recurso con un límite de 35 MB. Finalmente quedó descargado con 116 páginas y texto extraído.

**Inglés III (91003)** no se incluye en los 16 referentes: el endpoint `files` de Canvas continúa devolviendo HTTP 403 para esa materia y sus materiales siguen otra organización. No inferir que carece de material; está **pendiente descubrir un canal autorizado distinto**.

## Almacenamiento y trazabilidad

La exploración guardó localmente, no en GitHub ni Drive:

```text
C:\AcademicManagerData\procesamiento\canvas-sync\
├── study-inventory.json
└── study\
    ├── 82862\
    ├── 82892\
    ├── 91004\
    └── 91005\
        └── ejeN\
            ├── latest.json
            ├── referente-<SHA256>.pdf
            ├── referente-<SHA256>.txt
            └── lecturas\<index>\
                ├── latest.json
                ├── lectura-<SHA256>.pdf
                └── lectura-<SHA256>.txt  (solo si ya se extrajo)
```

Cada `latest.json` conserva procedencia del HTML de Canvas, URL institucional sin tokens, SHA-256 del PDF, páginas, tamaño, título/estatus y vínculos de lecturas y medios. La verificación offline `validate_study.py` confirmó todos los **16 referentes y 14 lecturas**, sin hashes erróneos ni archivos faltantes. **No se editaron Canvas ni `database.xlsx`**. Como en el Sync anterior, GET puede generar registros de acceso o indicadores de lectura en los servidores.

## Sincronizador estable `sync-study` — implementado y validado

Se incorporó `canvas_sync/study.py` y el comando de uso local:

```powershell
& "C:\AcademicManagerDev\Academic-Manager\scripts\canvas-sync.cmd" sync-study --all-active
# O para un curso específico:
& "C:\AcademicManagerDev\Academic-Manager\scripts\canvas-sync.cmd" sync-study --course-id 82862
```

**Contrato:** peticiones HTTP exclusivamente **GET**; el token DPAPI se emplea únicamente en endpoints de la API Canvas (perfil, listado de cursos/archivos y metadatos de HTML). Los enlaces temporales HTML y los PDF de `contenidos.areandina.edu.co` se descargan por GET **sin transmitir el token**. HTTPS y destinos autorizados, límites de HTML (150 KB), PDF (35 MB), páginas de extracción (220) y texto (700.000 caracteres). No se ejecutan interactivos ni enlaces de bibliotecas externas. No consulta datos de compañeros, respuestas de exámenes ni modifica la plataforma.

El comando descubre `eje1.html` a `eje4.html` desde el listado actual de archivos de cada curso y reconstruye el índice con referencias, lecturas y multimedia. Los PDF y TXT se identifican por SHA-256; cuando cambia el contenido conserva la versión anterior y actualiza el índice; cuando no cambia, **no duplica archivos ni snapshots**. Si falla una descarga, se conserva el último índice completo de esa materia (pueden existir recursos nuevos parciales previos a la publicación). Un error de permisos HTTP 403/404 queda registrado como restricción, sin intentar eludirlo. Se añaden metadatos y versiones únicamente en runtime local:

```text
C:\AcademicManagerData\procesamiento\canvas-sync\study\<course_id>\
├── index.json                 (sha256 + índice de ejes y procedencia)
├── snapshots\...             (versiones de índice por contenido)
└── ejeN\
    ├── latest.json            (referente del eje)
    ├── referente-<SHA>.pdf
    ├── referente-<SHA>.txt
    └── lecturas\<index>\
        ├── latest.json
        ├── lectura-<SHA>.pdf
        └── lectura-<SHA>.txt  (si se extrajo capa textual)
```

**Prueba real posterior a la exploración:** `sync-study --all-active` terminó con código 0 y **23 consultas GET a la API de Canvas** (más descargas GET sin autenticación). Se crearon índices para los cuatro cursos de ejes con **16 referentes**, **14 lecturas institucionales**, **12 lecturas no descargadas catalogadas** y **84 referencias multimedia**, verificadas offline con hashes correctos. Inglés III respondió `files=403` y no se generó índice para esa materia. Segunda ejecución de GBD `--course-id 82862`: **índice sin cambios**, una sola versión; GET correctos, Excel y Canvas sin escrituras. Ocho tests específicos nuevos cubren HTML sintético, dominios autorizados, bloqueo de redirecciones, duplicados, 403, integridad/idempotencia y ausencia de operaciones externas no autorizadas.

**Límites persistentes:** no hay ejecución programada autónoma ni sincronización al workbook V2.1. Las 3 lecturas de 331/427/738 páginas se preservan en PDF con estado `page_limit`, sin prometer extracción completa. Las bibliotecas externas y los 84 contenidos interactivos siguen inventariados, no descargados.

**Actualización posterior (2026-10-08):** `sync-study` ahora tiene reutilización segura de PDF/TXT por SHA y revalidación del contenido remoto cada siete días; `--full-refresh` fuerza comprobación de PDFs y `--approved-only` limita el sondeo a matrículas confirmadas. Validación real: 30 PDF reutilizados sin nueva descarga en una segunda ejecución. Ver `docs/28-actualizacion-incremental-materiales.md`.

**Uso posterior (2026-10-08):** los índices y TXT/PDF aquí descritos ya alimentan el primer índice académico consultable de `academic_knowledge/`, junto con las clases Capture y las actividades existentes; 34 fuentes con texto incluyendo guías Canvas, y 63 relaciones con actividades. Ver `docs/25-indice-conocimiento-academico-v1.md`. Este avance no elimina la restricción de Inglés III ni supone extracción de los tres libros extensos.

## Estado de diseño y próximos pasos

**Confirmado:** los referentes de pensamiento completos están accesibles sin API especial de contenidos: se descubren siguiendo referencias del HTML de cada eje, que sí está listado por Canvas. El diseño de Academic Manager debe separar **material de estudio por materia/eje** de **guías de actividades, notas y recursos complementarios**; no todo es actividad entregable. Los identificadores y hashes evitan colisiones de nombres.

**Pendiente (no implementado en el conector distribuido):**
1. **Implementado:** comando `sync-study`, con descarga selectiva por tipo, política para documentos extensos, indexación por materia/eje, SHA-256, historial e idempotencia.
2. **Parcialmente implementado:** índice **Materia → Eje → Referente principal / Lecturas / Multimedia** con origen/hash. **Pendiente:** vinculación explícita, revisada y aprobada con las guías de actividad y con las entidades estructuradas de V2.1.
3. Revisar las 12 lecturas externas y las 84 referencias multimedia sin abrir automáticamente sesiones ajenas ni ejecutar evaluaciones; definir opciones por proveedor y permiso.
4. Decidir si se extraen solo capítulos recomendados de los tres libros largos y si conviene resumir/indizar la teoría; citar siempre su origen y diferenciar contenido textual de elaboración posterior por IA.
5. Resolver un acceso permitido a materiales de Inglés III, que no participa en la estructura de ejes estándar de estas cuatro materias.
6. Integrar en V2.1 únicamente después de definir el modelo Actividad–Recurso y aplicar el protocolo seguro del workbook; no se aprobó ningún cambio a Excel.
