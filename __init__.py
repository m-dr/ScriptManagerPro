bl_info = {
    "name": "Script Manager Pro",
    "blender": (4, 2, 0),
    "version": (1, 0, 0),
    "category": "Development",
    "author": "Cemil Berk, m-dr",
    "description": "Manage and run your Python scripts in Blender with one-click access, favorites, tags, sub-folder support, and in-panel editing.",
    "support": "COMMUNITY"
}

import bpy
import os
import sys
import re
import json
import traceback
import hashlib
from bpy.props import StringProperty, CollectionProperty, BoolProperty, EnumProperty
from bpy.types import Operator, Panel, PropertyGroup, AddonPreferences

# ---------------------------------------------------------------------------
# Path & Metadata Utilities
# ---------------------------------------------------------------------------

def get_metadata_path(script_dir):
    dot_path = os.path.join(script_dir, ".script_manager", "metadata.json")
    if os.path.exists(dot_path):
        return dot_path
    return os.path.join(script_dir, "Manager_preferences", "preferences.json")


def list_scripts(script_dir):
    """List scripts in script_dir and all sub-folders."""
    if not os.path.isdir(script_dir):
        return []
    scripts = []
    for root, dirs, files in os.walk(script_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "Manager_preferences" and d != "__pycache__"]
        for f in files:
            if f.lower().endswith(".py") and not f.startswith("."):
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, script_dir).replace("\\", "/")
                # For root items rel_path is just f, for subfolders it includes subfolder
                scripts.append(rel_path)
    scripts.sort(key=str.lower)
    return scripts


def extract_auto_tags(name):
    """Extract tags from sub-folders and filename conventions like [mesh] or #tag."""
    tags = set()
    dir_name = os.path.dirname(name)
    if dir_name:
        for part in dir_name.replace("\\", "/").split("/"):
            if part.strip():
                tags.add(part.strip().lower())

    filename = os.path.basename(name)
    name_no_ext = os.path.splitext(filename)[0]

    for match in re.findall(r'\[(.*?)\]', name_no_ext):
        for t in match.split(','):
            if t.strip():
                tags.add(t.strip().lower())

    for t in re.findall(r'#([a-zA-Z0-9_\-]+)', name_no_ext):
        if t.strip():
            tags.add(t.strip().lower())

    for t in re.findall(r'@([a-zA-Z0-9_\-]+)', name_no_ext):
        if t.strip():
            tags.add(t.strip().lower())

    return tags


def load_metadata(script_dir):
    metadata_path = get_metadata_path(script_dir)
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "scripts" in data and isinstance(data["scripts"], dict):
                    unpacked = {}
                    for k, v in data["scripts"].items():
                        unpacked[k] = {
                            "favorite": v.get("favorite", False),
                            "tags": v.get("tags", ""),
                            "custom_display_name": v.get("display_name", "") or v.get("custom_display_name", "")
                        }
                    return unpacked
                return data
        except Exception:
            return {}
    return {}


def save_metadata(script_dir, data):
    metadata_path = get_metadata_path(script_dir)
    os.makedirs(os.path.dirname(metadata_path), exist_ok=True)
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)


# ---------------------------------------------------------------------------
# Direct Multi-Line Execution Engine
# ---------------------------------------------------------------------------

def run_python_file(filepath, context, reporter=None):
    """Execute multi-line python scripts cleanly without area switching hacks."""
    if not os.path.isfile(filepath):
        msg = f"Script file not found: {filepath}"
        if reporter:
            reporter.report({'ERROR'}, msg)
        print(f"[ScriptManagerPro] ERROR: {msg}")
        return False

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            code_str = f.read()
    except Exception as e:
        msg = f"Failed to read file: {e}"
        if reporter:
            reporter.report({'ERROR'}, msg)
        print(f"[ScriptManagerPro] ERROR: {msg}")
        return False

    try:
        code_obj = compile(code_str, filepath, 'exec')
    except Exception as e:
        traceback.print_exc()
        msg = f"Syntax error in {os.path.basename(filepath)}: {e}"
        if reporter:
            reporter.report({'ERROR'}, msg)
        return False

    global_namespace = {
        "__file__": filepath,
        "__name__": "__main__",
        "bpy": bpy,
        "context": context,
    }

    try:
        exec(code_obj, global_namespace)
        msg = f"Executed: {os.path.basename(filepath)}"
        if reporter:
            reporter.report({'INFO'}, msg)
        return True
    except Exception as e:
        traceback.print_exc()
        msg = f"Execution error in {os.path.basename(filepath)}: {e}"
        if reporter:
            reporter.report({'ERROR'}, msg)
        return False


