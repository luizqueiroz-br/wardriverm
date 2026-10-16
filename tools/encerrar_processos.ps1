# Encerra apenas os processos do Wardrive Manager (servidores, ferramentas de
# teste e janelas do servico.bat). Nada fora de wardrive-manager e tocado.
$eu = $PID
$alvos = Get-CimInstance Win32_Process |
    Where-Object {
        $_.ProcessId -ne $eu -and $_.CommandLine -and
        ($_.CommandLine -match 'wardrive-manager' -or $_.CommandLine -match 'servidor_mentira') -and
        $_.CommandLine -notmatch 'encerrar_processos'
    }

if (-not $alvos) {
    Write-Output 'nenhum processo do Wardrive Manager em execucao'
    exit 0
}

foreach ($p in $alvos) {
    $linha = $p.CommandLine
    if ($linha.Length -gt 120) { $linha = $linha.Substring(0, 120) }
    Write-Output ("encerrando pid " + $p.ProcessId + " [" + $p.Name + "] " + $linha)
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
    } catch {
        Write-Output ("  falhou: " + $_.Exception.Message)
    }
}

Start-Sleep -Seconds 2
$restantes = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'wardrive-manager' }
Write-Output ("restantes: " + @($restantes).Count)
