$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

$runtimePython = Join-Path $projectRoot '.runtime\Scripts\python.exe'
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$localDependencies = Join-Path $projectRoot '.codex-work\pydeps'
if (Test-Path -LiteralPath $runtimePython) {
    $pythonCommand = $runtimePython
} elseif (Test-Path -LiteralPath $localDependencies) {
    $env:PYTHONPATH = "$localDependencies;$projectRoot"
    $pythonCommand = 'python'
} elseif (Test-Path -LiteralPath $venvPython) {
    $pythonCommand = $venvPython
} else {
    Write-Host '未找到后端运行环境。请按照README.md完成Python 3.11环境安装。' -ForegroundColor Red
    exit 1
}

Write-Host 'UAV仿真平台正在启动：http://127.0.0.1:8000' -ForegroundColor Green
& $pythonCommand -m uav_sim.main
