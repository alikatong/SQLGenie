$ErrorActionPreference = 'Stop'

Set-Location 'D:\projects\SQLGenie'
$env:PYTHONPATH = 'D:\projects\SQLGenie\.python_packages'
$env:PYTHONIOENCODING = 'utf-8'
$appHost = if ($env:APP_HOST) { $env:APP_HOST } else { '127.0.0.1' }
$appPort = if ($env:APP_PORT) { $env:APP_PORT } else { '8000' }

$python = 'C:\Users\T14P\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$backendLog = 'D:\projects\SQLGenie\backend.log'
$backendErrLog = 'D:\projects\SQLGenie\backend.err.log'

# Start-Process keeps uvicorn detached and avoids PowerShell treating normal stderr logs as failures.
Start-Process `
  -FilePath $python `
  -ArgumentList @('-m', 'uvicorn', 'main:app', '--host', $appHost, '--port', $appPort) `
  -WorkingDirectory 'D:\projects\SQLGenie' `
  -RedirectStandardOutput $backendLog `
  -RedirectStandardError $backendErrLog `
  -WindowStyle Hidden
