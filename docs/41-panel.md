# 41 — Panel del día a día

**Estado:** implementado y probado localmente (WSL, con los lectores simulados); pendiente de probar en un PC Windows real. Fase 2 (issue #21).

## Para qué sirve

El panel muestra, sin comandos, lo que Academic Manager sabe de sus materias: qué hacer primero, un resumen del semestre y cada materia con sus actividades y fechas. También prepara el texto para pegar en ChatGPT. **Solo lee**: no cambia la base de datos, no sincroniza Canvas ni Drive y no usa internet. Para marcar algo como hecho o guardar una nota, se sigue usando el chat.

## Cómo se abre

Con el mismo acceso directo **Academic Manager** que el asistente ([38-asistente-configuracion.md](38-asistente-configuracion.md)), en el mismo servidor local seguro (solo este PC, clave por apertura).

- Si la configuración está **completa**, se abre el panel. Completa quiere decir: los pasos Carpeta de datos, Canvas, Google Drive, Carpeta de Drive y Materias están **Hecho** y la base de datos de este PC coincide con la publicada. Comprobación del equipo y Sincronización diaria no cuentan (solo se conocen al abrirlos).
- Si no, se abre el asistente como siempre. Su página principal ofrece «Abrir el panel del día a día» en cuanto la configuración queda completa.

Arriba de cada página está el menú **Hoy · Resumen · Materias · Instrucciones · Configuración**; Configuración es el asistente. El aviso de versión nueva («Actualizar ahora») también aparece en el panel.

## Las páginas

| Página | Qué muestra | De dónde sale |
|---|---|---|
| **Hoy** (`/panel`) | Lista de qué hacer primero, en el orden de `today` (lo más urgente arriba), con materia, tipo y fecha; arriba, los avisos del sistema en lenguaje sencillo (sincronización con Canvas que falla o no corre, token de Canvas por vencer, índice desactualizado). | `today` ([34-today.md](34-today.md)) y `summary` ([35-summary.md](35-summary.md)) |
| **Resumen** (`/panel/resumen`) | Semestre activo, sus materias con la última clase, lo más urgente, notas y tareas recientes y la última sincronización con Canvas. | `summary` |
| **Materias** (`/panel/materias`) | Las materias del semestre activo. | `summary` |
| **Materia** (`/panel/materia/MAT-…`) | Pendientes y fechas, entregas y notas, tareas abiertas y el cuadro **Copiar contexto**: el resumen de la materia en texto plano (fechas en hora de Colombia), listo para pegar al empezar el chat de su Project. | `subject-brief` |
| **Instrucciones** (`/panel/instrucciones`) | Un cuadro por Project (Coordinador y cada materia activa) con sus instrucciones para ChatGPT. | `academic_projects.render_all` ([29-preparacion-chatgpt-projects.md](29-preparacion-chatgpt-projects.md)), en memoria; no escribe archivos |

Una materia que no está en el semestre activo responde «no encontrada». Cada cuadro tiene un botón **Copiar** que lo copia al portapapeles y muestra «Copiado ✓». Si el navegador no deja copiar, el botón lo indica y selecciona el texto para copiarlo con Ctrl+C; sin el script, el botón no aparece y el texto se copia a mano.

## Cuando los datos no están al día

Nunca se muestra el texto de un error ni una página de fallo interno; el registro de la ventana solo guarda el tipo de error. Cada página lee por su cuenta, así que una materia con problemas no rompe las demás.

- **Sin base de datos o distinta de la publicada:** aviso y enlace a Configuración → Materias. Suele arreglarse con la sincronización diaria o terminando ese paso.
- **Base ocupada** (por ejemplo, la sincronización está escribiendo): esperar un momento y recargar.
- **Sin semestre activo con materias:** elegir las materias del semestre en Configuración → Materias.
- **Índice de búsqueda desactualizado** (solo afecta a Instrucciones): botón «Actualizar el índice» (la misma acción que ofrece el paso Materias después de publicar).
- **Archivo ilegible:** revisar que la carpeta de datos exista y que Google Drive esté abierto.
