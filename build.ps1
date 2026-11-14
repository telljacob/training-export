# Builds dist\Training Export.exe (one file, no console window). Run from this folder:
#   powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
if (-not (Test-Path .venv)) { py -3.12 -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt }
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "Training Export" `
    --add-data "training_export\ui;training_export\ui" `
    --collect-submodules keyring `
    run.py
Write-Host "Built: dist\Training Export.exe"
