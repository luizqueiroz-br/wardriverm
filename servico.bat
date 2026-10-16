@echo off
REM ---------------------------------------------------------------------------
REM Wardrive Manager - sobe o servico em segundo plano (janela minimizada).
REM Uso:  servico.bat            (porta 5000)
REM       servico.bat 8080       (outra porta)
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set PORTA=%1
if "%PORTA%"=="" set PORTA=5000
set AQUI=%~dp0
if "%AQUI:~-1%"=="\" set AQUI=%AQUI:~0,-1%

if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente virtual e instalando dependencias...
  python -m venv .venv || goto :erro
  ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt || goto :erro
)

REM Ja esta no ar?
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://127.0.0.1:%PORTA%/healthz; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"
if not errorlevel 1 (
  echo O servico ja esta no ar em http://127.0.0.1:%PORTA%
  start "" "http://127.0.0.1:%PORTA%"
  goto :fim
)

if not exist "data" mkdir "data"

echo Subindo o Wardrive Manager na porta %PORTA%...
REM Start-Process destaca o processo de verdade (nao herda este console) e guarda a saida em data\servidor*.log
powershell -NoProfile -Command "$p = Start-Process -WindowStyle Minimized -WorkingDirectory '%AQUI%' -FilePath '%AQUI%\.venv\Scripts\python.exe' -ArgumentList 'cli.py','serve','--port','%PORTA%' -RedirectStandardOutput '%AQUI%\data\servidor.log' -RedirectStandardError '%AQUI%\data\servidor.err.log' -PassThru; Write-Output ('pid ' + $p.Id)"

REM Espera o servico responder (ate 25 segundos)
powershell -NoProfile -Command "$fim=(Get-Date).AddSeconds(25); while ((Get-Date) -lt $fim) { try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:%PORTA%/healthz; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Milliseconds 500 }; exit 1"
if errorlevel 1 (
  echo.
  echo Nao consegui confirmar a resposta do servico. Veja data\servidor.err.log
  goto :fim
)

echo.
echo Servico no ar em http://127.0.0.1:%PORTA%
echo Log: data\servidor.log  ^|  Para encerrar: parar.bat %PORTA%
goto :fim

:erro
echo.
echo Falha ao preparar o ambiente. Verifique se o Python 3.10+ esta instalado e no PATH.
pause

:fim
endlocal
