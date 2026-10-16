# Lista o que ainda referencia o servidor do Wardrive Manager (nome, pid e linha de comando).
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'cli\.py serve|wardrive-manager' } |
    ForEach-Object {
        $linha = $_.CommandLine
        if ($linha.Length -gt 140) { $linha = $linha.Substring(0, 140) }
        Write-Output ($_.ProcessId.ToString() + " [" + $_.Name + "] " + $linha)
    }
