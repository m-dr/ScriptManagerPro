bl_info = {
    "name": "Script Manager Pro",
    "blender": (4, 2, 0),
    "version": (0, 1, 0),
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
import subprocess
from bpy.props import StringProperty, CollectionProperty, BoolProperty, EnumProperty
from bpy.types import Operator, Panel, PropertyGroup, AddonPreferences

# ---------------------------------------------------------------------------
# Path & Metadata Utilities
# ---------------------------------------------------------------------------

def get_metadata_path(script_dir):
    return os.path.join(script_dir, "Manager_preferences", "preferences.json")


def get_default_script_dir():
    user_script_dir = bpy.utils.user_resource('SCRIPTS')
    if user_script_dir:
        default_dir = os.path.join(user_script_dir, "script_manager_shelf")
    else:
        default_dir = os.path.join(os.path.expanduser("~"), "Documents", "BlenderScripts")
    return default_dir


def get_script_dir(context=None):
    if context is None:
        context = bpy.context
    try:
        addon_name = __package__ or "ScriptManagerPro"
        # Search for addon preferences across possible registered names
        for key in [addon_name, "bl_ext.blender_org.script_manager_pro", "script_manager_pro"]:
            addon_prefs = context.preferences.addons.get(key, None)
            if addon_prefs and addon_prefs.preferences and addon_prefs.preferences.script_dir.strip():
                path = bpy.path.abspath(addon_prefs.preferences.script_dir.strip())
                if os.path.isdir(path):
                    return path
    except Exception:
        pass

    default_dir = get_default_script_dir()
    os.makedirs(default_dir, exist_ok=True)
    return default_dir


def list_scripts_recursive(script_dir):
    """Recursively list all .py scripts in script_dir and any sub-folders."""
    if not os.path.isdir(script_dir):
        return []
    scripts = []
    for root, dirs, files in os.walk(script_dir):
        # Ignore hidden folders, git, and preferences cache
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "Manager_preferences" and d != "__pycache__"]
        for f in files:
            if f.lower().endswith(".py") and not f.startswith("."):
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, script_dir).replace("\\", "/")
                scripts.append((full_path, rel_path, f))
    scripts.sort(key=lambda x: x[1].lower())
    return scripts


def extract_auto_tags(rel_path):
    """Extract tags automatically from sub-folders and filename tags like [mesh] or #tag."""
    tags = set()
    dir_name = os.path.dirname(rel_path)
    if dir_name:
        for part in dir_name.replace("\\", "/").split("/"):
            part = part.strip()
            if part:
                tags.add(part.lower())

    filename = os.path.basename(rel_path)
    name_no_ext = os.path.splitext(filename)[0]

    # Bracketed tags: e.g. [mesh] or [modeling, tool]
    bracket_matches = re.findall(r'\[(.*?)\]', name_no_ext)
    for match in bracket_matches:
        for t in match.split(','):
            t = t.strip()
            if t:
                tags.add(t.lower())

    # Hashtags: e.g. #mesh #bevel
    hash_matches = re.findall(r'#([a-zA-Z0-9_\-]+)', name_no_ext)
    for t in hash_matches:
        if t.strip():
            tags.add(t.strip().lower())

    # @ tags: e.g. @uv @unwrap
    at_matches = re.findall(r'@([a-zA-Z0-9_\-]+)', name_no_ext)
    for t in at_matches:
        if t.strip():
            tags.add(t.strip().lower())

    return tags


def clean_display_title(filename):
    """Strip bracketed tags from the display name if desired."""
    name_no_ext = os.path.splitext(filename)[0]
    # Remove leading/trailing bracket tags for a clean title: e.g. "[mesh] extrude" -> "extrude"
    cleaned = re.sub(r'\[.*?\]', '', name_no_ext).strip()
    cleaned = re.sub(r'#[a-zA-Z0-9_\-]+', '', cleaned).strip()
    cleaned = re.sub(r'@[a-zA-Z0-9_\-]+', '', cleaned).strip()
    return cleaned if cleaned else name_no_ext


