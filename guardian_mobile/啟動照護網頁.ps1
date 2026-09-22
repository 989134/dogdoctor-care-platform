param(
    [string]$HostAddress = '127.0.0.1',
    [int]$Port = 5016,
    [string]$PublicUrl = 'https://evelyn.tail62a4b1.ts.net'
)
$ErrorActionPreference = 'Stop'
$guardianPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $guardianPython)) {
    throw '找不到手機端專用 Python 環境，請先執行：python -m venv .venv；.\.venv\Scripts\python.exe -m pip install -r requirements.txt'
}
$sourceDir = Join-Path $PSScriptRoot '..\..\homecloud\mec_output'
$dataDir = Join-Path $PSScriptRoot 'guardian_data'
$homecareEnv = Join-Path $PSScriptRoot '..\..\homecloud\.env'
if (Test-Path -LiteralPath $homecareEnv) {
    $geminiLine = Get-Content -LiteralPath $homecareEnv -Encoding UTF8 |
        Where-Object { $_ -match '^\s*GEMINI_API_KEY=' } |
        Select-Object -Last 1
    if ($geminiLine) {
        $geminiValue = $geminiLine.Substring($geminiLine.IndexOf('=') + 1).Trim()
        if ($geminiValue.Length -ge 20 -and $geminiValue -notmatch '\s') {
            $env:GEMINI_API_KEY = $geminiValue
            $env:GEMINI_MODEL = 'gemini-flash-lite-latest'
            Write-Host 'Gemini：已套用電腦照護端的共用 API Key（內容不顯示）。'
        } else {
            throw 'homecloud\.env 中的 GEMINI_API_KEY 格式不合理。'
        }
    }
}
$env:GUARDIAN_PUBLIC_URL = $PublicUrl
Write-Host "手機監護者前端：http://${HostAddress}:$Port"
Write-Host "Tailscale 私有 HTTPS：$PublicUrl"
Write-Host '帳號、藥品及事件處理紀錄儲存於 guardian_data。此程式不建立示範帳號或示範事件。'
Write-Host "照護事件以唯讀方式讀取：$sourceDir"
Write-Host '請保持此程式及電腦運作，才能持續計時與傳送通知。Ctrl+C 停止服務。'
try {
    & $guardianPython (Join-Path $PSScriptRoot 'run_guardian.py') --host $HostAddress --port $Port --data-dir $dataDir --source-dir $sourceDir
} finally {
    Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:GEMINI_MODEL -ErrorAction SilentlyContinue
}
