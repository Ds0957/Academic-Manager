@echo off
setlocal
cd /d "%~dp0.." || exit /b 1
set "AM_DATA=C:\AcademicManagerData"
if defined ACADEMIC_MANAGER_DATA set "AM_DATA=%ACADEMIC_MANAGER_DATA%"
set "PY=%AM_DATA%\procesamiento\capture-env\Scripts\python.exe"
if not exist "%PY%" set "PY=C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe"
if not exist "%PY%" (echo Falta el Python de Academic Manager. & exit /b 1)
if "%~1"=="" (echo Uso: scripts\canvas-sync.cmd daily^|reconcile-preview^|sync-rich^|sync-study^|status^|login & exit /b 2)
"%PY%" -m canvas_sync %*
exit /b %errorlevel%
