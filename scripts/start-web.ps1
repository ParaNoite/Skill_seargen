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

if (-not (Test-Path -LiteralPath $Config -PathType Leaf)) {
    Write-Error "Config file was not found: $Config"
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:HF_HOME = Join-Path $ProjectRoot ".hf-cache"
$env:HF_ENDPOINT = "https://hf-mirror.com"
$env:HF_HUB_DISABLE_XET = "1"

$asrDevice = $env:SKILL_GATHER_FASTER_WHISPER_DEVICE
if ([string]::IsNullOrWhiteSpace($asrDevice)) {
    $nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($nvidiaSmi) {
        & $nvidiaSmi.Source -L *> $null
        $asrDevice = if ($LASTEXITCODE -eq 0) { "cuda" } else { "cpu" }
    } else {
        $asrDevice = "cpu"
    }
}
$asrComputeType = $env:SKILL_GATHER_FASTER_WHISPER_COMPUTE_TYPE
if ([string]::IsNullOrWhiteSpace($asrComputeType)) {
    $asrComputeType = if ($asrDevice -eq "cuda") { "float16" } elseif ($asrDevice -eq "cpu") { "int8" } else { "default" }
}
$env:SKILL_GATHER_FASTER_WHISPER_DEVICE = $asrDevice
$env:SKILL_GATHER_FASTER_WHISPER_COMPUTE_TYPE = $asrComputeType

Write-Host "Running local preflight..."
& $Python -c "import sys, faster_whisper, huggingface_hub; from skill_gather.config import load_config; load_config(sys.argv[1]); print('Preflight passed: config and local ASR dependencies are available')" $Config
if ($LASTEXITCODE -ne 0) {
    Write-Error "Local preflight failed. Run: .\.venv\Scripts\python.exe -m pip install -e ."
}

Write-Host "Video Skill Gather: http://$HostAddress`:$Port"
Write-Host "ASR: faster-whisper ($asrDevice/$asrComputeType)"
Write-Host "Python: $Python"
Write-Host "Config: $Config"
Write-Host "Press Ctrl+C to stop the Web server."
& $Python -m skill_gather web --config $Config --host $HostAddress --port $Port