# ---------------------------------------------------------------------------
# Dynamic Operators for Quick Favorites and Hotkeys
# ---------------------------------------------------------------------------

DYNAMIC_OPERATOR_CLASSES = {}


def make_operator_slug(rel_path):
    base = os.path.splitext(rel_path)[0]
    slug = re.sub(r'[^a-zA-Z0-9_]', '_', base).strip('_')
    if len(slug) > 35:
        short_hash = hashlib.md5(slug.encode('utf-8')).hexdigest()[:6]
        slug = f"{slug[:28]}_{short_hash}"
    if not slug or slug[0].isdigit():
        slug = f"s_{slug}"
    return slug.lower()


def unregister_dynamic_operators():
    global DYNAMIC_OPERATOR_CLASSES
    for idname, cls in list(DYNAMIC_OPERATOR_CLASSES.items()):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    DYNAMIC_OPERATOR_CLASSES.clear()


def register_dynamic_operators(context=None):
    """Register dynamic operators so Right Click -> Quick Favorites / Hotkeys works with script name."""
    global DYNAMIC_OPERATOR_CLASSES
    unregister_dynamic_operators()

    if context is None:
        context = bpy.context

    script_dir = ""
    for key in [__package__, "bl_ext.blender_org.script_manager_pro", "script_manager_pro"]:
        addon = context.preferences.addons.get(key, None)
        if addon and addon.preferences and addon.preferences.script_dir.strip():
            script_dir = bpy.path.abspath(addon.preferences.script_dir.strip())
            break

    if not script_dir or not os.path.isdir(script_dir):
        return

    metadata = load_metadata(script_dir)
    files = list_scripts(script_dir)

    for fname in files:
        fpath = os.path.join(script_dir, fname)
        slug = make_operator_slug(fname)
        idname = f"script_manager.run_{slug}"

        counter = 1
        base_slug = slug
        while idname in DYNAMIC_OPERATOR_CLASSES:
            slug = f"{base_slug}_{counter}"
            idname = f"script_manager.run_{slug}"
            counter += 1

        meta = metadata.get(fname, {}) or metadata.get(os.path.basename(fname), {})
        display_name = meta.get("custom_display_name", "").strip() or fname

        op_dict = {
            "bl_idname": idname,
            "bl_label": display_name,
            "bl_description": f"Run {fname}",
            "bl_options": {'REGISTER', 'UNDO'},
            "filepath": fpath,
            "execute": (lambda script_path: (lambda self, ctx: {'FINISHED'} if run_python_file(script_path, ctx, self) else {'CANCELLED'}))(fpath)
        }

        try:
            cls = type(f"SCRIPT_OT_Dyn_{slug}", (Operator,), op_dict)
            bpy.utils.register_class(cls)
            DYNAMIC_OPERATOR_CLASSES[idname] = cls
        except Exception as e:
            print(f"[ScriptManagerPro] Could not register dynamic operator {idname}: {e}")


# ---------------------------------------------------------------------------
# Preferences & Properties
# ---------------------------------------------------------------------------

class ScriptManagerPreferences(AddonPreferences):
    bl_idname = __package__

    script_dir: StringProperty(
        name="Scripts Folder Path",
        subtype='DIR_PATH',
        default=""
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "script_dir")


class ScriptItem(PropertyGroup):
    name: StringProperty()
    path: StringProperty()
    favorite: BoolProperty(default=False)
    tags: StringProperty(default='')
    custom_display_name: StringProperty(default='')
    operator_idname: StringProperty(default='')
    edit_mode: BoolProperty(default=False)


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class SCRIPT_OT_RunScript(Operator):
    bl_idname = "script_manager.run_script"
    bl_label = "Run Script"
    bl_description = "Run the selected Python script."

    path: StringProperty()

    def execute(self, context):
        if not self.path or not os.path.isfile(self.path):
            self.report({'ERROR'}, f"Script not found: {self.path}")
            return {'CANCELLED'}
        success = run_python_file(self.path, context, self)
        return {'FINISHED'} if success else {'CANCELLED'}


