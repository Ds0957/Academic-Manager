# 38 — Asistente de configuración

**Estado:** implementado y probado localmente (WSL, con Canvas, Google y Windows simulados); pendiente de probar en un PC Windows real. Fase 1 (issue #20), tarea T3.

## Para qué sirve

El asistente prepara Academic Manager **sin usar la consola**: se abre una página en el navegador del PC y lleva, paso a paso, por todo lo que antes pedía comandos. Cada paso muestra si está **Hecho**, si **Falta** o si hay un **Error**. Puede cerrarlo cuando quiera: lo que ya hizo queda guardado y al volver ve en qué paso va.

## Cómo abrirlo

- Con el instalador de Windows ([39-instalador-windows.md](39-instalador-windows.md)): el acceso directo **Academic Manager** del menú Inicio o del escritorio.
- En la instalación de desarrollo: doble clic en `scripts\academic-app.cmd`.
- O, desde la carpeta de Academic Manager: `python -m academic_app`. Con `--sin-navegador` solo muestra la dirección.

Se abre el navegador con la página del asistente. Si no se abre solo, la ventana negra muestra una dirección `http://127.0.0.1:…/?clave=…`: cópiela en el navegador. Esa dirección sirve solo para esta apertura.

Para terminar pulse **Cerrar el asistente** en la página. Si lo deja abierto sin usarlo, se cierra solo a los 30 minutos.

## Los pasos

### 1. Carpeta de datos

Dónde guarda Academic Manager la base de datos, las autorizaciones cifradas y los archivos de trabajo. Se recomienda `C:\AcademicManagerData`. Si la carpeta no existe, se crea. La ruta se normaliza antes de guardarla (por ejemplo `C:\a\..\Datos` queda `C:\Datos`). Se guarda como `runtime` en el archivo de configuración del usuario (`%APPDATA%\AcademicManager\config.json`, ver [03-almacenamiento-y-seguridad.md](03-almacenamiento-y-seguridad.md)). Los demás pasos necesitan este primero.

No se acepta una carpeta igual a la del programa o dentro de ella (`%LOCALAPPDATA%\Programs\AcademicManager`, la carpeta del código que está corriendo y, en una copia instalada, toda su carpeta), porque el instalador la reemplaza al actualizar y la borra al desinstalar; la comparación no distingue mayúsculas en Windows. Lo mismo vale para la carpeta de Drive del paso 5.

Si el PC tiene la variable `ACADEMIC_MANAGER_DATA`, la página avisa: los comandos usan esa variable antes que el archivo.

### 2. Comprobación del equipo

La misma comprobación que `python -m academic_setup check --texto` ([37-comprobacion-equipo.md](37-comprobacion-equipo.md)): dice si este PC puede transcribir clases con Capture y cómo. La comprobación solo mira el PC y no cambia nada; instalar WhisperX es un botón aparte. Como no deja nada guardado, se repite cada vez que se abre el paso.

Debajo, según el veredicto de esa misma comprobación (`academic_setup.whisperx`, sin copiar su lógica):

- **`apto_gpu`** o **`solo_cpu`** sin WhisperX: botón **Instalar WhisperX**. Instala PyTorch con CUDA (`apto_gpu`) o para el procesador (`solo_cpu`) desde el índice oficial de PyTorch, después `whisperx==3.8.6`, en el mismo Python con el que corre el asistente, y por último FFmpeg 9.0.2 (huella SHA-256 fijada; si no coincide, no se usa) con `ffmpeg.exe` y `ffprobe.exe` junto a ese `python.exe`. Con WhisperX pero sin FFmpeg, el botón es **Completar la instalación**. El veredicto se vuelve a calcular al pulsar; nunca se toma del formulario.
- La instalación corre **en segundo plano** (sin ventana) y sigue aunque se cierre el asistente; mientras tanto el paso dice «Instalando WhisperX…». Deja su registro en `procesamiento\whisperx-install\` de la carpeta de datos (`estado.json`, `pip.log`, el zip de FFmpeg verificado) y un bloqueo impide dos instalaciones a la vez; el proceso espera unos segundos si la página está consultando el estado justo cuando arranca.
- Si falló o se interrumpió (PC apagado, sin internet), aparece **Reintentar la instalación**: pip continúa con lo que ya descargó. Si terminó pero la comprobación no encuentra WhisperX, lo dice así y el mismo botón lo repara.
- **`no_apto`**: lo dice claramente («no se instala WhisperX») y no ofrece instalar; lo de «Funciona siempre» sigue disponible.
- Con WhisperX ya instalado: «WhisperX ya está instalado».

El estado del paso (Hecho / Error) sigue siendo el de la comprobación; instalar WhisperX es opcional y no bloquea los demás pasos.

### 3. Canvas

La página explica cómo crear un token personal en Canvas:

1. Entrar a Canvas con el usuario de la universidad.
2. **Cuenta → Configuración**.
3. En **Integraciones aprobadas**, pulsar **+ Nuevo token de acceso**.
4. Propósito «Academic Manager» y una **fecha de vencimiento** (máximo 90 días) con su hora. Anotarlas.
5. **Generar token** y copiarlo: Canvas lo muestra una sola vez.
6. Pegarlo en el asistente junto con la misma fecha y hora de vencimiento.

El asistente comprueba el token con Canvas (lee el perfil de la cuenta) y lo guarda cifrado con DPAPI para el usuario de Windows, igual que `canvas_sync login`. Si ya había un token, solo se reemplaza por otro válido de la misma cuenta (como `renew`). El token nunca vuelve a aparecer en la página. Cuando vence, el paso marca **Error** y basta con repetir estos pasos.

### 4. Google Drive

Academic Manager lee los archivos de Drive con un **cliente de Google propio** de cada persona. Se crea una sola vez, gratis, en Google Cloud con la misma cuenta que usa en Drive:

1. En `console.cloud.google.com`, **Proyecto nuevo** «Academic Manager» → **Crear**.
2. **APIs y servicios → Biblioteca** → buscar **Google Drive API** → **Habilitar**.
3. **APIs y servicios → Pantalla de consentimiento de OAuth** (puede llamarse «Google Auth Platform») → **Comenzar**: nombre «Academic Manager», su correo, público **Externo**, aceptar y **Crear**.
4. **Público → Usuarios de prueba → Agregar usuarios**: su propio correo.
5. **Clientes** (o **Credenciales → Crear credenciales → ID de cliente de OAuth**): tipo **App de escritorio** → **Crear**.
6. **Descargar JSON** (un archivo `client_secret_….json`) y subirlo en el asistente. No enviarlo por chat ni correo.

Después, **Iniciar sesión con Google** abre Google en otra pestaña (el mismo inicio de sesión que `capture --login-drive`, `capture.sources.auth.login`, pero con `write=True`). Como la app es suya y está en prueba, Google avisa que no está verificada: se pulsa **Continuar** y se marcan todos los permisos: ver, descargar y **guardar** archivos de Drive (alcances `drive.readonly` y `drive`). Con ese único inicio de sesión Capture lee sus grabaciones y Academic Manager publica la base de datos en su Drive, sin cuenta de servicio. La autorización se guarda cifrada (DPAPI) en `control\drive-oauth\drive-token.dpapi` (la de Capture, de siempre) y `control\drive-oauth\drive-write-token.dpapi` (la de publicar). La página espera hasta 3 minutos.

El paso queda **Hecho** cuando hay sesión y con qué publicar: la sesión con permiso de escritura o, como en el PC de Diego, la cuenta de servicio `control\service-account-drive.json`. Una sesión antigua solo de lectura sin cuenta de servicio queda en **Falta** con «Su sesión de Google solo permite leer Drive»: basta **Volver a iniciar sesión**. Ver [03-almacenamiento-y-seguridad.md](03-almacenamiento-y-seguridad.md#identidad-para-publicar-la-base-en-drive).

**Caducidad a los 7 días.** Mientras la app de Google esté en modo de prueba, Google puede pedir iniciar sesión otra vez cada 7 días aproximadamente (según la política de Google, no verificado aquí). Cuando Capture diga que la autorización caducó, se abre el asistente y se pulsa **Volver a iniciar sesión**; no hace falta repetir lo de Google Cloud.

Si la cuenta es institucional, la universidad puede bloquear apps no verificadas; en ese caso el inicio de sesión falla con un mensaje y hay que usar una cuenta personal o pedir permiso.

### 5. Carpeta de Drive

La carpeta de Academic Manager dentro de Google Drive para escritorio, por ejemplo `G:\Mi unidad\Universidad\Academic Manager`. Debe existir en el PC (Google Drive para escritorio instalado y abierto). Se guarda como `drive_root` en el mismo archivo de configuración.

### 6. Materias

Muestra las materias activas de Canvas con casillas, y para cada una los bloques en que se dicta (1, 2 o ambos). Se escribe el semestre (por ejemplo `2027-1`) y se ve un **resumen** antes de guardar. Al **confirmar**, se usa el mismo alta que `canvas_sync onboard`; después se muestran los cambios propuestos para la base de datos de Drive y se pueden **publicar** una sola vez (el mismo protocolo que `enroll-publish`). Si no se publica (o la publicación falla), las materias quedan guardadas en el PC, el paso sigue en **Falta** con el aviso «pendientes de publicar» y el botón **Revisar y publicar** retoma la publicación; solo queda **Hecho** cuando la publicación terminó. Tras **Publicar**, el asistente reconstruye el índice de búsqueda de este PC con la misma función que `academic_knowledge build` (y que `complete` y la tarea diaria), para que la consulta de materias las vea enseguida; si falla, la publicación se mantiene y aparece un aviso con el botón **Reintentar índice**.

**Persona que empieza de cero.** Si la carpeta de datos no tiene base, ni estado de control, ni registro de materias, el paso empieza por **Crear la base de datos** (`academic_store.bootstrap.create`) y no consulta Canvas todavía. Antes pide los pasos 4 (sesión de Google con permiso para guardar) y 5 (carpeta de Drive). Al pulsarlo:

1. Busca en Drive la carpeta del paso 5, carpeta por carpeta desde «Mi unidad» (la ruta debe contener `Mi unidad` o `My Drive`).
2. Si esa carpeta ya tiene `Academic Manager - Database.sqlite`, `database-work-state.json`, `Academic Manager - Database.xlsx` o `database.xlsx`, se detiene y lo explica: no crea ni cambia nada.
3. Crea la base vacía (las siete hojas con sus encabezados V2.1, sin filas; el tablero solo lleva el título «Academic Manager»), la publica por primera vez en esa carpeta, la vuelve a descargar y compara el hash, crea el estado compartido libre con ese hash y lo relee.
4. Instala en el PC `academic-manager.sqlite` y `control\database-work-state.json`, como tras un corte, y `control\academic-operations.json` (carpeta de Drive y estado compartido) para que el coordinador pueda publicar y registrar clases. La carpeta del paso 5 debe llamarse «Academic Manager».

Después el paso muestra las materias de Canvas como a cualquier usuario: el primer alta crea el registro de materias con ese semestre (y el bloque más bajo elegido como bloque activo) y la publicación añade el semestre y las materias a la base. Si algo falla a mitad de camino (Drive no responde, la subida no coincide, falla la copia al PC), el paso queda en **Falta** con «quedó a medias» y el botón **Retomar la creación** continúa donde quedó, gracias al diario `procesamiento\bootstrap\journal.json`; cualquier negativa (carpeta de Drive con otra base, carpeta mal elegida, sin sesión con permiso) deja la carpeta de datos exactamente como estaba: reconoce por su identificador o su hash lo que ya subió, sin duplicar ni borrar. Ver [32-almacen-sqlite.md](32-almacen-sqlite.md#base-nueva-para-una-persona-nueva-bootstrap).

### 7. Sincronización diaria

Registra la tarea programada de Windows `AcademicManager-CanvasDaily` (08:00 y 18:00, con la sesión del usuario iniciada) para **esta copia** de Academic Manager, con el mismo `scripts\install-canvas-daily-task.ps1` de siempre: el asistente le pasa `-Repo` (la carpeta del código) y `-Python` (el Python con el que corre). La tarea ejecuta `scripts\canvas-daily.ps1` con esos valores, y la carpeta de datos (y la de sus registros, `procesamiento\canvas-sync\logs`) la decide el archivo de configuración a través de `academic_config.paths`. El estado se consulta al abrir el paso, igual que `academic_access system` (fila `tarea_diaria`); el resumen no consulta Windows. Una tarea que no existe (schtasks responde «El sistema no puede encontrar el archivo especificado» / «The system cannot find the file specified») se muestra como **Falta** («Todavía no está activada en este PC»); una tarea desactivada u otro problema se explica sin códigos ni textos de Windows. **Volver a activar** la registra de nuevo (por ejemplo tras cambiar la carpeta del programa). Fuera de Windows dice que solo funciona en Windows.

En el resumen aparece también la versión instalada (`academic_app/VERSION`).

Los mensajes de error de los módulos técnicos se muestran en lenguaje sencillo; de un error del sistema solo queda como «Detalle técnico» el tipo de error, nunca rutas internas ni datos.

## Seguridad

- Solo escucha en `127.0.0.1` (este PC), en un puerto libre elegido al azar. Rechaza cualquier otra dirección en la cabecera `Host` (protege contra *DNS rebinding*) y peticiones de otros sitios (`Origin`, `Sec-Fetch-Site`).
- Cada apertura genera una clave secreta nueva. El enlace inicial la lleva una vez; luego viaja en una cookie `HttpOnly` y `SameSite=Strict`. Sin la clave, todo responde 403. Cada formulario lleva además un token CSRF. Las comparaciones son en tiempo constante.
- Nunca muestra ni registra el token de Canvas, el secreto del cliente de Google ni los códigos de Google. El registro de la consola solo dice método, página y código de respuesta.
- El JSON del cliente se valida igual que en `capture --login-drive` (tipo Desktop, endpoints oficiales de Google) y se guarda solo en `control\drive-oauth\oauth-client.json` de la carpeta de datos, junto a la autorización de Drive, con permisos solo para el usuario. No se hacen copias temporales.
- Envíos de más de 64 KB se rechazan (413); un envío que no llega completo se abandona a los 15 segundos (408), así no deja el asistente colgado. Solo se aceptan GET y POST. Todo valor que se muestra se escapa. La página no usa JavaScript ni recursos externos (política CSP `default-src 'none'`).
- Se cierra con **Cerrar el asistente** o tras 30 minutos sin uso.
- «Cerrar el asistente» responde aunque otro paso siga en curso (por ejemplo, mientras el inicio de sesión de Google espera al navegador).
- **Riesgo aceptado:** los navegadores no separan las cookies por puerto, así que la cookie de sesión también se envía a otros servicios que el usuario abra en `127.0.0.1` mientras el asistente está abierto. Solo los programas del propio usuario en ese PC pueden recibirla, y esos ya pueden leer su carpeta de datos; la cookie deja de valer al cerrar el asistente o tras 30 minutos sin uso.

## Pendiente

- Para el uso diario de una persona nueva, después de publicar sus materias: `academic_knowledge build`, `summary`, `today` y `subjects` ya funcionan; `canvas_sync daily` necesita su primera sincronización en vivo (crea los snapshots), que hace la tarea del paso 7 en su primera ejecución.
- Instalar WhisperX y registrar la tarea no se han probado en un PC Windows real (ver la lista de [39-instalador-windows.md](39-instalador-windows.md#validar-en-un-pc-windows-real)).
- Validación en un PC Windows real (DPAPI, navegador predeterminado, Google Drive para escritorio).
