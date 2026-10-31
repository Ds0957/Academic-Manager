# 15 — Canvas Sync V0

**Estado:** decisión aprobada, implementación local validada con tests y **primera ejecución autenticada de `login`/`status`/`sync` realizada con éxito el 2026-10-07**. No modifica el workbook.

## Motivación y fuente de datos

El 2026-10-07, desde el PC Diego, el estudiante comprobó que el token personal creado en Canvas Areandina responde correctamente a `GET /api/v1/users/self/profile`, `GET /api/v1/courses/82862` y `GET /api/v1/courses/82862/assignments?per_page=100`; la primera página devolvió **5 actividades** de Gestión de Bases de Datos (curso `82862`). Esta comprobación fue manual y **anterior** a Canvas Sync V0; no debe confundirse con una ejecución autenticada del nuevo programa.

**Decisión confirmada:** integrar Canvas mediante token personal introducido exclusivamente en el computador del estudiante, protegido localmente con **DPAPI de usuario Windows** y renovable **manualmente** cuando caduque. No copiar credenciales a GitHub, chats, JSON académicos o Google Drive. El token personal puede autorizar más acciones que las utilizadas aquí: el código de Sync V0 realiza **solo GET**.

## Implementación V0

Módulo `canvas_sync/`, lanzador `scripts/canvas-sync.cmd` y tests `tests/test_canvas_sync.py`.

| Acción | Función |
|---|---|
| `login` | Pide vencimiento local, lee el token mediante entrada oculta y consulta perfil antes de cifrarlo. No reemplaza una autorización existente. |
| `renew` | Pide token nuevo y vencimiento, valida contra la **misma cuenta Canvas** y reemplaza la credencial atómicamente solo si la validación funciona. Si falla, preserva la anterior. |
| `status` | Lee vigencia local, avisa desde 15 días antes y consulta por GET la validez de la autorización. No revela el token ni los datos personales del perfil. |
| `courses` | Consulta con paginación los cursos activos visibles y guarda una lista mínima en JSON local. |
| `sync` | Consulta perfil, curso GBD y todas las páginas de actividades; incluye datos de `submission` cuando Canvas los entrega. Escribe snapshots mínimos y comparación por ID. |

Comandos desde una **terminal local interactiva en Diego**, sin pegar bloques multilínea:

```powershell
& "C:\AcademicManagerDev\Academic-Manager\scripts\canvas-sync.cmd" login
& "C:\AcademicManagerDev\Academic-Manager\scripts\canvas-sync.cmd" status
& "C:\AcademicManagerDev\Academic-Manager\scripts\canvas-sync.cmd" courses
& "C:\AcademicManagerDev\Academic-Manager\scripts\canvas-sync.cmd" sync
& "C:\AcademicManagerDev\Academic-Manager\scripts\canvas-sync.cmd" renew
```

Durante `login` o `renew`:

1. Generar un token en **Canvas → Cuenta → Configuraciones → Nuevo token de acceso**, con propósito `Academic Manager Sync` y un vencimiento de hasta 90 días; conservar la fecha y **hora exactas** elegidas.
2. Ejecutar **solo la línea de comando** `login` o `renew`. Cuando lo solicite, escribir vencimiento `YYYY-MM-DD HH:MM` (hora local del PC). No se infiere automáticamente la fecha configurada en Canvas.
3. En la siguiente petición, **pegar el token en la terminal cuando el programa esté esperando su entrada oculta**. No enviarlo al chat, ni ponerlo después del prompt normal `PS C:\...>` y no ejecutarlo como argumento.
4. Esperar el mensaje de validación. El token cifrado se almacena solo bajo `C:\AcademicManagerData\control\canvas-sync\canvas-token.dpapi` en el usuario de Windows que hizo `login`.

**No hay renovación automática en la API**: después del vencimiento se crea un token nuevo desde Canvas y se introduce con `renew`. Los avisos aparecen al ejecutar `status` y en el bloque `token` del informe de `daily` (ver abajo); el uso desde otro PC sigue **pendiente** (requiere su propia autorización DPAPI). Un `401` antes de la fecha local también exige reautorizar.

### Metadatos de vencimiento del token

`login` y `renew` guardan, además de la credencial cifrada, un archivo **no secreto** `C:\AcademicManagerData\control\canvas-sync\token-meta.json`, escrito de forma atómica:

```json
{"schema_version": 1, "expires_at": "2027-01-04T23:30:00-05:00", "saved_at": "2026-10-07T15:02:11+00:00"}
```

Solo contiene el vencimiento declarado y el momento de guardado (UTC); **nunca** el token ni datos de la cuenta. El token sigue existiendo únicamente en `canvas-token.dpapi`. `status` (que ya descifra la credencial) crea o corrige este archivo si falta, está dañado o no coincide, de modo que el token actual lo obtiene sin repetir `login`. Si no se puede escribir, `login`/`renew`/`status` muestran un aviso y continúan.

### Bloque `token` del informe diario

`daily` añade al informe JSON de la rutina (`procesamiento\canvas-sync\routine\semestres\<semestre>\bloque<n>\latest.json` y el resultado devuelto por `routine`) un bloque leído solo de los metadatos, sin descifrar:

```json
"token": {"vence": "2027-01-04", "dias_restantes": 74, "aviso": null}
```