def load_metadata(script_dir):
    metadata_path = get_metadata_path(script_dir)
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_metadata(script_dir, data):
    metadata_path = get_metadata_path(script_dir)
    try:
        os.makedirs(os.path.dirname(metadata_path), exist_ok=True)
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"[ScriptManagerPro] Failed to save metadata: {e}")


# ---------------------------------------------------------------------------
# Direct Script Execution Engine
# ---------------------------------------------------------------------------

def run_python_file(filepath, context, reporter=None):
    """Execute a multi-line python script cleanly without GUI hackery."""
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
        import hashlib
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
    """Dynamically register one unique operator per script.
    Enables Right Click -> Quick Favorites / Hotkeys to show the script's name."""
    global DYNAMIC_OPERATOR_CLASSES
    unregister_dynamic_operators()

    script_dir = get_script_dir(context)
    if not os.path.isdir(script_dir):
        return

    metadata = load_metadata(script_dir)
    entries = list_scripts_recursive(script_dir)

    for full_path, rel_path, fname in entries:
        slug = make_operator_slug(rel_path)
        idname = f"script_manager.run_{slug}"

        counter = 1
        base_slug = slug
        while idname in DYNAMIC_OPERATOR_CLASSES:
            slug = f"{base_slug}_{counter}"
            idname = f"script_manager.run_{slug}"
            counter += 1

        meta = metadata.get(rel_path, {}) or metadata.get(fname, {})
        display_name = meta.get("custom_display_name", "").strip() or clean_display_title(fname)

        op_dict = {
            "bl_idname": idname,
            "bl_label": display_name,
            "bl_description": f"Run {rel_path}",
            "bl_options": {'REGISTER', 'UNDO'},
            "filepath": full_path,
            "execute": (lambda script_path: (lambda self, ctx: {'FINISHED'} if run_python_file(script_path, ctx, self) else {'CANCELLED'}))(full_path)
        }

        try:
            cls = type(f"SCRIPT_OT_Dyn_{slug}", (Operator,), op_dict)
            bpy.utils.register_class(cls)
            DYNAMIC_OPERATOR_CLASSES[idname] = cls
        except Exception as e:
            print(f"[ScriptManagerPro] Could not register dynamic operator {idname}: {e}")


# ---------------------------------------------------------------------------
# Addon Preferences
# ---------------------------------------------------------------------------

class ScriptManagerPreferences(AddonPreferences):
    bl_idname = __package__ or "ScriptManagerPro"

    script_dir: StringProperty(
        name="Scripts Folder Path",
        subtype='DIR_PATH',
        default=""
    )

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.prop(self, "script_dir")
        effective_dir = get_script_dir(context)
        col.label(text=f"Active Folder: {effective_dir}", icon='FILE_FOLDER')


# ---------------------------------------------------------------------------
# UI Data Properties
# ---------------------------------------------------------------------------

