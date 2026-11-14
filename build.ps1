# Builds dist\Training Export.exe (one file, no console window). Run from this folder:
#   powershell -ExecutionPolicy Bypass -File build.ps1
# Close the app first: a running Training Export.exe can't be replaced.
$ErrorActionPreference = "Stop"
if (-not (Test-Path .venv)) { py -3.12 -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt }
.venv\Scripts\python -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Host "Tests failed, nothing built."; exit 1 }
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "Training Export" `
    --add-data "training_export\ui;training_export\ui" `
    --collect-submodules keyring `
    run.py
if ($LASTEXITCODE -ne 0) { Write-Host "Build failed. Is Training Export still open? Close it and run this again."; exit 1 }
Write-Host "Built: dist\Training Export.exe"
