@echo off
setlocal EnableExtensions
call "%~dp0scripts\sqlgenie_runtime.cmd" export || exit /b 1
cd /d "%SQLGENIE_ROOT%"

echo [sqlGenie] Stopping service...

powershell -NoProfile -Command ^
  "$ports = @(%APP_PORT%, %VITE_PORT%); $stopped = New-Object System.Collections.Generic.List[int]; foreach($port in $ports){ $candidatePids = New-Object System.Collections.Generic.List[int]; $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue; foreach($conn in $conns){ $candidatePids.Add([int]$conn.OwningProcess) | Out-Null }; if($candidatePids.Count -eq 0){ $pattern = '^\s*TCP\s+\S+:' + $port + '\s+\S+\s+LISTENING\s+(\d+)\s*$'; foreach($line in (netstat -ano)){ if($line -match $pattern){ $candidatePids.Add([int]$Matches[1]) | Out-Null } } }; foreach($targetPid in $candidatePids){ if(-not $stopped.Contains($targetPid)){ try { Stop-Process -Id $targetPid -Force -ErrorAction Stop; $stopped.Add($targetPid) | Out-Null; Write-Output ('[sqlGenie] Stopped PID=' + $targetPid + ' (port ' + $port + ')') } catch { Write-Output ('[sqlGenie] Failed to stop PID=' + $targetPid + ': ' + $_.Exception.Message) } } } }; if($stopped.Count -eq 0){ Write-Output ('[sqlGenie] No listening service found on ' + ($ports -join '/')) }"

exit /b 0
