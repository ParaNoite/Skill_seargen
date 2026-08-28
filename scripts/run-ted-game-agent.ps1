[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Task,
    [string] $Config = "configs/skill-gather.example.json",
    [string] $Model = "claude-haiku-4-5-20251001"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "未找到仓库 .venv Python" }

$env:PYTHONPATH = Join-Path $repoRoot "src"
$env:TED_GAME_TASK = $Task
$env:TED_GAME_CONFIG = $Config
$env:TED_GAME_MODEL = $Model
& $python -c "import os; from skill_gather.game_hatch import run_game_hatch; result=run_game_hatch(os.environ['TED_GAME_TASK'], config_path=os.environ['TED_GAME_CONFIG'], model=os.environ['TED_GAME_MODEL']); print(result.final_output); print(result.report_path)"
exit $LASTEXITCODE
