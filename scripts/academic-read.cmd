@echo off
setlocal
cd /d "%~dp0.." || exit /b 1
set "AM_DATA=C:\AcademicManagerData"
if defined ACADEMIC_MANAGER_DATA set "AM_DATA=%ACADEMIC_MANAGER_DATA%"
set "PY=%AM_DATA%\procesamiento\capture-env\Scripts\python.exe"
if not exist "%PY%" set "PY=C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe"
if not exist "%PY%" (echo {"ok":false,"error":{"code":"PYTHON_UNAVAILABLE"}} & exit /b 2)
"%PY%" -m academic_access %*
exit /b %errorlevel%
