param(
    [string]$Config = "config.json",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8766,
    [string]$Python = "",
    [string]$AssetsDir = "src\skill_gather\web_assets_school"
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

if (-not $Python -or -not (Test-Path $Python)) {
    Write-Error "Python was not found. Install Python, add it to PATH, or pass -Python <path-to-python.exe>."
}

if (-not (Test-Path -LiteralPath $Config -PathType Leaf)) {
    Write-Error "Config file was not found: $Config"
}

if (-not (Test-Path -LiteralPath $AssetsDir -PathType Container)) {
    Write-Error "School frontend assets were not found: $AssetsDir. Run: npm run frontend2:build"
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

Write-Host "AI University: http://$HostAddress`:$Port"
Write-Host "Frontend: school clone"
Write-Host "ASR: faster-whisper ($asrDevice/$asrComputeType)"
Write-Host "Python: $Python"
Write-Host "Config: $Config"
Write-Host "Press Ctrl+C to stop the Web server."
& $Python -m skill_gather web --config $Config --host $HostAddress --port $Port --assets-dir $AssetsDir
