# ⚙️ AppSettings Editor

> **A native Windows desktop utility for finding, editing, validating, and comparing `appsettings*.json` files across complex .NET backend solutions.**

Zero external runtime dependencies. Runs directly with Python or as a single standalone `.exe`.

---

## ✨ Features at a Glance

| Feature | Description |
| :--- | :--- |
| 🔍 **Solution-Wide Scanner** | Recursively traverses directory trees, ignoring clutter (`bin`, `obj`, `node_modules`, `.git`), with a 1-click filter for exact `appsettings.json` vs all variants. |
| 📝 **LSP-Like Code Editor** | Real-time syntax highlighting, live parse error diagnostics with line/column readout, gutter badges, and jump-to-error (`F8`). |
| ⚡ **Smart Editing** | Auto-closes brackets (`{}`, `[]`) and quotes, auto-indents on `Enter`, bracket pair matching, and instant 2-space pretty-printing (`Ctrl+Shift+F`) or minification. |
| ⚖️ **Dual-Mode Diff Tool** | Compare any two files side-by-side with synchronized scrolling or view a **semantic key differences** table flattened into ASP.NET Core path notation (`Section:Key`). |
| 🛡️ **Safe & Native** | Handles UTF-8 with BOM (`utf-8-sig`) seamlessly, warns on unsaved changes, and runs entirely using Python's standard library (`tkinter`). |

---

## 🚀 Quick Start

### Option 1: Run the Standalone Executable (Recommended)

No Python installation required:
1. Locate or build `AppSettingsEditor.exe` inside `dist/`.
2. Double-click to launch.

*(Note: On Windows Server, run inside an active desktop or RDP session).*

### Option 2: Run from Source

Requires **Python 3.8+** with Tkinter (standard on [python.org](https://www.python.org/) Windows installers).

Using [`uv`](https://github.com/astral-sh/uv):
```powershell
uv run python appsettings_editor.py
```

Or using standard Python:
```powershell
python appsettings_editor.py
```

---

## 📖 How to Use

### 1. Scan Your Repository
1. Click **Browse...** (or press `Ctrl + O`) and select your backend workspace or solution root.
2. The treeview on the left will display all matching files grouped by directory structure.
3. Toggle the **`Exact "appsettings.json" only`** checkbox to filter out environment overlays (e.g. `appsettings.Development.json`, `appsettings.Production.json`) when you only need base configurations.

### 2. Edit & Validate
1. Click any file in the tree to open it.
2. The editor performs **real-time syntax validation** as you type:
   - **Valid JSON**: Status bar turns green with `Valid JSON`.
   - **Syntax Error**: Error line is highlighted in red, flagged in the line gutter, and the status bar displays the exact line and column.
   - Press **`F8`** at any time to jump directly to the invalid line.
3. Press **`Ctrl + Shift + F`** to format and beautify with consistent 2-space indentation.
4. Press **`Ctrl + S`** to save changes.

### 3. Compare Configurations
Easily catch missing connection strings or mismatched environment configs:

- **Compare within Tree**: Right-click any file in the sidebar and select **Compare with current file**.
- **Compare with External File**: Click **Compare with...** (or press `Ctrl + D`) in the toolbar to pick any JSON file on your machine.

#### Two Inspection Views:
1. **Side by Side**: Visual line-by-line diff with synchronized scrolling, color-coded additions (green), removals (red), and changes (yellow). Check **Normalize** to auto-format and sort keys before comparing.
2. **Key Differences**: Semantic comparison flattening JSON keys into ASP.NET Core configuration paths (e.g., `ConnectionStrings:DefaultConnection`, `Logging:LogLevel:Default`, `Endpoints[0]`). Shows a high-level table of only added, removed, or modified values.

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
| :--- | :--- |
| `Ctrl + O` | Open root folder |
| `F5` | Rescan directory |
| `Ctrl + S` | Save current file |
| `Ctrl + Shift + F` | Format JSON (pretty-print) |
| `Ctrl + D` | Compare open file with another file |
| `F8` | Jump to next syntax error |
| `Ctrl + Z` / `Ctrl + Y` | Undo / Redo |
| `Ctrl + +` / `Ctrl + -` | Zoom in / Zoom out |
| `Ctrl + 0` | Reset editor font zoom |
| `Ctrl + MouseWheel` | Zoom in / Zoom out with scroll wheel |

---

## 📦 Building Standalone `.exe`

To package the tool into a standalone Windows binary via [PyInstaller](https://pyinstaller.org/):

```powershell
# Single-file standalone executable (dist/AppSettingsEditor.exe)
pwsh -ExecutionPolicy Bypass -File .\build-exe.ps1

# Directory bundle (faster startup and friendly with enterprise antivirus)
pwsh -ExecutionPolicy Bypass -File .\build-exe.ps1 -OneDir
```

> **Note:** `build-exe.ps1` requires **PowerShell 7+** (`pwsh`).
