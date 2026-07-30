param(
    [string]$Config = "config.json",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8765,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

if (-not $Python) {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $Python = $venvPython
    }
}

if (-not $Python) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $Python = $pythonCommand.Source
    }
}

if (-not $Python) {
    $codexPython = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $codexPython) {
        $Python = $codexPython
    }
}

if (-not $Python -or -not (Test-Path $Python)) {
    Write-Error "Python was not found. Install Python, add it to PATH, or pass -Python <path-to-python.exe>."
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
if (-not $env:HF_HOME) {
    $env:HF_HOME = Join-Path $ProjectRoot ".hf-cache"
}
if (-not $env:HF_ENDPOINT) {
    $env:HF_ENDPOINT = "https://hf-mirror.com"
}
if (-not $env:HF_HUB_DISABLE_XET) {
    $env:HF_HUB_DISABLE_XET = "1"
}

Write-Host "Video Skill Gather dev server"
Write-Host "URL: http://$HostAddress`:$Port"
Write-Host "Config: $Config"
Write-Host "Python: $Python"
Write-Host "Press Ctrl+C to stop."
& $Python -m skill_gather web --config $Config --host $HostAddress --port $Port
