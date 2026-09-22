param(
    [string]$Source = 'http://100.89.208.26:5001/video_feed',
    [int]$Port = 5003,
    [string]$HostAddress = '0.0.0.0'
)
$candidatePaths = @(
    (Join-Path $PSScriptRoot '.venv/Scripts/python.exe'),
    'C:/Users/lin40/Downloads/cloud/cloud/.venv/Scripts/python.exe'
)
$carePython = $candidatePaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $carePython) { throw 'Python environment with requirements-care.txt was not found.' }
& $carePython (Join-Path $PSScriptRoot 'run_homecare.py') --source $Source --port $Port --host $HostAddress
