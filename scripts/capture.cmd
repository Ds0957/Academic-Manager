@echo off
setlocal
cd /d "%~dp0.." || exit /b 1
set "AM_DATA=C:\AcademicManagerData"
if defined ACADEMIC_MANAGER_DATA set "AM_DATA=%ACADEMIC_MANAGER_DATA%"
set "PY=%AM_DATA%\procesamiento\capture-env\Scripts\python.exe"
if not exist "%PY%" set "PY=C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe"
if not exist "%PY%" (echo Falta el Python local de Academic Manager. & exit /b 1)
rem deno, instalado por requirements-capture.txt, debe estar en PATH para yt-dlp.
for %%I in ("%PY%") do set "PATH=%%~dpI;%PATH%"
if "%~1"=="" (echo Uso: scripts\capture.cmd --source "https://www.youtube.com/watch?v=ID" --tipo material --subject-id MAT-SER --semester 2026-2 --subject "SER (BE-IT)" & exit /b 2)
"%PY%" -m capture %*
exit /b %errorlevel%
