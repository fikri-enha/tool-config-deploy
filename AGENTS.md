# AGENTS.md

## Overview
Standalone Windows desktop GUI utility (`AppSettingsEditor`) for scanning, editing, validating, formatting, and diffing `appsettings*.json` files across .NET backend directory trees. Built with zero runtime dependencies (pure Python standard library + Tkinter).

## Commands

### Running Locally
- Run app: `uv run python appsettings_editor.py` (or `python appsettings_editor.py`)
- Syntax & compilation verification: `uv run python -m py_compile appsettings_editor.py`

### Building Executable
- Build standalone `.exe` using build script (requires PowerShell 7+):
  `pwsh -ExecutionPolicy Bypass -File .\build-exe.ps1`
- Build directory bundle (antivirus-friendly):
  `pwsh -ExecutionPolicy Bypass -File .\build-exe.ps1 -OneDir`
- Direct PyInstaller build:
  `uv run pyinstaller AppSettingsEditor.spec --noconfirm`
- Output location: `dist/AppSettingsEditor.exe` (or `dist/AppSettingsEditor/`)

## Architecture & Code Map
Single-file desktop application (`appsettings_editor.py`):
- `flatten_json()` / `fmt_value()`: ASP.NET Core config path flattener (`:` delimited, `[i]` for lists) used for semantic key diffing.
- `EventedText`: Custom `tk.Text` proxy intercepting Tk commands to emit `<<Change>>` on edit, cursor move, and scroll.
- `LineGutter`: Canvas line number gutter rendering line states (active line, syntax error lines).
- `JsonEditor`: Syntax-highlighted JSON editor with debounced (130ms) live diagnostics via `json.loads`, bracket matching, auto-indent, and format/minify.
- `CompareWindow`: Dual-pane diff viewer supporting side-by-side line diff with synchronized scrolling and treeview-based semantic key comparison.
- `AppSettingsEditorApp`: Root `tk.Tk` application managing directory walking, Treeview filtering (exact `appsettings.json` vs `appsettings*.json`), file I/O, and hotkeys.

## Environment & Gotchas
- **PowerShell 7 Required for `build-exe.ps1`**: The script contains `#Requires -Version 7.0` and will fail in Windows PowerShell 5.1 (`powershell.exe`). Always use `pwsh`.
- **Tkinter Requirement**: `uv` virtual environment must use a Python distribution with Tcl/Tk support (`uv run python -c "import tkinter; print('ok')"`). If broken, re-create with `uv venv --python "C:\Path\To\python.exe"`.
- **Headless / CI Limitation**: Tkinter requires an active desktop/display session. Do not run `appsettings_editor.py` directly in headless environments; verify changes via `py_compile` or headless unit testing of pure functions (`flatten_json`).
- **File Encoding**: Reads files using `utf-8-sig` (handles UTF-8 BOM from Visual Studio) and writes using `utf-8` with `newline=""`.
- **Directory Filtering**: Scanner automatically prunes directories in `SKIP_DIRS` (`.git`, `.vs`, `.idea`, `node_modules`, `bin`, `obj`, `__pycache__`).
- **Performance Cap**: Highlighting is skipped if file length exceeds `MAX_HIGHLIGHT_CHARS` (400,000 chars) to prevent UI freezing.
