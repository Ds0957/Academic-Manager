@echo off
setlocal
cd /d "%USERPROFILE%" || exit /b 1
if not exist "%USERPROFILE%\.desktop-commander-device\device.json" (
  echo [Academic Manager] No hay sesion registrada de Desktop Commander. Ejecutar manualmente npx.cmd para autorizarla.
  exit /b 2
)
if not exist "C:\Program Files\nodejs\npx.cmd" (
  echo [Academic Manager] Falta npx.cmd de Node.js.
  exit /b 3
)
if /I "%~1"=="--check" (
  echo REMOTE_LAUNCHER_OK
  exit /b 0
)
:reconnect
call "C:\Program Files\nodejs\npx.cmd" @wonderwhy-er/desktop-commander@latest remote
echo [Academic Manager] El agente ha terminado; reintento en 45 segundos. Codigo: %ERRORLEVEL%
powershell.exe -NoProfile -Command "Start-Sleep -Seconds 45"
goto reconnect
