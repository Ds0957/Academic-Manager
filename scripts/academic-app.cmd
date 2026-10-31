@echo off
setlocal
cd /d "%~dp0.." || exit /b 1
set "AM_DATA=C:\AcademicManagerData"
if defined ACADEMIC_MANAGER_DATA set "AM_DATA=%ACADEMIC_MANAGER_DATA%"
set "PY=%AM_DATA%\procesamiento\capture-env\Scripts\python.exe"
if not exist "%PY%" set "PY=C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe"
if exist "%PY%" goto run
rem Usuario nuevo sin entorno de Capture: el asistente funciona con cualquier Python 3.
where python >nul 2>nul || (echo Falta Python. Instale Academic Manager con su instalador. & exit /b 1)
set "PY=python"
:run
"%PY%" -m academic_app %*
exit /b %errorlevel%
