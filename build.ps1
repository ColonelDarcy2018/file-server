$ErrorActionPreference = "Stop"

$venvPython = ".\\.venv\\Scripts\\python.exe"
$pyinstaller = ".\\.venv\\Scripts\\pyinstaller.exe"

if (-not (Test-Path $venvPython)) {
    uv venv
}

uv pip install --python $venvPython pyinstaller
& $pyinstaller --onefile --name d-file-server server.py