class SCRIPT_OT_NewScript(Operator):
    bl_idname = "script_manager.new_script"
    bl_label = "New Script"
    bl_description = "Create a new Python script in your scripts folder."
    bl_options = {'REGISTER', 'UNDO'}

    script_name: StringProperty(
        name="Script Name",
        description="Filename (e.g. bevel_all.py or Subfolder/my_tool.py)",
        default="new_script.py"
    )

    template: EnumProperty(
        name="Template",
        items=[
            ('EMPTY', "Blank", "Empty file"),
            ('BASIC', "Basic (bpy)", "Standard import bpy"),
            ('SELECTION', "Loop Selected Objects", "Iterate over selected objects"),
        ],
        default='BASIC'
    )

    open_in_editor: BoolProperty(
        name="Open in Text Editor",
        default=True
    )

    def invoke(self, context, event):
        prefs = context.preferences.addons[__package__].preferences
        script_dir = bpy.path.abspath(prefs.script_dir)
        counter = 1
        name = "new_script.py"
        while os.path.exists(os.path.join(script_dir, name)):
            name = f"new_script_{counter}.py"
            counter += 1
        self.script_name = name
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "script_name")
        layout.prop(self, "template")
        layout.prop(self, "open_in_editor")

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        script_dir = bpy.path.abspath(prefs.script_dir)
        if not os.path.isdir(script_dir):
            self.report({'ERROR'}, "Invalid Scripts Folder Path in Addon Preferences.")
            return {'CANCELLED'}

        fname = self.script_name.strip().replace("\\", "/")
        if not fname.lower().endswith(".py"):
            fname += ".py"

        filepath = os.path.join(script_dir, fname)
        if os.path.exists(filepath):
            self.report({'ERROR'}, f"File already exists: {fname}")
            return {'CANCELLED'}

        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        templates = {
            'EMPTY': "",
            'BASIC': "import bpy\n\n# Your script here\nprint('Hello from ' + __file__)\n",
            'SELECTION': "import bpy\n\nfor obj in bpy.context.selected_objects:\n    print(f'Selected: {obj.name}')\n",
        }

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(templates.get(self.template, ""))
        except Exception as e:
            self.report({'ERROR'}, f"Failed to create file: {e}")
            return {'CANCELLED'}

        bpy.ops.script_manager.refresh_list()

        if self.open_in_editor:
            bpy.ops.script_manager.open_script(path=filepath)

        self.report({'INFO'}, f"Created script: {fname}")
        return {'FINISHED'}


class SCRIPT_OT_NewFromClipboard(Operator):
    bl_idname = "script_manager.new_from_clipboard"
    bl_label = "Paste Script"
    bl_description = "Create a new script file with the code currently in your clipboard."
    bl_options = {'REGISTER', 'UNDO'}

    script_name: StringProperty(
        name="Script Name",
        default="pasted_script.py"
    )

    open_in_editor: BoolProperty(
        name="Open in Text Editor",
        default=False
    )

    def invoke(self, context, event):
        clipboard = context.window_manager.clipboard.strip()
        if not clipboard:
            self.report({'WARNING'}, "Clipboard is empty! Copy some code first.")
            return {'CANCELLED'}

        prefs = context.preferences.addons[__package__].preferences
        script_dir = bpy.path.abspath(prefs.script_dir)
        counter = 1
        name = "pasted_script.py"
        while os.path.exists(os.path.join(script_dir, name)):
            name = f"pasted_script_{counter}.py"
            counter += 1
        self.script_name = name
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "script_name")
        layout.prop(self, "open_in_editor")

    def execute(self, context):
        clipboard = context.window_manager.clipboard
        if not clipboard.strip():
            self.report({'ERROR'}, "Clipboard is empty.")
            return {'CANCELLED'}

        prefs = context.preferences.addons[__package__].preferences
        script_dir = bpy.path.abspath(prefs.script_dir)
        if not os.path.isdir(script_dir):
            self.report({'ERROR'}, "Invalid Scripts Folder Path in Addon Preferences.")
            return {'CANCELLED'}

        fname = self.script_name.strip().replace("\\", "/")
        if not fname.lower().endswith(".py"):
            fname += ".py"

        filepath = os.path.join(script_dir, fname)
        if os.path.exists(filepath):
            self.report({'ERROR'}, f"File already exists: {fname}")
            return {'CANCELLED'}

        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(clipboard)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to save script: {e}")
            return {'CANCELLED'}

        bpy.ops.script_manager.refresh_list()

        if self.open_in_editor:
            bpy.ops.script_manager.open_script(path=filepath)

        self.report({'INFO'}, f"Saved script from clipboard: {fname}")
        return {'FINISHED'}


