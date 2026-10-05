[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$MongoVersion = '8.0.32',
    [int]$Port = 27018,
    [switch]$InstallDependencies,
    [switch]$Start
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeRoot = Join-Path $repoRoot '.runtime'
$mongoRoot = Join-Path $runtimeRoot 'mongodb'
New-Item -ItemType Directory -Path $runtimeRoot,$mongoRoot -Force | Out-Null
$archiveName = "mongodb-windows-x86_64-$MongoVersion.zip"
$downloadUrl = "https://fastdl.mongodb.org/windows/$archiveName"
$zipPath = Join-Path $runtimeRoot $archiveName
$mongod = Get-ChildItem -LiteralPath $mongoRoot -Filter mongod.exe -Recurse | Select-Object -First 1
if (-not $mongod) {
    Invoke-WebRequest -Uri "$downloadUrl.sha256" -OutFile "$zipPath.sha256"
    $expectedHash = ((Get-Content -LiteralPath "$zipPath.sha256" -Raw).Trim() -split '\s+')[0]
    if ($expectedHash -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid official SHA256 response' }
    Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath
    if ((Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash -ne $expectedHash) { throw 'MongoDB archive hash mismatch' }
    Expand-Archive -LiteralPath $zipPath -DestinationPath $mongoRoot
    $mongod = Get-ChildItem -LiteralPath $mongoRoot -Filter mongod.exe -Recurse | Select-Object -First 1
    @{version=$MongoVersion; source=$downloadUrl; sha256=$expectedHash} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $mongoRoot 'download-manifest.json') -Encoding utf8
}
if ($InstallDependencies) {
    & $Python -m pip install --use-pep517 --target (Join-Path $runtimeRoot 'python') -r (Join-Path $repoRoot 'requirements-panda-alpha.txt')
    if ($LASTEXITCODE) { throw 'Dependency installation failed' }
}
if ($Start) {
    $dataPath = Join-Path $mongoRoot 'data'
    New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
    $logPath = Join-Path $mongoRoot 'mongod.log'
    $existingListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($existingListener) { throw "Port $Port already has a listener; inspect it before starting another database" }
    $mongoArgs = @('--dbpath',('"' + $dataPath + '"'),'--logpath',('"' + $logPath + '"'),'--logappend','--bind_ip','127.0.0.1','--port',$Port,'--wiredTigerCacheSizeGB','0.5')
    $process = Start-Process -FilePath $mongod.FullName -ArgumentList $mongoArgs -WindowStyle Hidden -PassThru
    @{pid=$process.Id; dbpath=$dataPath; port=$Port; executable=$mongod.FullName} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $mongoRoot 'process.json') -Encoding utf8
    Write-Output "MongoDB PID $($process.Id), mongodb://127.0.0.1:$Port, log $logPath"
}