class ScriptItem(PropertyGroup):
    name: StringProperty()           # Filename
    rel_path: StringProperty()       # Relative path with sub-folders
    path: StringProperty()           # Absolute path
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
    bl_options = {'REGISTER', 'UNDO'}

    path: StringProperty()

    def execute(self, context):
        if not self.path:
            self.report({'ERROR'}, "No script path provided.")
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
        description="Filename (e.g. bevel_all.py or subfolder/bevel.py)",
        default="new_script.py"
    )

    template: EnumProperty(
        name="Template",
        description="Boilerplate template",
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
        script_dir = get_script_dir(context)
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
        script_dir = get_script_dir(context)
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

        content = templates.get(self.template, "")
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
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
    bl_label = "Paste from Clipboard"
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

        script_dir = get_script_dir(context)
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

        script_dir = get_script_dir(context)
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
        if not os.path.isfile(self.path):
            self.report({'ERROR'}, f"File not found: {self.path}")
            return {'CANCELLED'}

        filename = os.path.basename(self.path)
        text = None
        for t in bpy.data.texts:
            if t.filepath == self.path or t.name == filename:
                text = t
                break

        if not text:
            try:
                text = bpy.data.texts.load(self.path)
            except Exception as e:
                self.report({'ERROR'}, f"Could not load script into Text Editor: {e}")
                return {'CANCELLED'}

        for area in context.window.screen.areas:
            if area.type == 'TEXT_EDITOR':
                area.spaces.active.text = text
                self.report({'INFO'}, f"Opened {filename} in Text Editor")
                return {'FINISHED'}

        self.report({'INFO'}, f"Loaded {filename}. Switch to Text Editor / Scripting workspace to view.")
        return {'FINISHED'}


class SCRIPT_OT_DeleteScript(Operator):
    bl_idname = "script_manager.delete_script"
    bl_label = "Delete Script"
    bl_description = "Delete this script file."
    bl_options = {'REGISTER', 'UNDO'}

    script_name: StringProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        script_dir = get_script_dir(context)
        filepath = os.path.join(script_dir, self.script_name)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception as e:
                self.report({'ERROR'}, f"Failed to delete file: {e}")
                return {'CANCELLED'}

        metadata = load_metadata(script_dir)
        if self.script_name in metadata:
            del metadata[self.script_name]
            save_metadata(script_dir, metadata)

        bpy.ops.script_manager.refresh_list()
        self.report({'INFO'}, f"Deleted: {self.script_name}")
        return {'FINISHED'}


class SCRIPT_OT_ToggleFavorite(Operator):
    bl_idname = "script_manager.toggle_favorite"
    bl_label = "Toggle Favorite"
    bl_description = "Add or remove this script from favorites."

    script_name: StringProperty()

    def execute(self, context):
        wm = context.window_manager
        script_dir = get_script_dir(context)
        metadata = load_metadata(script_dir)
        for item in wm.script_list:
            if item.rel_path == self.script_name or item.name == self.script_name:
                item.favorite = not item.favorite
                key = item.rel_path
                metadata[key] = {
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
            if item.rel_path == self.script_name or item.name == self.script_name:
                item.edit_mode = not item.edit_mode
                break
        return {'FINISHED'}


class SCRIPT_OT_SaveMetadata(Operator):
    bl_idname = "script_manager.save_metadata"
    bl_label = "Save Metadata"
    bl_description = "Save tags and display name for this script."

    script_name: StringProperty()

    def execute(self, context):
        wm = context.window_manager
        script_dir = get_script_dir(context)
        metadata = load_metadata(script_dir)
        for item in wm.script_list:
            if item.rel_path == self.script_name or item.name == self.script_name:
                key = item.rel_path
                metadata[key] = {
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
    bl_description = "Refresh the list of available scripts (including sub-folders)."

    def execute(self, context):
        wm = context.window_manager
        script_dir = get_script_dir(context)

        wm.script_list.clear()
        metadata = load_metadata(script_dir)
        entries = list_scripts_recursive(script_dir)

        register_dynamic_operators(context)

        for full_path, rel_path, fname in entries:
            item = wm.script_list.add()
            item.name = fname
            item.rel_path = rel_path
            item.path = full_path

            meta = metadata.get(rel_path, {}) or metadata.get(fname, {})
            item.favorite = meta.get("favorite", False)
            
            # Auto-tagging: combine manual tags with tags discovered from subfolders and filename brackets
            manual_tags = meta.get("tags", "")
            auto_tags = extract_auto_tags(rel_path)
            if manual_tags:
                item.tags = manual_tags
            elif auto_tags:
                item.tags = ", ".join(sorted(auto_tags))
            else:
                item.tags = ""

            item.custom_display_name = meta.get("custom_display_name", "")

            slug = make_operator_slug(rel_path)
            item.operator_idname = f"script_manager.run_{slug}"

        self.report({'INFO'}, f"Script list refreshed ({len(entries)} scripts).")
        return {'FINISHED'}


class SCRIPT_OT_OpenScriptFolder(Operator):
    bl_idname = "script_manager.open_script_folder"
    bl_label = "Open Scripts Folder"
    bl_description = "Open the scripts folder in your file explorer."

    def execute(self, context):
        script_dir = get_script_dir(context)
        if os.path.isdir(script_dir):
            try:
                if sys.platform == "win32":
                    os.startfile(script_dir)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", script_dir])
                else:
                    subprocess.Popen(["xdg-open", script_dir])
                self.report({'INFO'}, "Scripts folder opened.")
            except Exception as e:
                self.report({'ERROR'}, f"Failed to open folder: {e}")
        else:
            self.report({'ERROR'}, "Invalid Scripts Folder Path.")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# UI Panel - Faithful to original layout with added New/Paste & Subfolders
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

        # Row 1: Add new file actions
        row = layout.row(align=True)
        row.operator("script_manager.new_script", icon='ADD', text="New Script")
        row.operator("script_manager.new_from_clipboard", icon='PASTEDOWN', text="Paste Script")

        # Row 2: Original Refresh List & Open Scripts Folder
        row = layout.row(align=True)
        row.operator("script_manager.refresh_list", icon='FILE_REFRESH', text="Refresh List")
        row.operator("script_manager.open_script_folder", icon='FILE_FOLDER', text="Open Scripts Folder")

        # Row 3: Original Tags filter & Favorites toggle
        row = layout.row()
        row.prop(wm, "filter_tags", text="Tags")
        row.prop(wm, "show_favorites_only", toggle=True, text="Favorites", icon='SOLO_ON')

        if len(wm.script_list) == 0:
            box = layout.box()
            box.label(text="No scripts found in folder.", icon='INFO')
            box.label(text="Click 'New Script' or 'Paste Script' above.")
            return

        filter_term = wm.filter_tags.strip().lower()

        for item in wm.script_list:
            if wm.show_favorites_only and not item.favorite:
                continue

            # Tag & Name filter check
            if filter_term:
                auto_tags = extract_auto_tags(item.rel_path)
                combined = f"{item.name} {item.tags} {' '.join(auto_tags)} {item.custom_display_name}".lower()
                if filter_term not in combined:
                    continue

            box = layout.box()
            row = box.row()

            # Display name: show sub-folder prefix if in sub-folder
            base_display = item.custom_display_name.strip() or clean_display_title(item.name)
            subfolder = os.path.dirname(item.rel_path).replace("\\", "/")
            if subfolder:
                display_label = f"{subfolder}/{base_display}"
            else:
                display_label = base_display

            row.label(text=display_label, icon='SCRIPT')

            # Run button uses dynamic operator so Right Click -> Quick Favorites / Shortcut uses script name
            op_name = item.operator_idname if item.operator_idname in DYNAMIC_OPERATOR_CLASSES else "script_manager.run_script"
            run = row.operator(op_name, text="", icon='PLAY')
            if op_name == "script_manager.run_script":
                run.path = item.path

            edit = row.operator("script_manager.open_script", text="", icon='TEXT')
            edit.path = item.path

            fav = row.operator("script_manager.toggle_favorite", text="", icon='SOLO_ON' if item.favorite else 'SOLO_OFF')
            fav.script_name = item.rel_path

            toggle = row.operator("script_manager.toggle_edit_mode", text="", icon='GREASEPENCIL')
            toggle.script_name = item.rel_path

            if item.edit_mode:
                box.prop(item, "custom_display_name", text="Display Name")
                box.prop(item, "tags", text="Tags")
                btn_row = box.row(align=True)
                save = btn_row.operator("script_manager.save_metadata", text="Save")
                save.script_name = item.rel_path
                del_btn = btn_row.operator("script_manager.delete_script", text="Delete", icon='TRASH')
                del_btn.script_name = item.rel_path
            else:
                display_tags = item.tags.strip()
                if not display_tags:
                    auto_tags = extract_auto_tags(item.rel_path)
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
    SCRIPT_OT_DeleteScript,
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
    bpy.types.WindowManager.show_favorites_only = BoolProperty(name="Favorites", default=False)
    bpy.types.WindowManager.filter_tags = StringProperty(name="Tags", default="")

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
