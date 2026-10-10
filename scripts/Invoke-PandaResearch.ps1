[CmdletBinding(PositionalBinding=$false)]
param(
    [string]$Python = '',
    [string]$Config = 'config/panda-alpha.local.json',
    [Parameter(Position=0, ValueFromRemainingArguments = $true)] [string[]]$ResearchArgs
)
$ErrorActionPreference = 'Stop'
# Preserve the Python process's exit code even if the caller enabled PowerShell
# 7 native-error promotion. This assignment remains local to the script scope.
$PSNativeCommandUseErrorActionPreference = $false
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dependencyPath = Join-Path $repoRoot '.runtime\python'
if (-not $Python) {
    $venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
    $posixVenvPython = Join-Path $repoRoot '.venv/bin/python'
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $Python = $venvPython
    } elseif (Test-Path -LiteralPath $posixVenvPython -PathType Leaf) {
        $Python = $posixVenvPython
    } else {
        $pathPython = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $pathPython) {
            throw 'Python is unavailable. Create .venv, add python to PATH, or pass -Python.'
        }
        $Python = $pathPython.Source
    }
}
$previousPythonPath = $env:PYTHONPATH
$previousPythonUtf8 = $env:PYTHONUTF8
$previousPythonEncoding = $env:PYTHONIOENCODING
$locationPushed = $false
$researchExitCode = 1
try {
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $importPaths = @($repoRoot)
    if (Test-Path -LiteralPath $dependencyPath -PathType Container) {
        $importPaths = @($dependencyPath) + $importPaths
    }
    if ($previousPythonPath) { $importPaths += $previousPythonPath }
    $env:PYTHONPATH = $importPaths -join [IO.Path]::PathSeparator
    Push-Location $repoRoot
    $locationPushed = $true
    & $Python -X utf8 -B -m panda_alpha --config $Config @ResearchArgs
    $researchExitCode = $LASTEXITCODE
} finally {
    if ($locationPushed) { Pop-Location }
    $env:PYTHONPATH = $previousPythonPath
    $env:PYTHONUTF8 = $previousPythonUtf8
    $env:PYTHONIOENCODING = $previousPythonEncoding
}
exit $researchExitCode
