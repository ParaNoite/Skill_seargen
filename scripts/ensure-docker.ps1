param(
    [int]$TimeoutSec = 120
)

$ErrorActionPreference = 'Stop'

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerCliCandidates = @(
    $(if ($dockerCommand) { $dockerCommand.Source }),
    (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'),
    (Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe')
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
$dockerCli = $dockerCliCandidates | Select-Object -First 1
$desktopCandidates = @(
    'C:\Program Files\Docker\Docker\Docker Desktop.exe',
    (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\frontend\Docker Desktop.exe'),
    (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe')
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

if (-not $dockerCli) {
    throw 'Docker CLI was not found. Install Docker Desktop, then rerun the supervisor.'
}

$ready = $false
try {
    & $dockerCli info --format '{{.ServerVersion}}' *> $null
    $ready = ($LASTEXITCODE -eq 0)
} catch {
    $ready = $false
}

if (-not $ready) {
    if (-not $desktopCandidates) {
        throw 'Docker CLI is installed, but Docker Desktop was not found. Start it manually, then retry.'
    }
    Start-Process -FilePath $desktopCandidates[0] -WindowStyle Hidden | Out-Null
}

$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
    & $dockerCli info --format '{{.ServerVersion}}' *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Output 'Docker Desktop is ready.'
        exit 0
    }
    Start-Sleep -Seconds 2
}

throw "Docker Desktop did not become ready within $TimeoutSec seconds. Check Docker Desktop and retry."
