$ErrorActionPreference = 'Stop'

Set-Location 'D:\projects\SQLGenie\frontend'
$viteHost = if ($env:VITE_HOST) { $env:VITE_HOST } else { '127.0.0.1' }
$vitePort = if ($env:VITE_PORT) { $env:VITE_PORT } else { '5173' }

& 'C:\Users\T14P\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' `
  '.\node_modules\vite\bin\vite.js' --host $viteHost --port $vitePort *> 'D:\projects\SQLGenie\frontend.log'
