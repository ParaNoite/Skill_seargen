$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot

$candidates = @(
    (Get-Command opencode -CommandType Application -ErrorAction SilentlyContinue).Source,
    (Join-Path $env:APPDATA 'npm\opencode.cmd'),
    (Join-Path $env:LOCALAPPDATA 'opencode\opencode.exe'),
    (Join-Path $env:USERPROFILE '.local\bin\opencode.exe')
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique

if (-not $candidates) {
    throw '未找到 OpenCode。请先安装 OpenCode，或确认它能在正常 PowerShell 中通过 opencode 启动。'
}

$launcher = $candidates[0]
Write-Host "OpenCode: $launcher"
Write-Host "工作目录: $repoRoot"

if ($launcher -like '*.cmd') {
    & cmd.exe /d /c $launcher @args
} else {
    & $launcher @args
}
exit $LASTEXITCODE