- `dias_restantes`: días de calendario desde la fecha local hasta la fecha de vencimiento (negativo si ya pasó).
- `aviso`: texto cuando quedan **15 días o menos** (`Token Canvas vence en N días: prepare la renovación con renew.`) o el token está vencido (`Token Canvas vencido: ejecute renew.`); `null` en otro caso.
- Cuando hay `aviso`, `daily` también imprime una línea `AVISO TOKEN CANVAS: …`, que queda en el log diario de `canvas-daily.ps1`.
- Si faltan o están dañados los metadatos, `vence` y `dias_restantes` son `null` y `aviso` explica que hay que ejecutar `status` en el PC.
- Un token vencido sigue haciendo fallar `daily` en vivo como antes. El bloque no forma parte del digest del informe: un cambio solo de días restantes no reescribe `latest.json`, así que la copia en disco refleja la última escritura; la salida de texto de `daily` y sus códigos de salida no cambian.

## Destino local

```text
C:\AcademicManagerData\
├── control\canvas-sync\canvas-token.dpapi       (secreto cifrado; NO sincronizar)
├── control\canvas-sync\token-meta.json         (solo vencimiento; sin secretos)
└── procesamiento\canvas-sync\
    ├── courses.json                             (cursos activos de Canvas)
    └── 82862\
        ├── latest.json                          (última evidencia de GBD)
        └── snapshots\                           (historial de cambios)
```

La evidencia solo contiene los campos de curso/actividad de la lista permitida, además de `submission` del propio estudiante si lo entrega Canvas. No copia perfiles, nombres de compañeros, contenido completo de HTML ni token. Comparación por ID externo: añadidas, modificadas y retiradas. Si el contenido no cambió, `sync` no duplica el snapshot. Pagina por `Link: rel=next`, restringiendo el host `areandina.instructure.com` y rechazando redirecciones.

Todo permanece **local** y excluido del repositorio; V0 **no modifica ni publica la base XLSX**. La integración en V2.1 solo se abordará después de revisar datos reales y aprobar la regla de mapeo.

## Validación y límites

- Pruebas unitarias con respuestas API simuladas: DPAPI de usuario, renovación fallida, cambio de identidad, vencimiento máximo, alerta de 15 días, 401 redactado, host autorizado, redirecciones rechazadas, paginación, snapshots e idempotencia.
- La suite íntegra de Capture + Canvas Sync pasa; el entorno conserva dos omisiones conocidas de Capture.
- **Validación real completada (2026-10-07):** el usuario ejecutó `login` en la terminal local y obtuvo «Autorización validada por Canvas y guardada cifrada en el PC». Vencimiento declarado: `2027-01-04 23:30 -0500`. Después, Remote Desktop Commander ejecutó `scripts/canvas-sync.cmd status`, que verificó la cuenta por GET y terminó con código `0`; no se mostró ni se copió el token.
- `scripts/canvas-sync.cmd sync --course-id 82862` completó su primera extracción autenticada: **5 registros**, IDs únicos `391671`, `391683`, `391687`, `391690`, `391692`. Las cuatro actividades evaluativas E1–E4 coinciden en ID y 20 puntos con la exploración previa `docs/09-exploracion-canvas.md`. El quinto registro aparece como `Nota final [P1]`, con 100 puntos: **no importarlo como tarea pendiente** sin clasificar primero su naturaleza.
- Cobertura observada del JSON: `due_at`, `unlock_at` y `lock_at` presentes en 4/5; `points_possible` en 5/5; `quiz_id=228076` en E1; `discussion_id` ausente en los cinco; objeto `submission` en 5/5 con `workflow_state=unsubmitted`, sin `submitted_at`, `score`, `grade` ni `graded_at`. Las fechas llegan en UTC (por ejemplo E1 `due_at=2026-10-13T04:59:59Z`), consistentes con el 12 de octubre a las 23:59 UTC-05 documentado. No atribuir una zona horaria institucional contractual sin corroborarla.
- Evidencia local: `C:\AcademicManagerData\procesamiento\canvas-sync\82862\latest.json` y un snapshot en `snapshots/`. SHA-256 del payload comprobado, 5 IDs únicos y diferencias iniciales `added=5, modified=0, removed=0`. Una **segunda ejecución de `sync`** devolvió «sin cambios (5 actividades)», mantuvo exactamente **1** snapshot y terminó con código 0: prueba real de idempotencia.
- Estado del workbook compartido consultado tras sincronizar: `status=free`, `lock=null`. El sincronizador no ejecutó ningún flujo de edición/publicación de Excel.
- **Aún sin validar:** comando `courses` con la cuenta real, extracción de entregas efectivamente realizadas/calificadas, contenidos externos y reconciliación con V2.1. No existe actualización automática del Excel ni alertas autónomas de expiración.
- Canvas puede limitar o no ofrecer las entregas, calificaciones, módulos, anuncios o contenidos de herramientas externas. Se verificará la cobertura campo a campo antes de poblar V2.1.
- **Auditoría ampliada completada (2026-10-07):** se probaron categorías REST y campos en las **cinco materias**. La consulta recuperó tareas, módulos, rúbricas adjuntas, archivos (solo metadatos), discusiones, quizzes, planificador y calificaciones de Inglés III. Las cifras, restricciones y siguientes pasos están en `docs/16-cobertura-canvas-api.md`. Estos datos **no están incorporados a los snapshots de Sync V0**, salvo los campos de la extracción mínima anterior.
- **Canvas Sync V0.1 implementado y validado con las cinco materias (2026-10-07):** comando `sync-rich --all-active`, 66 peticiones HTTP GET, evidencia estructurada local de rúbricas, consignas, módulos, recursos indexados, quizzes, discusiones y calificaciones propias. Snapshots nuevos bajo `procesamiento/canvas-sync/v01`, separados del formato V0; segunda ejecución sin cambios. Sin modificaciones de Canvas mediante métodos de escritura ni actualización del workbook. Ver contrato en `docs/17-canvas-sync-enriquecido.md`.
- Propuesto para V1: aviso autónomo de expiración, bloqueo/supervisión de ejecuciones concurrentes, migración verificada a XLSX y políticas de reconciliación de ediciones manuales.
