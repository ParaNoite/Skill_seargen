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
$env:HF_HOME = Join-Path $ProjectRoot ".hf-cache"
$env:HF_ENDPOINT = "https://hf-mirror.com"
$env:HF_HUB_DISABLE_XET = "1"
$env:SKILL_GATHER_FASTER_WHISPER_DEVICE = "cuda"
$env:SKILL_GATHER_FASTER_WHISPER_COMPUTE_TYPE = "float16"
Write-Host "Video Skill Gather: http://$HostAddress`:$Port"
Write-Host "ASR: faster-whisper large-v3-turbo on CUDA float16"
Write-Host "Press Ctrl+C to stop the Web server."
& $Python -m skill_gather web --config $Config --host $HostAddress --port $Port
