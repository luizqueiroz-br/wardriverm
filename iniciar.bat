@echo off
REM ---------------------------------------------------------------------------
REM Wardrive Manager - inicia a interface web e abre o navegador.
REM Basta dar dois cliques neste arquivo.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente virtual...
  python -m venv .venv || goto :erro
  ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt || goto :erro
)

set PORTA=5000
echo Iniciando %~nx0 em http://127.0.0.1:%PORTA%
start "" "http://127.0.0.1:%PORTA%"
".venv\Scripts\python.exe" cli.py serve --port %PORTA%
goto :fim

:erro
echo.
echo Falha ao preparar o ambiente. Verifique se o Python 3.10+ esta instalado e no PATH.
pause

:fim
endlocal