class SCRIPT_OT_OpenScript(Operator):
    bl_idname = "script_manager.open_script"
    bl_label = "Open in Text Editor"
    bl_description = "Open the script in Blender's text editor."

    path: StringProperty()

    def execute(self, context):
        filename = os.path.basename(self.path)
        try:
            text = bpy.data.texts.load(self.path)
        except RuntimeError:
            text = bpy.data.texts.get(filename)
        for area in context.window.screen.areas:
            if area.type == 'TEXT_EDITOR':
                area.spaces.active.text = text
                self.report({'INFO'}, f"Opened {filename} in Text Editor")
                return {'FINISHED'}
        self.report({'INFO'}, "No Text Editor found. Please open one manually.")
        return {'FINISHED'}


class SCRIPT_OT_ToggleFavorite(Operator):
    bl_idname = "script_manager.toggle_favorite"
    bl_label = "Toggle Favorite"
    bl_description = "Add or remove this script from favorites."

    script_name: StringProperty()

    def execute(self, context):
        wm = context.window_manager
        script_dir = context.preferences.addons[__package__].preferences.script_dir
        metadata = load_metadata(script_dir)
        for item in wm.script_list:
            if item.name == self.script_name:
                item.favorite = not item.favorite
                metadata[item.name] = {
                    "favorite": item.favorite,
                    "tags": item.tags,
                    "custom_display_name": item.custom_display_name
                }
                state = "added to" if item.favorite else "removed from"
                self.report({'INFO'}, f"{item.name} {state} favorites.")
                break
        save_metadata(script_dir, metadata)
        return {'FINISHED'}


class SCRIPT_OT_ToggleEditMode(Operator):
    bl_idname = "script_manager.toggle_edit_mode"
    bl_label = "Toggle Edit Mode"
    bl_description = "Toggle metadata editing for this script."

    script_name: StringProperty()

    def execute(self, context):
        wm = context.window_manager
        for item in wm.script_list:
            if item.name == self.script_name:
                item.edit_mode = not item.edit_mode
                mode = "enabled" if item.edit_mode else "disabled"
                self.report({'INFO'}, f"Edit mode {mode} for {item.name}")
                break
        return {'FINISHED'}


class SCRIPT_OT_SaveMetadata(Operator):
    bl_idname = "script_manager.save_metadata"
    bl_label = "Save Metadata"
    bl_description = "Save tags and display name for this script."

    script_name: StringProperty()

    def execute(self, context):
        wm = context.window_manager
        script_dir = context.preferences.addons[__package__].preferences.script_dir
        metadata = load_metadata(script_dir)
        for item in wm.script_list:
            if item.name == self.script_name:
                metadata[item.name] = {
                    "favorite": item.favorite,
                    "tags": item.tags,
                    "custom_display_name": item.custom_display_name
                }
                item.edit_mode = False
                self.report({'INFO'}, f"Metadata saved for {item.name}")
                break
        save_metadata(script_dir, metadata)
        register_dynamic_operators(context)
        return {'FINISHED'}


class SCRIPT_OT_RefreshList(Operator):
    bl_idname = "script_manager.refresh_list"
    bl_label = "Refresh List"
    bl_description = "Refresh the list of available scripts."

    def execute(self, context):
        wm = context.window_manager
        prefs = context.preferences.addons[__package__].preferences
        script_dir = prefs.script_dir
        if not os.path.isdir(script_dir):
            self.report({'ERROR'}, "Invalid Scripts Folder Path in Addon Preferences.")
            return {'CANCELLED'}

        wm.script_list.clear()
        metadata = load_metadata(script_dir)
        files = list_scripts(script_dir)

        register_dynamic_operators(context)

        for fname in files:
            item = wm.script_list.add()
            item.name = fname
            item.path = os.path.join(script_dir, fname)
            meta = metadata.get(fname, {}) or metadata.get(os.path.basename(fname), {})
            item.favorite = meta.get("favorite", False)
            item.tags = meta.get("tags", "")
            item.custom_display_name = meta.get("custom_display_name", "")
            slug = make_operator_slug(fname)
            item.operator_idname = f"script_manager.run_{slug}"

        self.report({'INFO'}, f"Script list refreshed ({len(files)} scripts).")
        return {'FINISHED'}


class SCRIPT_OT_OpenScriptFolder(Operator):
    bl_idname = "script_manager.open_script_folder"
    bl_label = "Open Scripts Folder"
    bl_description = "Open the scripts folder in your file explorer."

    def execute(self, context):
        script_dir = context.preferences.addons[__package__].preferences.script_dir
        if os.path.isdir(script_dir):
            try:
                if sys.platform == "win32":
                    os.startfile(script_dir)
                else:
                    import subprocess
                    subprocess.Popen(["xdg-open" if sys.platform == "linux" else "open", script_dir])
                self.report({'INFO'}, "Scripts folder opened.")
            except Exception as e:
                self.report({'ERROR'}, f"Failed to open folder: {e}")
        else:
            self.report({'ERROR'}, "Invalid Scripts Folder Path.")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# UI Panel - 100% faithful to original 1.0 UI layout
