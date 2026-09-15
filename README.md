# Script Manager Pro / Tool Shelf

A fast, lightweight, and dependable Blender add-on / extension for managing and running Python scripts directly from your 3D Viewport sidebar (`N` panel), Quick Favorites (`Q`), or custom hotkeys.

![Blender](https://img.shields.io/badge/Blender-4.2%2B-orange)
![Version](https://img.shields.io/badge/Version-0.1.0-blue)
![License](https://img.shields.io/badge/License-GPL--3.0-green)

---

## What makes this different?

- **Rock-Solid Multi-Line Execution**: Directly executes multi-line Python scripts, functions, imports, loops, and complex scripts without fragile GUI area switching or crashes. Preserves the active 3D View context so mesh, object, selection, and mode operations execute cleanly.
- **Create Scripts from Blender**:
  - **New Script (`+`)**: Create a new `.py` file with standard templates (blank, basic `bpy`, or selection loop) and immediately open it in Blender's Text Editor.
  - **Paste from Clipboard (`Paste`)**: Directly save the multi-line code currently in your clipboard as a new shelf button in 1 click.
- **Quick Favorites (`Q`) & Custom Hotkeys**:
  - Right-click any script button and choose **Add to Quick Favorites** — the script's actual name appears in your `Q` menu!
  - Right-click and choose **Assign Shortcut** to bind a hotkey directly to that script.
  - Searchable in Blender's `F3` operator search.
- **Streamlined Single-Folder Workflow**:
  - Uses a dedicated scripts folder by default (or set your own custom folder in Preferences).
  - 1-click **Open Folder** button opens your scripts in your system's File Explorer.
- **In-Panel & External Editing**:
  - **Text icon**: Opens the script directly in Blender's built-in Text Editor.
  - **Pencil icon**: Rename display name, edit tags, delete, or open in your system default editor (e.g. VS Code, Notepad).
- **Favorites & Search**: Filter instantly by name, tag, or toggle favorites.

---

## Installation

### As Blender Extension (Blender 4.2+)
1. In Blender, go to **Edit > Preferences > Get Extensions**.
2. Click the top-right menu dropdown and select **Install from Disk...**
3. Select the `ScriptManagerPro` folder or zip archive.

### Classic Add-on Installation
1. Copy or symlink the `ScriptManagerPro` folder into your Blender addons directory (`scripts/addons/`).
2. In Blender, go to **Edit > Preferences > Add-ons**, search for **Script Manager Pro**, and enable the checkbox.

---

## Quick Start

1. Open the 3D Viewport sidebar by pressing **`N`**.
2. Click on the **Tool Shelf** tab.
3. Use the header buttons:
   - **`New`**: Create a new script.
   - **`Paste`**: Create a button directly from clipboard code.
   - **`Refresh`**: Re-scan your scripts folder.
   - **`Folder`**: Open your scripts folder in Windows Explorer.
4. Click any script button to run it immediately.
5. **Right-click** any script button to add it to **Quick Favorites** or **Assign Shortcut**!

---

## Permissions (Blender 4.2+ Extensions)

| Permission | Reason |
|------------|--------|
| `files` | Reads, creates, and executes user Python scripts and saves shelf metadata |

---

## Requirements

- Blender 4.2 LTS or later (including 4.3, 4.4, 4.5+)
- Windows, macOS, or Linux

---

## License

[GPL-3.0-or-later](https://www.gnu.org/licenses/gpl-3.0.html)
