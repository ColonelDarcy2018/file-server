# D Drive File Server

Simple LAN file server rooted at `D:\` with browse, preview, and download.

## Run

```powershell
python .\server.py
```

Then open:

```
http://<your-lan-ip>:8000
```

## Options

```powershell
python .\server.py --root D:\ --host 0.0.0.0 --port 8000 --max-preview-bytes 1000000
```

Notes:
- `--host 0.0.0.0` makes it reachable on the LAN.
- If Windows Firewall blocks access, allow inbound traffic on the chosen port.

## uv setup

1) Install uv (one-time):

```powershell
winget install --id Astral.Uv
```

2) Create and activate a virtual environment:

```powershell
uv venv
.\.venv\Scripts\activate
```

3) Run the server:

```powershell
python .\server.py
```

If you add dependencies later:

```powershell
uv pip install <package>
```

## Build exe (Windows)

```powershell
.\build.ps1
```

Output:

```
dist\d-file-server.exe
```

## Git tips

- Keep `.venv/`, `dist/`, and `build/` out of Git (see `.gitignore`).
- The repo normalizes text files via `.gitattributes`.
