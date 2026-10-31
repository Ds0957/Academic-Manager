# 39 — Instalador de Windows

**Estado:** construido y probado con pruebas estáticas y simuladas en WSL; **pendiente de compilar y probar en un PC Windows real** (ver [la lista](#validar-en-un-pc-windows-real)). Fase 1 (issue #20), tarea T4.

`AcademicManager-Setup-<versión>.exe` instala Academic Manager con su propio Python, sin permisos de administrador, y crea el acceso directo **Academic Manager**, que abre el [asistente de configuración](38-asistente-configuracion.md). Cada persona lleva lo suyo: el programa y sus datos están separados.

## Para quien lo usa

### Descargar

La versión nueva está en la página **Releases** del repositorio en GitHub, como archivo `AcademicManager-Setup-<versión>.exe`. Mientras el repositorio sea privado hace falta iniciar sesión en GitHub con una cuenta que tenga acceso (ver [Acceso al repositorio](#acceso-al-repositorio-decisión-pendiente)).

### Instalar

1. Doble clic en `AcademicManager-Setup-<versión>.exe`. No pide contraseña de administrador.
2. El instalador no está firmado digitalmente, así que Windows avisa (decisión: «Sin firma por ahora»). Es normal:
   - **Al descargar en Edge** puede aparecer «AcademicManager-Setup… no se descarga habitualmente». Pase el ratón sobre la descarga, pulse **…** (Más acciones) → **Conservar**; si pregunta otra vez, **Mostrar más** → **Conservar de todas formas**.
   - **Al abrirlo** aparece una ventana azul **«Windows protegió su PC»** con el texto «Microsoft Defender SmartScreen impidió el inicio de una aplicación no reconocida…» y un solo botón, **No ejecutar**. No lo pulse: pulse el enlace **Más información**, debajo del texto. La ventana muestra entonces «Aplicación: AcademicManager-Setup-<versión>.exe» y «Editor: Editor desconocido», y aparece el botón **Ejecutar de todas formas**: púlselo.
3. Marque si quiere el acceso directo en el escritorio y pulse **Instalar**. Al terminar puede abrir Academic Manager directamente.

El programa queda en `%LOCALAPPDATA%\Programs\AcademicManager` (solo para su usuario de Windows): `python\` (Python 3.12 portátil con sus componentes) y `app\` (el código).

### Primera apertura

Doble clic en **Academic Manager** (menú Inicio o escritorio). Se abre una ventana negra y, enseguida, el asistente en el navegador. No cierre la ventana negra mientras usa el asistente: si el navegador no se abre solo, ahí aparece la dirección para copiarla. Siga los pasos:

1. **Carpeta de datos** — donde se guardan sus datos (recomendado `C:\AcademicManagerData`). El asistente no acepta una carpeta dentro de la del programa (`%LOCALAPPDATA%\Programs\AcademicManager`), porque esa se reemplaza al actualizar y se borra al desinstalar.
2. **Comprobación del equipo** — dice si el PC puede transcribir clases grabadas. Si es apto, ofrece **Instalar WhisperX**, que instala también **FFmpeg** (el programa que lee el audio); necesita internet y tarda, y sigue aunque cierre el asistente. Si no es apto, lo dice y no descarga nada: YouTube con subtítulos e importar transcripciones funcionan igual.
3. **Canvas**, 4. **Google Drive**, 5. **Carpeta de Drive**, 6. **Materias** — como en [38-asistente-configuracion.md](38-asistente-configuracion.md).
7. **Sincronización diaria** — **Activar la sincronización diaria** crea la tarea de Windows que revisa Canvas a las 08:00 y a las 18:00 con el PC encendido y la sesión iniciada.

### Actualizar

Al abrir el asistente, Academic Manager pregunta en segundo plano a GitHub cuál es la última versión publicada (la release más reciente de `Ds0957/Academic-Manager`). La primera página no espera esa respuesta.

- Si hay una versión más nueva, el resumen muestra arriba un aviso con la versión nueva y la que tiene, y el botón **Actualizar ahora**.
- Al pulsarlo se descarga el instalador `AcademicManager-Setup-<versión>.exe` (solo por HTTPS desde GitHub) a una carpeta temporal (`%TEMP%\AcademicManager-actualizacion`) y se comprueba su huella SHA-256 contra la publicada en la release. Si coincide, el asistente se cierra y se abre el instalador normal: pulse **Siguiente** hasta terminar; al acabar, Academic Manager se abre solo.
- Si la huella no coincide, falta o la descarga falla, el archivo se borra, **no se ejecuta nada** y el resumen explica el problema en palabras sencillas.
- Sin internet, con GitHub lento (más de 5 segundos) o con una respuesta rara, no aparece ningún aviso y todo funciona igual.

También puede instalar la versión nueva encima a mano: se reemplaza el código, se conservan los datos, la configuración y WhisperX si ya estaba instalado.

### Desinstalar

**Configuración → Aplicaciones → Academic Manager → Desinstalar**. La propia página de desinstalación lo recuerda: **sus datos no se borran**. Se conservan la carpeta de datos (la que eligió en el paso 1) y la configuración `%APPDATA%\AcademicManager\config.json`. Solo se borran `python\` y `app\` de la carpeta del programa (incluidos WhisperX y FFmpeg). Si había activado la sincronización diaria, el desinstalador quita la tarea `AcademicManager-CanvasDaily` **solo si su acción usa el Python de esta copia** (decisión: «Quitarla si apunta a esta copia»); una tarea que apunta a otra instalación, como la de desarrollo en `C:\AcademicManagerDev`, no se toca.

## Para Diego

### Qué hay en el repositorio

| Archivo | Qué es |
|---|---|
| `academic_app/VERSION` | Única fuente de la versión: la leen el asistente (`academic_app.version()`) y el `.iss`. La etiqueta de la release debe ser `v<VERSION>`. |
| `installer/pins.json` | Descargas fijadas con versión exacta y SHA-256: Python embeddable 3.12.10 x64, el wheel de pip 26.2.2 (en lugar de `get-pip.py`, que no tiene versión) e Inno Setup 6.7.3. Cada entrada dice de dónde sale su hash. |
| `requirements-app.txt` | Dependencias exactas de la app instalada (asistente, Canvas Sync, Drive, Capture sin WhisperX): los mismos `pypdf` y `yt-dlp` que `requirements-canvas.txt`/`requirements-capture.txt` y versiones exactas de Google dentro de los rangos de `requirements-drive.txt`, más `tzdata` (Windows no trae zonas horarias y `America/Bogota` las necesita). |
| `academic_setup/whisperx.py` | Versiones de WhisperX (`whisperx==3.8.6`) y de PyTorch (`2.8.0`, índices `cu128` y `cpu`) que instala el asistente, y FFmpeg 9.0.2 «essentials» de gyan.dev (espejo de GitHub `GyanD/codexffmpeg`, SHA-256 `60f46726…fa2047ba`, el mismo que publica gyan.dev en `<archivo>.sha256` y el digest del asset). `ffmpeg.exe` y `ffprobe.exe` quedan junto a `python.exe` (`{app}\python`): Windows busca primero ahí al lanzar `ffmpeg` sin ruta, así que Capture y WhisperX lo encuentran sin cambiar el PATH, y la comprobación del equipo lo detecta. Una huella distinta descarta la descarga. |
| `installer/remove-daily-task.ps1` | Lo ejecuta el desinstalador: quita la tarea diaria solo si todas sus acciones llevan `-Python "<este python.exe>"`; siempre termina con código 0. |
| `installer/build.ps1` | Construcción en Windows: descarga y verifica los SHA-256 (un hash ausente o distinto detiene todo), prepara el Python portátil, instala `requirements-app.txt`, copia el código y compila. |
| `installer/stage.py` | Ayudante en Python: activa `Lib\site-packages` y `..\app` en el `python312._pth` y copia solo los paquetes que usa la app (sin `tests/`, `odd/`, `docs/`, `.git`, ni los `.cmd` de la instalación de desarrollo), y de ellos solo los archivos con seguimiento de git (`git ls-files`): nada local sin seguimiento viaja en el instalador. |
| `installer/academic-manager.iss` | Script de Inno Setup: por usuario (`PrivilegesRequired=lowest`), en español, accesos directos a `{app}\python\python.exe -m academic_app`, desinstalación que solo borra `{app}\python` y `{app}\app`. |
| `.github/workflows/build-installer.yml` | CI en `windows-latest`. |

El Python portátil ignora `PYTHONPATH` y la carpeta actual: encuentra el código por `..\app` en su `._pth`.

### Construir en tu PC Windows

Desde la carpeta del repositorio, en PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File installer\build.ps1 -InstallInnoSetup
```

`-InstallInnoSetup` instala el Inno Setup fijado (verificado) dentro de la carpeta de construcción, sin administrador; sin esa opción usa un Inno Setup ya instalado o el `ISCC.exe` de `-Iscc <ruta>`, que debe ser **6.3 o posterior** (el `.iss` usa `x64compatible`; el propio `.iss` comprueba la versión del compilador y se detiene si es anterior). La construcción usa `%TEMP%\AcademicManager-build` (otra con `-BuildDir`) y al final escribe la ruta del `.exe` (`...\out\AcademicManager-Setup-<versión>.exe`). Necesita internet.

### Construir con GitHub Actions

**Actions → build-installer → Run workflow** (rama elegida). Al terminar, el instalador está en el artefacto **AcademicManager-Setup** de esa ejecución (30 días). Permisos: solo lectura; ningún secreto. Una ejecución manual nunca crea ni cambia releases, aunque se elija una etiqueta.

### Publicar una versión

1. Cambia `academic_app/VERSION` (por ejemplo `0.2.0`) y fusiona en `main`.
2. Crea y empuja la etiqueta: `git tag v0.2.0` y `git push origin v0.2.0`.
3. El workflow comprueba que la etiqueta coincide con `VERSION`, construye y adjunta el `.exe` a la release de esa etiqueta; si no existe, la crea **como borrador**. Solo ese paso tiene `contents: write`.
4. Revisa el borrador en GitHub y publícalo tú: publicar sigue siendo una decisión humana.

Volver a ejecutar el workflow de una etiqueta reemplaza el `.exe` adjunto (`--clobber`) **solo mientras la release es borrador**; si ya está publicada, el job falla con «ya está publicada: no se reemplaza su instalador» y hay que sacar una versión nueva.

### Actualizar las versiones fijadas

- Python: cambia `version`, `url` y `sha256` en `installer/pins.json` con el hash publicado por python.org (el SBOM `<archivo>.spdx.json` trae el SHA-256 del paquete). Si no puedes verificarlo, deja un valor que no sea un hash: `build.ps1` se detiene.
- pip e Inno Setup: igual, con el `sha256` de PyPI y el digest del asset de la release de GitHub.
- Dependencias: edita `requirements-app.txt` (las pruebas exigen versiones exactas y coherentes con los otros `requirements-*.txt`).
- Se verifican por hash las descargas de `pins.json` y FFmpeg; las dependencias de pip y las de WhisperX se fijan por versión (sin `--require-hashes`).

### Tarea diaria

`scripts\canvas-daily.ps1` y `scripts\install-canvas-daily-task.ps1` aceptan `-Repo`, `-Python` y `-Runtime`. **Sin parámetros hacen exactamente lo de antes** (repo `C:\AcademicManagerDev\Academic-Manager`, Python `C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe`, registros en `C:\AcademicManagerData\procesamiento\canvas-sync\logs`, misma acción de la tarea). Con `-Repo`/`-Python` (lo que pasa el paso 7 del asistente) la tarea reenvía esos valores y `canvas-daily.ps1` pregunta la carpeta de datos a `academic_config.paths` con ese Python (variable `ACADEMIC_MANAGER_DATA`, luego `config.json`, luego `C:\AcademicManagerData`); `-Runtime` la fija a mano. El nombre de la tarea sigue siendo `AcademicManager-CanvasDaily`, el que comprueba `academic_access system`. Si `config.json` es inválido, la ejecución falla antes de escribir el registro (código distinto de cero en el historial de la tarea).

### Acceso al repositorio (decisión pendiente)

Para descargar una release de un repositorio privado hace falta acceso. Opciones:

- **Invitarla como colaboradora** (Settings → Collaborators → Add people). En un repositorio de cuenta personal, una colaboradora tiene permiso de escritura (no hay rol de solo lectura); necesita su propia cuenta de GitHub y entrar con ella para descargar.
- **Hacer público el repositorio**: descarga sin cuenta, pero todo el código y el historial quedan visibles (revisa antes que no haya datos personales en el historial).

Se decide al publicar la primera release. El aviso de versión nueva dentro de la app consulta `releases/latest` de `Ds0957/Academic-Manager` sin iniciar sesión, así que necesita que ese repositorio sea público; la release debe llevar el asset `AcademicManager-Setup-<VERSION>.exe` (GitHub publica su `digest` SHA-256) y la etiqueta `v<VERSION>`.

### Validar en un PC Windows real

Nada de esto se pudo probar sin Windows:

- [ ] `installer\build.ps1 -InstallInnoSetup` termina (descargas, hashes, pip en el Python portátil, `academic_app --help`) y el `.iss` compila con Inno Setup 6.7.3 (ISPP: lectura de `VERSION`, `StageDir`).
- [ ] El workflow corre en `windows-latest` y una etiqueta de prueba crea la release en borrador con el `.exe`.
- [ ] Instalar sin administrador en una cuenta estándar: carpeta `%LOCALAPPDATA%\Programs\AcademicManager`, accesos directos del menú Inicio y del escritorio, textos en español, SmartScreen.
- [ ] El acceso directo abre el asistente en el navegador predeterminado; la ventana negra muestra la dirección.
- [ ] Paso 1 rechaza una carpeta dentro de `%LOCALAPPDATA%\Programs\AcademicManager` (también escrita con otras mayúsculas).
- [ ] Paso 2 en un PC sin GPU (`solo_cpu`): **Instalar WhisperX** instala PyTorch CPU, WhisperX y FFmpeg en `{app}\python`, sigue al cerrar el asistente, el paso dice «Instalando…» y después «ya está instalado»; un corte de red deja **Reintentar**. En un PC con NVIDIA (`apto_gpu`): PyTorch `cu128`. Con `no_apto`: no se ofrece.
- [ ] WhisperX transcribe de verdad con el Python portátil y Capture encuentra `ffmpeg`/`ffprobe` junto a `python.exe` (incluida la tarea diaria).
- [ ] Paso 7 en un PC nuevo dice «Todavía no está activada» (schtasks: «El sistema no puede encontrar el archivo especificado»); crea la tarea con `-Repo`/`-Python` de la copia instalada; una ejecución manual (`schtasks /Run /TN AcademicManager-CanvasDaily`) escribe el registro en la carpeta de datos de `config.json`; `ConvertFrom-Json` de Windows PowerShell 5.1 lee la ruta (también con tildes).
- [ ] En el PC de Diego, `install-canvas-daily-task.ps1` sin parámetros deja la tarea igual que antes (acción y argumentos).
- [ ] Actualizar encima conserva datos, configuración y WhisperX; desinstalar muestra el aviso, conserva la carpeta de datos y `config.json`, borra `python\` y `app\`, quita la tarea de esta copia y deja intacta una tarea que apunta a `C:\AcademicManagerDev`.
- [ ] El aviso de SmartScreen y de Edge se ven como se describen en [Instalar](#instalar).
- [ ] Con una release publicada más nueva que la instalada, el resumen muestra el aviso; **Actualizar ahora** descarga a `%TEMP%\AcademicManager-actualizacion`, abre el instalador (con su aviso de SmartScreen), el asistente se cierra, la instalación encima termina y la app se abre con la versión nueva conservando datos y configuración.
- [ ] Sin internet (por ejemplo, con el Wi-Fi apagado) el asistente abre igual de rápido y sin aviso.

### Decisiones tomadas

- FFmpeg: «Junto con WhisperX» — se descarga (versión y huella fijadas) solo si el equipo es apto y se instala WhisperX.
- Desinstalar: «Quitarla si apunta a esta copia» — la tarea diaria se borra solo si su acción usa el Python instalado.
- Firma: «Sin firma por ahora» — el aviso de SmartScreen se explica en [Instalar](#instalar).
