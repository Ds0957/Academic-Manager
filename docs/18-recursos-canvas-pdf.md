# 18 — Canvas Sync: recuperación selectiva y análisis local de PDF

**Estado:** implementado, validado con pruebas sintéticas y ejecutado realmente con **12 PDF accesibles de cuatro materias** el 2026-10-07. No existe descarga indiscriminada ni importación al workbook.

## Contrato de solo lectura y seguridad

- Comando `scripts/canvas-sync.cmd pdf --course-id ID --file-id ID` o `python -m canvas_sync pdf ...`.
- El archivo **debe estar incluido en el inventario firmado por hash de un snapshot V0.1** del curso (y el estado de la categoría `files` debe ser `ok`). No se admiten IDs arbitrarios ajenos al curso.
- El único uso de la credencial Canvas cifrada con DPAPI es consultar mediante **GET** el perfil mínimo y los metadatos del archivo a `https://areandina.instructure.com/api/v1/files/ID`. Se verifica la identidad y correspondencia de ID/MIME, tamaño máximo de **5 MB**, permiso vigente y ausencia de bloqueo.
- La URL temporal que devuelve Canvas se utiliza **solo en memoria**. La descarga binaria usa **GET sin cabecera Authorization**, incluso al redirigir. HTTPS obligatorio, con origen inicial Canvas, máximo 4 redirecciones y lista controlada de dominios de infraestructura Instructure/almacenamiento. Prohibido descargar en destinos externos no incluidos. **Nunca guardar URLs con parámetros firmados, cookies o tokens**.
- Se valida el tamaño esperado, `%PDF-`, SHA-256, número de páginas (máximo 35) y texto mediante la biblioteca `pypdf==5.9.0` fijada en `requirements-canvas.txt`. No ejecutar macros/JavaScript, formularios, contenido embebido ni páginas externas. Escaneos sin capa textual quedarán señalados como `no_text_layer`, sin inventar OCR.
- Los resultados se guardan **solo en** `C:\AcademicManagerData\procesamiento\canvas-sync\pdfs\<course_id>\<file_id>\`: PDF por hash, TXT por el mismo hash cuando existe texto, y `latest.json` sin secretos. La segunda descarga idéntica no crea otro binario.
- El programa **no envía POST/PUT/PATCH/DELETE** a Canvas, ni modifica/entrega actividades, examina respuestas a evaluaciones o edita la base. Las descargas GET pueden generar trazas de acceso o indicadores de lectura del servidor; no se afirma ausencia de todo efecto secundario remoto.

Una redirección de `areandina.instructure.com` a `a8606-22387883.cluster51.canvas-user-content.com` fue bloqueada de forma preventiva en el primer ensayo; se inspeccionó **solo el hostname, nunca la URL firmada** y se verificó que `*.canvas-user-content.com` está documentado por Instructure como dominio de Canvas. Solo entonces se agregó a la lista HTTPS autorizada. Referencia oficial: [Canvas Domain, Email, and Server Management](https://community.instructure.com/en/kb/articles/485223-canvas-domain-email-and-server-management).

## Resultados reales

Se recuperaron **12/12 PDF catalogados** de cuatro cursos, **56 páginas en total**, todos con `extraction_status=text` y SHA-256 verificado contra los bytes locales. Inglés III devolvió `403` al listar archivos y no se intentó eludir ese permiso.

| Curso | ID de archivo | Archivo de Canvas | Páginas | Estado |
|---|---:|---|---:|---|
| GBD 82862 | 22387883 | Actividad evaluativa.pdf (Eje 2, según exploración GBD) | 2 | Texto |
| GBD 82862 | 22387892 | Actividad evaluativa eje 3.pdf | 2 | Texto |
| GBD 82862 | 22387896 | Actividad evaluativa eje 4.pdf | 2 | Texto |
| Sistemas Transaccionales 82892 | 22388616 | Tarea_E3.pdf | 5 | Texto; plantilla extraña |
| Sistemas Transaccionales 82892 | 22388626 | Tarea_E1.pdf | 2 | Texto; plantilla extraña |
| Sistemas Transaccionales 82892 | 22388632 | Proyecto_E4.pdf | 4 | Texto; plantilla extraña |
| SO2 91004 | 22401322 | Actividad evaluativa eje 3.pdf | 2 | Texto |
| SO2 91004 | 22401341 | Actividad evaluativa eje 4.pdf | 2 | Texto |
| SER 91005 | 22615456 | Tarea2.pdf («El Divino Niño») | 2 | Texto |
| SER 91005 | 22615479 | Tarea3.pdf («Investigación del problema e implementación del reto») | 6 | Texto |
| SER 91005 | 22615493 | Referente2.pdf (propiedad industrial) | 21 | Texto |
| SER 91005 | 22615502 | Tarea2.pdf («Planificando el Reto») | 6 | Texto |

**Calidad y contradicciones:** las tres guías PDF de Sistemas Transaccionales incluyen al inicio referencias a «Negocios internacionales - Eje 1», iconografía genérica y `Lorem ipsum`. Esto **no prueba que toda la guía sea inútil**, pues E3/E4 también contienen instrucciones más adelante; requiere distinguir plantilla reutilizada de consigna específica. En SER hay **dos archivos con el mismo nombre `Tarea2.pdf`** pero distinto ID, SHA y contenido: se deben identificar **por ID de Canvas y hash**, jamás únicamente por nombre.

La guía GBD E2 extrae consignas SQL/copias de seguridad e integridad en inglés, coherentes con la exploración anterior `docs/09-exploracion-canvas.md`. La PDF de GBD E3 describe desarrollo de aplicación web/persistencia; **no debe imponerse automáticamente como consigna definitiva** ante el cambio de actividad anunciado por el docente en la grabación y pendiente de contrastar en Canvas.

## Enlaces externos: inventario sin abrir

Se inspeccionaron **solo los hostnames** de enlaces de las capturas V0.1, guardando el resumen local `C:\AcademicManagerData\procesamiento\canvas-sync\external-links-hosts.json`. Aparecen `areandina.instructure.com`, `instructure-uploads.s3.amazonaws.com`, `campusvirtual.areandina.edu.co`, `servicio.areandina.edu.co`, `drive.google.com`, `meet.google.com`, `www.canva.com` y `tel.meet`. No se consultaron URLs externas ni se pasaron credenciales Canvas a esas aplicaciones. No asumir que el acceso a grabaciones, Drive, videoconferencias o contenido LTI se obtiene mediante el mismo token.

## Validaciones y evolución

- Pruebas automáticas: curso/archivo cruzados, integridad del catálogo, redirecciones no autorizadas, HTTPS, GET binario sin Bearer, archivos HTML falsos, tamaños excesivos, rutas de salida por hash, idempotencia y protección de secretos. Biblioteca opcional para extracción: prueba con PDF sintético de una página sin texto.
- Suite general tras incorporar este componente: **63 pruebas ejecutadas, 61 correctas y 2 omitidas** (omisiones históricas de Capture).
- Primera descarga de GBD 22387883 produjo PDF de 132153 bytes, dos páginas y texto. Se reejecutó y conservó un solo PDF de ese hash.
- El catálogo y los PDF se mantienen locales; **no** se han relacionado definitivamente todos los archivos con `02 - Actividades` ni copiado el material en Google Drive.

**Evolución exploratoria posterior (2026-10-07):** se descubrió que los HTML `ejeN.html` enlazan a referentes de pensamiento y lecturas en `contenidos.areandina.edu.co`. Se recuperaron por separado **16 referentes completos** y **14 PDF de lectura institucional**, con indexación local por materia y eje; ver `docs/19-material-estudio-canvas.md`. Esta exploración es posterior al componente estable `pdf` que solo admite archivos del inventario Canvas; no confundir ambas rutas ni afirmar que `pdf` descarga materiales externos automáticamente.

**Próximo paso pendiente:** definir y verificar un índice de correspondencias Actividad–Archivo basado en IDs, fuente y evidencia, con estado `confirmado/propuesto/requiere_revisión`; resolver las discrepancias mencionadas antes de importar información a V2.1. Para archivos LTI o de Google Drive se necesitará cada autorización específica; no automatizar el seguimiento de URLs externas sin diseño aprobado.
