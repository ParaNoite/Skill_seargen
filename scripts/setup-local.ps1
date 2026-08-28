param(
    [string]$Config = "config.json"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Write-Error "Project virtual environment was not found: $Python"
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

Write-Host "Installing project dependencies into .venv..."
& $Python -m pip install -e .
if ($LASTEXITCODE -ne 0) {
    Write-Error "Dependency installation failed."
}

Write-Host "Downloading and validating the configured ASR model..."
Write-Host "ASR: faster-whisper ($asrDevice/$asrComputeType)"
& $Python -c "import sys; from faster_whisper import WhisperModel; from skill_gather.config import load_config; from skill_gather.integrations.faster_whisper import FasterWhisperClient, _configure_hugging_face_environment; cfg=load_config(sys.argv[1]); client=FasterWhisperClient.from_model(cfg.newapi.asr_model); cache=_configure_hugging_face_environment(); WhisperModel(client.model_name, device=client.device, compute_type=client.compute_type, download_root=str(cache)); print(f'Local ASR ready: {client.model_name} ({client.device}/{client.compute_type})')" $Config
if ($LASTEXITCODE -ne 0) {
    Write-Error "ASR model setup failed. Check the CUDA driver and network connection."
}

Write-Host "Local setup complete. Start with: npm start"
