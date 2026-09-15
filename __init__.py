bl_info = {
    "name": "Script Manager Pro",
    "blender": (4, 2, 0),
    "version": (0, 1, 0),
    "category": "Development",
    "author": "Cemil Berk, m-dr",
    "description": "Fast and reliable Python script shelf: create, paste, run, hotkey, and favorite multi-line scripts.",
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

def get_default_script_dir():
    """Return a dependable default directory for scripts."""
    user_script_dir = bpy.utils.user_resource('SCRIPTS')
    if user_script_dir:
        default_dir = os.path.join(user_script_dir, "script_manager_shelf")
    else:
        default_dir = os.path.join(os.path.expanduser("~"), "Documents", "BlenderScripts")
    return default_dir


def get_script_dir(context=None):
    """Retrieve the active scripts folder, creating it if needed."""
    if context is None:
        context = bpy.context
    try:
        addon_name = __package__ or "ScriptManagerPro"
        addon_prefs = context.preferences.addons.get(addon_name, None)
        if addon_prefs and addon_prefs.preferences and addon_prefs.preferences.script_dir.strip():
            path = bpy.path.abspath(addon_prefs.preferences.script_dir.strip())
            if os.path.isdir(path):
                return path
    except Exception:
        pass

    default_dir = get_default_script_dir()
    os.makedirs(default_dir, exist_ok=True)
    return default_dir


def get_metadata_path(script_dir):
    return os.path.join(script_dir, "Manager_preferences", "preferences.json")


def list_scripts(script_dir):
    if not os.path.isdir(script_dir):
        return []
    try:
        return sorted([f for f in os.listdir(script_dir) if f.lower().endswith(".py") and not f.startswith(".")])
    except Exception:
        return []


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


def make_operator_slug(filename):
    base = os.path.splitext(filename)[0]
    slug = re.sub(r'[^a-zA-Z0-9_]', '_', base).strip('_')
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
    """Dynamically register one unique operator per script file.
    This enables right-click -> 'Add to Quick Favorites' and 'Assign Shortcut'
    to display the script's actual name in the Q menu and keymaps."""
    global DYNAMIC_OPERATOR_CLASSES
    unregister_dynamic_operators()

    script_dir = get_script_dir(context)
    if not os.path.isdir(script_dir):
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

        meta = metadata.get(fname, {})
        display_name = meta.get("custom_display_name", "").strip() or os.path.splitext(fname)[0]

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
# Addon Preferences
# ---------------------------------------------------------------------------

class ScriptManagerPreferences(AddonPreferences):
    bl_idname = __package__ or "ScriptManagerPro"

    script_dir: StringProperty(
        name="Scripts Folder",
        description="Path to folder containing your Python scripts (.py)",
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
    name: StringProperty(name="File Name")
    path: StringProperty(name="File Path")
    favorite: BoolProperty(name="Favorite", default=False)
    tags: StringProperty(name="Tags", default="")
    custom_display_name: StringProperty(name="Display Name", default="")
    operator_idname: StringProperty(name="Operator ID", default="")
    edit_mode: BoolProperty(name="Edit Mode", default=False)


# ---------------------------------------------------------------------------
# Standard Operators
# ---------------------------------------------------------------------------

class SCRIPT_OT_RunScript(Operator):
    bl_idname = "script_manager.run_script"
    bl_label = "Run Script"
    bl_description = "Run this Python script"
    bl_options = {'REGISTER', 'UNDO'}

    path: StringProperty(name="Script Path")

    def execute(self, context):
        if not self.path:
            self.report({'ERROR'}, "No script path provided.")
            return {'CANCELLED'}
        success = run_python_file(self.path, context, self)
        return {'FINISHED'} if success else {'CANCELLED'}


class SCRIPT_OT_NewScript(Operator):
    bl_idname = "script_manager.new_script"
    bl_label = "New Script"
    bl_description = "Create a new Python script in your scripts folder"
    bl_options = {'REGISTER', 'UNDO'}

    script_name: StringProperty(
        name="Script Name",
        description="Filename for the new script (e.g. bevel_all.py)",
        default="new_script.py"
    )

    template: EnumProperty(
        name="Template",
        description="Initial code template",
        items=[
            ('EMPTY', "Blank", "Empty python file"),
            ('BASIC', "Basic (bpy)", "Standard import bpy boilerplate"),
            ('SELECTION', "Selected Objects Loop", "Loop over selected objects"),
            ('MODAL_OP', "Simple Operator", "Register a quick operator template"),
        ],
        default='BASIC'
    )

    open_in_editor: BoolProperty(
        name="Open in Text Editor",
        description="Immediately open this file in Blender's Text Editor",
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
        fname = self.script_name.strip()
        if not fname.lower().endswith(".py"):
            fname += ".py"

        filepath = os.path.join(script_dir, fname)
        if os.path.exists(filepath):
            self.report({'ERROR'}, f"File already exists: {fname}")
            return {'CANCELLED'}

        templates = {
            'EMPTY': "",
            'BASIC': (
                "import bpy\n\n"
                "# Your script here\n"
                "print('Hello from ' + __file__)\n"
            ),
            'SELECTION': (
                "import bpy\n\n"
                "for obj in bpy.context.selected_objects:\n"
                "    print(f'Selected: {obj.name}')\n"
            ),
            'MODAL_OP': (
                "import bpy\n\n"
                "class SimpleToolOperator(bpy.types.Operator):\n"
                "    bl_idname = 'object.simple_tool'\n"
                "    bl_label = 'Simple Tool'\n"
                "    \n"
                "    def execute(self, context):\n"
                "        self.report({'INFO'}, 'Executed Simple Tool')\n"
                "        return {'FINISHED'}\n\n"
                "bpy.utils.register_class(SimpleToolOperator)\n"
            ),
        }

        content = templates.get(self.template, "")
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to create script file: {e}")
            return {'CANCELLED'}

        bpy.ops.script_manager.refresh_list()

        if self.open_in_editor:
            bpy.ops.script_manager.open_script(path=filepath)

        self.report({'INFO'}, f"Created script: {fname}")
        return {'FINISHED'}


class SCRIPT_OT_NewFromClipboard(Operator):
    bl_idname = "script_manager.new_from_clipboard"
    bl_label = "New Script from Clipboard"
    bl_description = "Create a new script file with the code currently in your clipboard"
    bl_options = {'REGISTER', 'UNDO'}

    script_name: StringProperty(
        name="Script Name",
        description="Filename for the new script",
        default="pasted_script.py"
    )

    open_in_editor: BoolProperty(
        name="Open in Text Editor",
        description="Open in Blender Text Editor after creating",
        default=False
    )

    clipboard_preview: StringProperty(name="Code Preview", default="")

    def invoke(self, context, event):
        clipboard = context.window_manager.clipboard.strip()
        if not clipboard:
            self.report({'WARNING'}, "Clipboard is empty! Copy some code first.")
            return {'CANCELLED'}

        lines = [line for line in clipboard.splitlines() if line.strip()]
        line_count = len(clipboard.splitlines())
        first_line = lines[0] if lines else ""
        self.clipboard_preview = f"{line_count} line(s) - '{first_line[:50]}...'" if len(first_line) > 50 else f"{line_count} line(s)"

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
        layout.label(text=f"Clipboard: {self.clipboard_preview}", icon='COPYDOWN')
        layout.prop(self, "script_name")
        layout.prop(self, "open_in_editor")

    def execute(self, context):
        clipboard = context.window_manager.clipboard
        if not clipboard.strip():
            self.report({'ERROR'}, "Clipboard is empty.")
            return {'CANCELLED'}

        script_dir = get_script_dir(context)
        fname = self.script_name.strip()
        if not fname.lower().endswith(".py"):
            fname += ".py"

        filepath = os.path.join(script_dir, fname)
        if os.path.exists(filepath):
            self.report({'ERROR'}, f"File already exists: {fname}")
            return {'CANCELLED'}

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
    bl_description = "Open this script in Blender's Text Editor"

    path: StringProperty(name="Script Path")

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
                self.report({'ERROR'}, f"Could not load file into Blender: {e}")
                return {'CANCELLED'}

        found_area = False
        for area in context.window.screen.areas:
            if area.type == 'TEXT_EDITOR':
                area.spaces.active.text = text
                found_area = True
                break

        if found_area:
            self.report({'INFO'}, f"Opened {filename} in Text Editor")
        else:
            self.report({'INFO'}, f"Loaded {filename}. Switch to Scripting workspace to view.")
        return {'FINISHED'}


class SCRIPT_OT_OpenExternal(Operator):
    bl_idname = "script_manager.open_external"
    bl_label = "Open in System Editor"
    bl_description = "Open script file with your operating system's default editor (e.g. VS Code)"

    path: StringProperty(name="Script Path")

    def execute(self, context):
        if not os.path.isfile(self.path):
            self.report({'ERROR'}, f"File not found: {self.path}")
            return {'CANCELLED'}

        try:
            if sys.platform == "win32":
                os.startfile(self.path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self.path])
            else:
                subprocess.Popen(["xdg-open", self.path])
            self.report({'INFO'}, f"Opened {os.path.basename(self.path)} in external editor")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Could not open external editor: {e}")
            return {'CANCELLED'}


class SCRIPT_OT_DeleteScript(Operator):
    bl_idname = "script_manager.delete_script"
    bl_label = "Delete Script"
    bl_description = "Delete this script file"
    bl_options = {'REGISTER', 'UNDO'}

    script_name: StringProperty(name="Script Name")

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
    bl_description = "Add or remove this script from favorites"

    script_name: StringProperty()

    def execute(self, context):
        wm = context.window_manager
        script_dir = get_script_dir(context)
        metadata = load_metadata(script_dir)

        for item in wm.script_list:
            if item.name == self.script_name:
                item.favorite = not item.favorite
                if item.name not in metadata:
                    metadata[item.name] = {}
                metadata[item.name]["favorite"] = item.favorite
                metadata[item.name]["tags"] = item.tags
                metadata[item.name]["custom_display_name"] = item.custom_display_name
                state = "added to" if item.favorite else "removed from"
                self.report({'INFO'}, f"{item.name} {state} favorites.")
                break

        save_metadata(script_dir, metadata)
        return {'FINISHED'}


class SCRIPT_OT_ToggleEditMode(Operator):
    bl_idname = "script_manager.toggle_edit_mode"
    bl_label = "Toggle Edit Mode"
    bl_description = "Toggle metadata and name editing for this script"

    script_name: StringProperty()

    def execute(self, context):
        wm = context.window_manager
        for item in wm.script_list:
            if item.name == self.script_name:
                item.edit_mode = not item.edit_mode
                break
        return {'FINISHED'}


class SCRIPT_OT_SaveMetadata(Operator):
    bl_idname = "script_manager.save_metadata"
    bl_label = "Save Metadata"
    bl_description = "Save display name and tags for this script"

    script_name: StringProperty()

    def execute(self, context):
        wm = context.window_manager
        script_dir = get_script_dir(context)
        metadata = load_metadata(script_dir)

        for item in wm.script_list:
            if item.name == self.script_name:
                if item.name not in metadata:
                    metadata[item.name] = {}
                metadata[item.name]["favorite"] = item.favorite
                metadata[item.name]["tags"] = item.tags
                metadata[item.name]["custom_display_name"] = item.custom_display_name
                item.edit_mode = False
                self.report({'INFO'}, f"Saved settings for {item.name}")
                break

        save_metadata(script_dir, metadata)
        register_dynamic_operators(context)
        return {'FINISHED'}


class SCRIPT_OT_RefreshList(Operator):
    bl_idname = "script_manager.refresh_list"
    bl_label = "Refresh Scripts"
    bl_description = "Reload script files from folder"

    def execute(self, context):
        wm = context.window_manager
        script_dir = get_script_dir(context)

        wm.script_list.clear()
        metadata = load_metadata(script_dir)
        files = list_scripts(script_dir)

        register_dynamic_operators(context)

        for fname in files:
            item = wm.script_list.add()
            item.name = fname
            item.path = os.path.join(script_dir, fname)
            meta = metadata.get(fname, {})
            item.favorite = meta.get("favorite", False)
            item.tags = meta.get("tags", "")
            item.custom_display_name = meta.get("custom_display_name", "")
            slug = make_operator_slug(fname)
            item.operator_idname = f"script_manager.run_{slug}"

        self.report({'INFO'}, f"Refreshed ({len(files)} scripts)")
        return {'FINISHED'}


class SCRIPT_OT_OpenScriptFolder(Operator):
    bl_idname = "script_manager.open_script_folder"
    bl_label = "Open Scripts Folder"
    bl_description = "Open the scripts folder in your system's file manager"

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
            self.report({'ERROR'}, "Scripts folder does not exist.")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# UI Panel
# ---------------------------------------------------------------------------

class SCRIPT_PT_ScriptManagerPanel(Panel):
    bl_label = "Script Shelf"
    bl_idname = "SCRIPT_PT_script_manager"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Tool Shelf'

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager

        # Action Bar: New, Paste, Refresh, Open Folder
        top_row = layout.row(align=True)
        top_row.operator("script_manager.new_script", text="New", icon='ADD')
        top_row.operator("script_manager.new_from_clipboard", text="Paste", icon='PASTEDOWN')
        top_row.operator("script_manager.refresh_list", text="", icon='FILE_REFRESH')
        top_row.operator("script_manager.open_script_folder", text="", icon='FILE_FOLDER')

        # Filter and Favorites row
        filter_row = layout.row(align=True)
        filter_row.prop(wm, "script_manager_search", text="", icon='VIEWZOOM', placeholder="Filter scripts...")
        fav_icon = 'SOLO_ON' if wm.script_manager_fav_only else 'SOLO_OFF'
        filter_row.prop(wm, "script_manager_fav_only", text="", icon=fav_icon)

        # Empty state handling
        if len(wm.script_list) == 0:
            box = layout.box()
            box.label(text="No scripts found.", icon='INFO')
            box.label(text="Click 'New' or 'Paste' above to add one.")
            box.operator("script_manager.refresh_list", text="Scan Folder", icon='FILE_REFRESH')
            return

        search_query = wm.script_manager_search.strip().lower()

        # Scripts List
        visible_count = 0
        for item in wm.script_list:
            if wm.script_manager_fav_only and not item.favorite:
                continue

            display_name = item.custom_display_name.strip() or os.path.splitext(item.name)[0]

            if search_query:
                combined_text = f"{item.name} {display_name} {item.tags}".lower()
                if search_query not in combined_text:
                    continue

            visible_count += 1
            box = layout.box()
            main_row = box.row(align=True)

            op_name = item.operator_idname if item.operator_idname in DYNAMIC_OPERATOR_CLASSES else "script_manager.run_script"
            run_btn = main_row.operator(op_name, text=display_name, icon='PLAY')
            if op_name == "script_manager.run_script":
                run_btn.path = item.path

            main_row.operator("script_manager.open_script", text="", icon='TEXT').path = item.path

            fav_btn = main_row.operator("script_manager.toggle_favorite", text="", icon='SOLO_ON' if item.favorite else 'SOLO_OFF')
            fav_btn.script_name = item.name

            edit_toggle = main_row.operator("script_manager.toggle_edit_mode", text="", icon='GREASEPENCIL')
            edit_toggle.script_name = item.name

            if item.edit_mode:
                col = box.column(align=True)
                col.prop(item, "custom_display_name", text="Display Name")
                col.prop(item, "tags", text="Tags")

                btn_row = col.row(align=True)
                save_btn = btn_row.operator("script_manager.save_metadata", text="Save", icon='CHECKMARK')
                save_btn.script_name = item.name

                ext_btn = btn_row.operator("script_manager.open_external", text="Ext. Editor", icon='WINDOW')
                ext_btn.path = item.path

                del_btn = btn_row.operator("script_manager.delete_script", text="Delete", icon='TRASH')
                del_btn.script_name = item.name
            elif item.tags.strip():
                tag_row = box.row()
                tag_row.label(text=f"Tags: {item.tags}", icon='TAGS')

        if visible_count == 0:
            layout.label(text="No scripts match filter criteria.", icon='INFO')


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
    SCRIPT_OT_OpenExternal,
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
    bpy.types.WindowManager.script_manager_fav_only = BoolProperty(name="Favorites Only", default=False)
    bpy.types.WindowManager.script_manager_search = StringProperty(name="Search", default="")

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
    del bpy.types.WindowManager.script_manager_fav_only
    del bpy.types.WindowManager.script_manager_search


if __name__ == "__main__":
    register()
