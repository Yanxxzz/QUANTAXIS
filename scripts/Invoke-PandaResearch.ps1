[CmdletBinding(PositionalBinding=$false)]
param(
    [string]$Python = 'D:\Anaconda\envs\open-webui\python.exe',
    [string]$Config = 'config/panda-alpha.local.json',
    [Parameter(Position=0, ValueFromRemainingArguments = $true)] [string[]]$ResearchArgs
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dependencyPath = Join-Path $repoRoot '.runtime\python'
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONPATH = $dependencyPath + [IO.Path]::PathSeparator + $repoRoot + [IO.Path]::PathSeparator + $previousPythonPath
    Push-Location $repoRoot
    & $Python -X utf8 -B -m panda_alpha --config $Config @ResearchArgs
    $researchExitCode = $LASTEXITCODE
    Pop-Location
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
exit $researchExitCode
