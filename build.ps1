$ErrorActionPreference = "Stop"

$venvPython = ".\\.venv\\Scripts\\python.exe"

if (-not (Test-Path $venvPython)) {
    uv venv
}

uv pip install --python $venvPython pyinstaller pillow pystray
& $venvPython -m PyInstaller --noconfirm --onefile --name d-file-server server.py
& $venvPython -m PyInstaller --noconfirm --onefile --windowed --name d-file-server-gui --hidden-import pystray._win32 gui.py