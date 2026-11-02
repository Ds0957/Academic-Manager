# 43 — Conector de Claude Desktop

**Estado:** primera fase (solo lectura) implementada y probada localmente (WSL, con un cliente MCP de prueba), con el botón para conectarlo a Claude Desktop y las instrucciones para Claude. Rama `feat/conector-claude`.

## Para qué sirve

Permite que **Claude Desktop**, con la suscripción de Claude (sin clave de API ni pagos aparte), consulte los datos de Academic Manager durante una conversación: qué hay que hacer hoy, cómo va cada materia, notas guardadas, tareas, material de estudio y lo nuevo de Canvas.

Es un servidor **MCP** (Model Context Protocol) local: Claude Desktop lo abre en el mismo PC y le habla por la entrada y salida estándar (stdio). No abre puertos de red. Convive con otros conectores, como Desktop Commander; no los reemplaza.

## Herramientas

Todas son **de solo lectura** en esta fase: ninguna escribe en la base, en Drive ni en archivos. Usan los mismos lectores que el chat y el panel ([27](27-contrato-consulta-academica.md), [34](34-today.md), [35](35-summary.md), [36](36-anomalias.md), [41](41-panel.md)).

| Herramienta | Qué devuelve |
|---|---|
| `ponerme_al_dia` | Contexto para **empezar cada conversación**. Sin materia: lo de hoy, avisos, materias, notas y tareas recientes y notas nuevas de Canvas. Con `materia`: pendientes, entregas, tareas, decisiones, resúmenes de clase y notas recientes de esa materia, más su nota, mensajes sin leer y comentarios del profesor en Canvas. Listas recortadas a 20 elementos. |
| `hoy` | Qué hacer primero (`dias`: 1 a 60, por defecto 14). |
| `resumen` | Resumen del semestre y avisos del sistema. |
| `materias` | Materias del semestre activo con su identificador `MAT-…`. |
| `materia` | Detalle y cobertura del material de una materia. |
| `actividades`, `clases` | Actividades o clases de una materia, por páginas (`limite` 1 a 100, `offset`). |
| `notas` | Notas guardadas (opcional `materia` y `tipo`), por páginas. |
| `tareas` | Tareas personales (opcional `estado` y `materia`), por páginas. |
| `buscar` | Busca palabras en el material de una materia (`consulta`, `materia`, `limite` 1 a 30). |
| `leer_fuente` | Lee un fragmento de una fuente (`fuente`, opcional `materia`, `offset`, `length` hasta 6000) para citarla. |
| `anomalias` | Problemas detectados en los datos (opcional `materia`). |

Los parámetros se revisan antes de leer nada (identificadores `MAT-…`, límites como en la línea de comandos). Si algo falla, Claude recibe un mensaje sencillo en español (por ejemplo, «La base de datos se está actualizando en este momento…»), nunca el error técnico ni rutas del PC.

## Cómo lo abre Claude Desktop

Claude Desktop ejecuta, con el Python que instala Academic Manager:

```
python -m academic_mcp
```

La carpeta de datos se busca como en el resto de Academic Manager: la opción `--runtime`, si no `ACADEMIC_MANAGER_DATA`, si no el archivo de configuración del usuario, si no `C:\AcademicManagerData`.

Al conectarlo desde Configuración (abajo), la carpeta de datos elegida en el asistente se le pasa a Claude Desktop en `ACADEMIC_MANAGER_DATA`, así el conector lee siempre la misma carpeta que el panel.

## Conectar Claude Desktop

En **Configuración**, debajo de «Acceso desde el celular», la fila **Claude Desktop** abre la página `/claude` (solo en el PC; no está disponible desde el celular). Necesita la carpeta de datos elegida (paso 1).

1. Instale Claude Desktop, ábralo una vez con su cuenta y ciérrelo.
2. En la página, pulse **Conectar con Claude Desktop**.
3. Cierre Claude Desktop **por completo** (también desde la bandeja del sistema, junto al reloj) y vuelva a abrirlo. El conector `academic-manager` aparece entre sus conectores.

Qué hace el botón (`academic_app/claude_desktop.py`):

