$ErrorActionPreference = 'Stop'
$demoPython = Join-Path $PSScriptRoot '..\.venv-test\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $demoPython)) {
    throw '找不到 Demo Python 環境，請先建立 .venv-test 並安裝 requirements.txt。'
}
Write-Host '互動 Demo：http://127.0.0.1:5017 （Ctrl+C 停止）'
& $demoPython (Join-Path $PSScriptRoot 'run_demo.py')
