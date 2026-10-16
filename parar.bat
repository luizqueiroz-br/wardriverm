@echo off
REM ---------------------------------------------------------------------------
REM Wardrive Manager - encerra o servico em segundo plano.
REM Uso:  parar.bat            (porta 5000)
REM       parar.bat 8080       (outra porta)
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set PORTA=%1
if "%PORTA%"=="" set PORTA=5000

".venv\Scripts\python.exe" cli.py parar --port %PORTA%
endlocal
