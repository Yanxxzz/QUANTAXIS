param([string]$MongoDirectory = '', [int]$Port = 27018)
$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path -Parent $PSScriptRoot
if (-not $MongoDirectory) { $MongoDirectory = Join-Path $taskRepo '.runtime\mongodb' }
$taskRuntime = (Resolve-Path -LiteralPath $MongoDirectory).Path
$taskData = (Resolve-Path -LiteralPath (Join-Path $taskRuntime 'data')).Path
$taskExecutable = @(Get-ChildItem -LiteralPath $taskRuntime -Recurse -Filter mongod.exe -File)
if ($taskExecutable.Count -ne 1) { throw 'Expected one installed MongoDB executable.' }
$taskListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($taskListener) { Write-Output "Mongo port $Port is already listening; no process started."; exit 0 }
$taskLog = Join-Path $taskRuntime 'mongod.log'
$taskProcess = Start-Process -FilePath $taskExecutable[0].FullName -WindowStyle Hidden -PassThru `
  -ArgumentList @('--dbpath', "`"$taskData`"", '--logpath', "`"$taskLog`"", '--logappend', '--port', "$Port", '--bind_ip', '127.0.0.1')
Write-Output "Started local MongoDB PID $($taskProcess.Id), port $Port, data $taskData"