# ---------------------------------------------------------------------------

class SCRIPT_PT_ScriptManagerPanel(Panel):
    bl_label = "Script Manager"
    bl_idname = "SCRIPT_PT_script_manager"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Script Manager'

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager

        # Original Row 1: Refresh List & Open Scripts Folder
        row = layout.row()
        row.operator("script_manager.refresh_list", icon='FILE_REFRESH', text="Refresh List")
        row.operator("script_manager.open_script_folder", icon='FILE_FOLDER', text="Open Scripts Folder")

        # Added Row: New Script & Paste Script
        row = layout.row()
        row.operator("script_manager.new_script", icon='ADD', text="New Script")
        row.operator("script_manager.new_from_clipboard", icon='PASTEDOWN', text="Paste Script")

        # Original Row 2: Tags & Favorites
        row = layout.row()
        row.prop(wm, "filter_tags", text="Tags")
        row.prop(wm, "show_favorites_only", toggle=True, text="Favorites", icon='SOLO_ON')

        for item in wm.script_list:
            if wm.show_favorites_only and not item.favorite:
                continue

            # Tag filter: checks manual tags, subfolder tags, and filename tags
            if wm.filter_tags.strip():
                filter_term = wm.filter_tags.strip().lower()
                auto_tags = extract_auto_tags(item.name)
                combined = f"{item.tags} {' '.join(auto_tags)} {item.name}".lower()
                if filter_term not in combined:
                    continue

            box = layout.box()
            row = box.row()
            display_name = item.custom_display_name if item.custom_display_name.strip() else item.name
            row.label(text=display_name, icon='SCRIPT')

            # Run button uses dynamic operator so Quick Favorites & Hotkeys work
            op_name = item.operator_idname if item.operator_idname in DYNAMIC_OPERATOR_CLASSES else "script_manager.run_script"
            run = row.operator(op_name, text="", icon='PLAY')
            if op_name == "script_manager.run_script":
                run.path = item.path

            edit = row.operator("script_manager.open_script", text="", icon='TEXT')
            edit.path = item.path

            fav = row.operator("script_manager.toggle_favorite", text="", icon='SOLO_ON' if item.favorite else 'SOLO_OFF')
            fav.script_name = item.name

            toggle = row.operator("script_manager.toggle_edit_mode", text="", icon='GREASEPENCIL')
            toggle.script_name = item.name

            if item.edit_mode:
                box.prop(item, "custom_display_name", text="Display Name")
                box.prop(item, "tags", text="Tags")
                save = box.operator("script_manager.save_metadata", text="Save")
                save.script_name = item.name
            else:
                display_tags = item.tags.strip()
                if not display_tags:
                    auto_tags = extract_auto_tags(item.name)
                    if auto_tags:
                        display_tags = ", ".join(sorted(auto_tags))
                if display_tags:
                    row = box.row()
                    row.label(text=f"Tags: {display_tags}")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    ScriptManagerPreferences,
    ScriptItem,
    SCRIPT_OT_RunScript,
    SCRIPT_OT_NewScript,
    SCRIPT_OT_NewFromClipboard,
    SCRIPT_OT_OpenScript,
    SCRIPT_OT_ToggleFavorite,
    SCRIPT_OT_ToggleEditMode,
    SCRIPT_OT_SaveMetadata,
    SCRIPT_OT_RefreshList,
    SCRIPT_OT_OpenScriptFolder,
    SCRIPT_PT_ScriptManagerPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.script_list = CollectionProperty(type=ScriptItem)
    bpy.types.WindowManager.show_favorites_only = BoolProperty(name="Favorites Only", default=False)
    bpy.types.WindowManager.filter_tags = StringProperty(name="Tag Filter", default="")

    def deferred_init():
        try:
            bpy.ops.script_manager.refresh_list()
        except Exception:
            pass
        return None

    bpy.app.timers.register(deferred_init, first_interval=0.1)


def unregister():
    unregister_dynamic_operators()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.script_list
    del bpy.types.WindowManager.show_favorites_only
    del bpy.types.WindowManager.filter_tags


if __name__ == "__main__":
    register()
