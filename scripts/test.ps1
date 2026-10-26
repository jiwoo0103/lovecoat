$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$testRoot = Join-Path $projectRoot ('.local\tests-' + [guid]::NewGuid().ToString('N'))
& .\.venv\Scripts\python.exe -m pytest -q "--basetemp=$testRoot"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe -m ruff check coatdesk coatdesk_v2 tests scripts run_app.py run_v2.py
exit $LASTEXITCODE
