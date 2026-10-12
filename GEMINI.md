# Workspace Rule: Mandatory WSL Development Environment

## Directive
- **Strictly No Windows Development**: Never run Python, package managers, FFmpeg, Node, or application scripts natively in Windows PowerShell or CMD.
- **Always Use WSL 2**: All command execution, testing, dependency management, and background services must run inside the Ubuntu WSL 2 distribution using `wsl bash -lc "<command>"` or direct WSL execution.
- **Filesystem Mapping**:
  - Project Workspace: `c:\Users\Patri\Documents\captioning_pipeline` maps to `/mnt/c/Users/Patri/Documents/captioning_pipeline` inside WSL.
  - Video Source Directory: `C:\Users\Patri\Videos\_girls` maps to `/mnt/c/Users/Patri/Videos/_girls` inside WSL.
- **Python & Environment Standard**:
  - Use `uv` inside WSL for Python management. The virtual environment lives on WSL ext4 at
    `~/.venvs/captioning_pipeline` (set via `UV_PROJECT_ENVIRONMENT`, see `scripts/env.sh`), never on `/mnt/c`.
  - Caches, SQLite state, previews and proxies live at `~/.cache/captioning_pipeline`. Only source code
    and the finished dataset live on `/mnt/c`.
  - Run project commands via the scripts in `scripts/` to avoid PowerShell to bash quoting issues.
  - Target Linux Python (Python 3.11 / 3.12) to ensure Linux aarch64 wheel compatibility with PyTorch, Torchvision, OpenCV, and PyAV.
- **Web UI & Services**:
  - Backend servers (e.g. FastAPI / Uvicorn) must run inside WSL binding to `0.0.0.0:8000` or `127.0.0.1:8000`. WSL 2 automatically forwards localhost ports to the Windows host browser.