- Busca el archivo `claude_desktop_config.json` de Claude Desktop. La instalación normal lo guarda en `%APPDATA%\Claude\`; la de **Microsoft Store**, en `%LOCALAPPDATA%\Packages\Claude_…\LocalCache\Roaming\Claude\`. Si existen las dos carpetas, usa la que tiene otros archivos de Claude (en empate, la de Microsoft Store, porque esa carpeta desaparece al desinstalarla). Si no existe ninguna carpeta de Claude, dice «Claude Desktop no está instalado en este PC» y no crea nada.
- Agrega o reemplaza **solo** la entrada `mcpServers["academic-manager"]`:

  ```json
  {
    "mcpServers": {
      "academic-manager": {
        "command": "C:\\…\\Academic Manager\\python\\python.exe",
        "args": ["-m", "academic_mcp"],
        "env": {"ACADEMIC_MANAGER_DATA": "C:\\AcademicManagerData"}
      }
    }
  }
  ```

  `command` es el mismo Python con el que corre Academic Manager. Las demás opciones y los demás conectores (por ejemplo Desktop Commander) quedan igual.
- Antes de cambiar el archivo guarda una **copia de seguridad** a su lado: `claude_desktop_config.json.academic-manager-<fecha y hora UTC>.bak`. Si el archivo no existía, lo crea (sin copia, porque no había nada que guardar).
- Escribe de forma atómica (archivo temporal en la misma carpeta y reemplazo), en UTF-8.
- Si ya estaba conectado igual, no cambia nada ni hace otra copia.
- No toca el archivo si no es JSON válido, si no tiene la forma esperada (`mcpServers` debe ser un objeto) o si es un enlace a otro archivo: en ese caso explica el problema en palabras sencillas y muestra cómo hacerlo a mano.
- Nunca muestra ni registra el contenido del archivo: otros conectores pueden guardar claves en él.

La página muestra el estado: **No instalado**, **Sin conectar**, **Conectado**, **Hay que actualizar** (existe la entrada pero apunta a otro Python o a otra carpeta de datos, por ejemplo tras mover la carpeta: pulse **Actualizar conexión**) o **Hay que revisar** (el archivo no se puede usar). **Quitar conexión** quita solo la entrada `academic-manager`, también con copia de seguridad antes.

### Hacerlo a mano

Si el botón no puede, la página muestra dónde está el archivo y el texto exacto para pegar, con su botón **Copiar**. En Claude Desktop también se abre desde Configuración → Desarrollador → «Editar configuración». Si el archivo está vacío o no existe, se pega todo el texto; si ya tiene `mcpServers`, se agrega solo la parte de `academic-manager` dentro, sin borrar los demás conectores. Después se cierra Claude Desktop por completo y se vuelve a abrir.

## Instrucciones para Claude

La misma página trae un cuadro **Instrucciones para Claude** para pegar en las instrucciones de un proyecto de Claude (o al empezar una conversación):

1. Al empezar cada conversación, llamar primero a `ponerme_al_dia` (con la materia, si se va a hablar de una).
2. Para el material de una materia, usar `buscar` y después `leer_fuente`, y citar la fuente de cada dato; si no se encuentra, decirlo.
3. Para registrar entregas, notas, tareas o decisiones, usar Academic Manager: el panel o, cuando existan, las herramientas de escritura del conector, con vista previa y aprobación.
4. Aunque Claude tenga Desktop Commander u otro acceso a los archivos del PC, **nunca** editar directamente los datos, la base de datos ni los archivos de Academic Manager.

Desktop Commander sigue conectado y funcionando igual; estas instrucciones solo evitan que se use para cambiar los datos de Academic Manager por fuera de sus controles.

## Protocolo

Usa el SDK oficial de MCP para Python (`mcp`, versión fija en `requirements-app.txt`). El SDK atiende tanto a los clientes que empiezan con el saludo `initialize` (revisiones hasta 2025-11-25) como a los de la revisión sin estado 2026-07-28. Por la salida estándar solo viajan mensajes del protocolo; los avisos internos van a la salida de errores y solo dicen el tipo de fallo, sin datos.

## Privacidad

- Corre **solo en este PC**; no envía nada a internet por su cuenta.
- Lee solo los datos de Academic Manager (la base local, el índice de material y las copias de Canvas de la sincronización).
- Lo que Claude lee con estas herramientas sí llega a la conversación con Claude, como cuando usted le pega un texto.
- Escribir (registrar entregas, notas, tareas) llegará en una fase posterior, siempre con vista previa y aprobación.
