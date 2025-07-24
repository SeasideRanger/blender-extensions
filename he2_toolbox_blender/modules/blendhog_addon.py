import bpy
import re
import json
import subprocess
import os
import sys

def clean_object_name(name):
    # Remove the "Gismo." prefix if it exists
    if name.startswith("Gismo."):
        name = name[len("Gismo."):]
    # Split the name on periods and remove segments that are purely numeric
    parts = name.split(".")
    parts = [part for part in parts if not part.isdigit()]
    return ".".join(parts)

class OBJECT_OT_generate_blendhog_gismo(bpy.types.Operator):
    """Generate Blendhog Gismo from selected objects"""
    bl_idname = "object.generate_blendhog_gismo"
    bl_label = "Generate Blendhog Gismo"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        import re
        for obj in context.selected_objects:
            bpy.context.view_layer.objects.active = obj

            original_name = obj.name
            name_segment = None

            # Determine name_segment: if suffix after dot is numeric-only, use prefix before dot
            if '.' in original_name:
                parts = original_name.rsplit('.', 1)
                prefix, suffix = parts[0], parts[1]
                if re.fullmatch(r"\d+", suffix):
                    name_segment = prefix
                # else leave name_segment None, will compute after renaming

            # 1. Prefix name with "Gismo." if not already
            if not obj.name.startswith("Gismo."):
                obj.name = f"Gismo.{original_name}"
                self.report({'INFO'}, f"Renamed object to: {obj.name}")
            else:
                self.report({'INFO'}, f"Object {obj.name} already has Gismo prefix.")

            # 2. Enable JSON display params
            if hasattr(obj, "use_display_json_parameters"):
                obj.use_display_json_parameters = True
            else:
                self.report({'WARNING'}, f"Object {obj.name} missing 'use_display_json_parameters'.")

            # 3. Set json_parameters[3].string_value to object's name segment
            if hasattr(obj, "json_parameters") and len(obj.json_parameters) > 3:
                # if not set from numeric-suffix logic, extract last segment after any dots
                if name_segment is None:
                    name_segment = obj.name.split('.')[-1]
                obj.json_parameters[3].string_value = name_segment
                self.report({'INFO'}, f"Set json_parameters[3] to: {name_segment}")
            else:
                self.report({'WARNING'}, f"Object {obj.name} missing valid 'json_parameters'.")
        return {'FINISHED'}


class OBJECT_OT_adjust_blendhog_scale(bpy.types.Operator):
    """Adjust object and JSON scale based on HSON or Blender scale"""
    bl_idname = "object.adjust_blendhog_scale"
    bl_label = "Adjust Blendhog Scale"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        for obj in context.selected_objects:
            # 1. Skip any object not starting with "Gismo." prefix
            if not obj.name.startswith("Gismo."):
                self.report({'INFO'}, f"Skipping {obj.name}: not a Gismo object.")
                continue

            # 2. Try to find an HSON scale parameter (name before any ':')
            hson_scale = None
            if hasattr(obj, 'hson_parameters'):
                for p in obj.hson_parameters:
                    key = p.name.split(':', 1)[0].strip().lower()
                    if key == 'scale':
                        hson_scale = p
                        break

            # 3. Determine scale value
            if hson_scale:
                raw = getattr(hson_scale, 'float_value', None) or getattr(hson_scale, 'value', None)
                try:
                    scale_val = float(raw)
                except (TypeError, ValueError):
                    self.report({'WARNING'}, f"Invalid HSON scale on {obj.name}: {raw!r}")
                    continue
            else:
                scale_val = obj.scale[0]
                self.report({'INFO'}, f"Using Blender scale for {obj.name}: {scale_val}")

            # 4. Apply scale to object uniformly
            obj.scale = (scale_val, scale_val, scale_val)

            # 5. Sync to JSON parameter named "scale" (before colon) or index 4
            if hasattr(obj, 'json_parameters') and len(obj.json_parameters) > 4:
                json_scale = None
                # named lookup with split before colon
                for jp in obj.json_parameters:
                    name_key = jp.name.split(':', 1)[0].strip().lower()
                    if name_key == 'scale':
                        json_scale = jp
                        break

                if not json_scale:
                    # fallback to index 4
                    json_scale = obj.json_parameters[4]
                    self.report({'INFO'}, f"No named JSON scale param; using index 4 for {obj.name}")

                old_json_val = getattr(json_scale, 'float_value', None)
                json_scale.float_value = scale_val
                self.report({'INFO'}, f"json_parameters scale updated from {old_json_val} to {scale_val} on {obj.name}")
            else:
                self.report({'WARNING'}, f"Object {obj.name} missing valid json_parameters")

        return {'FINISHED'}


# Blendhog Parameter Transfer logic
special_aliases = []
excluded_parameters = []

def load_special_aliases(path=None):
    # allow an override, but default to your addon folder
    if path is None:
        # assumes this file lives one level inside your addon folder
        addon_dir = os.path.dirname(__file__)
        path = os.path.join(addon_dir, 'modules', 'parameters.json')

    with open(path, 'r') as f:
        data = json.load(f)

    raw_groups = data.get("special_aliases", [])
    special_aliases = []

    for group in raw_groups:
        # validate that we have at least [base, alias]
        if not isinstance(group, list) or len(group) < 2:
            continue

        # use the lowercase base name for consistent lookups
        base = str(group[0]).strip().lower()
        # strip and store all other entries as aliases
        aliases = [str(alias).strip() for alias in group[1:]]
        special_aliases[base] = aliases

    return special_aliases

def load_excluded_parameters(path=None):
    if path is None:
        addon_dir = os.path.dirname(__file__)
        path = os.path.join(addon_dir, 'modules', 'parameters.json')
    with open(path, 'r') as f:
        data = json.load(f)
    excluded = data.get("excluded_parameters", [])
    # ensure all entries are strings and trimmed
    return [str(p).strip() for p in excluded if isinstance(p, str)]

def strip_hson_type(raw_name: str) -> str:
    parts = raw_name.split(':')
    if len(parts) > 1 and parts[-1].lower() in {"float", "int", "string", "bool", "list"}:
        base = ":".join(parts[:-1])
    else:
        base = raw_name
    return base.lower().strip()

def extract_json_segments(raw_name: str) -> list:
    s = raw_name.strip()
    if s.startswith('[') and s.endswith(']') and ',' in s:
        inner = s[1:-1].strip()
        parts = [seg.strip().strip("'\"") for seg in inner.split(',')]
        return [part.lower() for part in parts if part]
    s = re.sub(r"^[\[\]\{\}\(\)\<\>]+|[\[\]\{\}\(\)\<\>]+$", "", s)
    s = s.strip("'\"")
    s = s.lower()
    s = re.sub(r"[^0-9a-z_/]", "", s)
    return [seg for seg in s.split('/') if seg]

def normalize_segment(seg: str) -> str:
    # drop ALL non-alphanumeric, collapse to lowercase
    return re.sub(r'[^0-9a-z]', '', seg.lower())

def match_segment(base_h: str, segs: list) -> bool:
    # Split base_h by ':' and compare to segments
    base_parts = [normalize_segment(p) for p in base_h.split(":")]
    segs_norm = [normalize_segment(s) for s in segs]
    # Match if all base_parts are found in order in segs
    for i in range(len(segs_norm) - len(base_parts) + 1):
        if segs_norm[i:i+len(base_parts)] == base_parts:
            return True
    return False

def match_alias_candidate(j_item, candidate: str) -> bool:
    cand_segs = extract_json_segments(candidate)
    if not cand_segs:
        return False
    j_segs = extract_json_segments(j_item.name)
    if len(cand_segs) == 1:
        base = cand_segs[0]
        if len(j_segs) == 2 and j_segs[0] == base and j_segs[1] in {"float", "int", "bool", "string"}:
            return True
    result = all(any(c == js for js in j_segs) for c in cand_segs)
    return result

def infer_type_from_hson(h_item) -> str:
    if hasattr(h_item, "enum_items") and len(h_item.enum_items):
        return "enum"
    if hasattr(h_item, "float_value"):
        return "float"
    if hasattr(h_item, "bool_value"):
        return "bool"
    if hasattr(h_item, "list_value"):
        lv = h_item.list_value
        if lv and hasattr(lv[0], "liststring"):
            return "string"
    return ""

def copy_value_by_type(h_item, j_item, dt: str):
    if dt == "float":
        j_item.float_value = h_item.float_value
    elif dt == "int":
        j_item.int_value = h_item.int_value
    elif dt == "bool":
        j_item.bool_value = h_item.bool_value
    elif dt == "string":
        if hasattr(h_item, "list_value") and h_item.list_value:
            j_item.string_value = h_item.list_value[0].liststring
        else:
            j_item.string_value = h_item.string_value
    elif dt == "list":
        if hasattr(j_item, "list_value"):
            while len(j_item.list_value) > 0:
                 j_item.list_value.remove(0)
            for elem in h_item.list_value:
                new_elem = j_item.list_value.add()
                if hasattr(elem, "liststring"):
                    new_elem.liststring = elem.liststring
                elif hasattr(elem, "value"):
                    new_elem.liststring = str(elem.value)
        else:
            j_item.string_value = h_item.list_value[0].liststring
    elif dt == "enum":
        # Replicate enum selection by copying selected state for each enum_item
        if hasattr(h_item, "enum_items") and hasattr(j_item, "enum_items"):
            for h_enum, j_enum in zip(h_item.enum_items, j_item.enum_items):
                j_enum.selected = h_enum.selected
            # Optionally update string_value/enum_value to match selected
            selected = next((e for e in j_item.enum_items if getattr(e, "selected", False)), None)
            if selected:
                if hasattr(j_item, "string_value"):
                    j_item.string_value = selected.value
                if hasattr(j_item, "enum_value"):
                    j_item.enum_value = selected.value
    else:
        raise ValueError(f"Unknown data type '{dt}'")

def _force_update_enum_selection(enum_item, obj):
    # This mimics the update_enum_selection logic for JSON parameters
    parent_param = None
    for param in obj.json_parameters:
        if "enum" in param.name:
            for item in param.enum_items:
                if item == enum_item:
                    parent_param = param
                    break
        if parent_param is not None:
            break
    if parent_param:
        for item in parent_param.enum_items:
            if item != enum_item and enum_item.selected:
                item.selected = False
        if enum_item.selected:
            parent_param.string_value = enum_item.value

def transfer_enum_value(h_item, j_item):
    if hasattr(h_item, "enum_items") and len(h_item.enum_items) > 0:
        selected_value = None
        selected_index = None
        for idx, enum in enumerate(h_item.enum_items):
            if getattr(enum, "selected", False):
                selected_value = str(getattr(enum, "value", None))
                selected_index = idx
                break
        print(f"[DEBUG] HSON selected index: {selected_index}, value: {selected_value}")
        # Always clear all selections in JSON enum_items before applying new selection
        for enum in j_item.enum_items:
            enum.selected = False
        if selected_value is not None:
            found = False
            found_index = None
            for idx, enum in enumerate(j_item.enum_items):
                enum_val = str(getattr(enum, "value", None))
                if enum_val.lower() == selected_value.lower():
                    enum.selected = True
                    _force_update_enum_selection(enum, bpy.context.object)
                    found = True
                    found_index = idx
                    break
            print(f"[DEBUG] JSON selected index after transfer: {found_index}")
            if hasattr(j_item, "enum_value"):
                j_item.enum_value = selected_value
            if hasattr(j_item, "string_value"):
                j_item.string_value = selected_value
            if not found:
                print(f"[DEBUG] Could not find matching enum value '{selected_value}' for {h_item.name} in {j_item.name}")
        else:
            print(f"[DEBUG] Could not find selected enum for {h_item.name}")
    else:
        print(f"[DEBUG] h_item {h_item.name} does not have enum_items or enum_value")

class OBJECT_OT_universal_parameter_transfer(bpy.types.Operator):
    """Sync HSON parameters to JSON parameters for all selected objects"""
    bl_idname = "object.universal_parameter_transfer"
    bl_label = "Blendhog Parameter Transfer"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        for obj in context.selected_objects:
            if not hasattr(obj, "hson_parameters") or not hasattr(obj, "json_parameters"):
                self.report({'WARNING'}, f"Object '{obj.name}' skipped: missing 'hson_parameters' or 'json_parameters'.")
                continue

            json_entries = list(obj.json_parameters)
            json_with_segs = [(j, extract_json_segments(j.name)) for j in json_entries]

            transferred = set()
            not_transferred = set()
            enum_debug = []
            for h_item in obj.hson_parameters:
                raw_h = h_item.name
                base_h = strip_hson_type(raw_h)
                matches = [j_item for (j_item, segs) in json_with_segs if match_segment(base_h, segs)]
                if len(matches) == 0 and base_h in special_aliases:
                    for candidate in special_aliases[base_h]:
                        this_round = [j_item for j_item in json_entries if match_alias_candidate(j_item, candidate)]
                        if this_round:
                            matches = this_round
                            break
                if not matches:
                    not_transferred.add(raw_h)
                    continue
                for j_item in matches:
                    segs = extract_json_segments(j_item.name)
                    full_path = "/".join(segs)
                    if full_path in excluded_parameters:
                        continue
                    dt = ""
                    if segs and segs[-1] in {"float", "int", "string", "bool", "list"}:
                        dt = segs[-1]
                    else:
                        dt = infer_type_from_hson(h_item)
                        if dt == "":
                            not_transferred.add(raw_h)
                            continue
                    try:
                        # Handle enum_items if present
                        if dt == "enum" and hasattr(h_item, "enum_items"):
                            transfer_enum_value(h_item, j_item)
                            # After transfer, compare enum_items selection
                            h_selected = [getattr(e, "selected", False) for e in h_item.enum_items]
                            j_selected = [getattr(e, "selected", False) for e in j_item.enum_items]
                            enum_debug.append(
                                f"[DEBUG] Enum transfer check: {h_item.name} → {j_item.name} | hson: {h_selected} json: {j_selected}"
                            )
                        else:
                            copy_value_by_type(h_item, j_item, dt)
                        transferred.add(raw_h)
                    except Exception as e:
                        self.report({'WARNING'}, f"{raw_h} → {j_item.name}: error copying ({e})")
                        not_transferred.add(raw_h)
            # Debug print for all parameters
            print(f"[DEBUG] For object '{obj.name}':")
            print(f"  Transferred parameters ({len(transferred)}): {sorted(transferred)}")
            print(f"  Not transferred parameters ({len(not_transferred)}): {sorted(not_transferred)}")
            for line in enum_debug:
                print(line)

            # --- Additional debug: compare all hson and json parameters ---
            print(f"[DEBUG] --- Full parameter comparison for '{obj.name}' ---")
            hson_params = list(obj.hson_parameters)
            json_params = list(obj.json_parameters)
            print(f"[DEBUG] hson_parameters ({len(hson_params)}):")
            for idx, h in enumerate(hson_params):
                if hasattr(h, "enum_items") and len(h.enum_items) > 0:
                    selected = [getattr(e, "selected", False) for e in h.enum_items]
                    values = [getattr(e, "value", None) for e in h.enum_items]
                    print(f"    [{idx}] {h.name} (enum) selected: {selected} values: {values}")
                elif hasattr(h, "float_value"):
                    print(f"    [{idx}] {h.name} float: {getattr(h, 'float_value', None)}")
                elif hasattr(h, "int_value"):
                    print(f"    [{idx}] {h.name} int: {getattr(h, 'int_value', None)}")
                elif hasattr(h, "bool_value"):
                    print(f"    [{idx}] {h.name} bool: {getattr(h, 'bool_value', None)}")
                elif hasattr(h, "string_value"):
                    print(f"    [{idx}] {h.name} string: {getattr(h, 'string_value', None)}")
                else:
                    print(f"    [{idx}] {h.name} (unknown type)")

            print(f"[DEBUG] json_parameters ({len(json_params)}):")
            for idx, j in enumerate(json_params):
                if hasattr(j, "enum_items") and len(j.enum_items) > 0:
                    selected = [getattr(e, "selected", False) for e in j.enum_items]
                    values = [getattr(e, "value", None) for e in j.enum_items]
                    print(f"    [{idx}] {j.name} (enum) selected: {selected} values: {values}")
                elif hasattr(j, "float_value"):
                    print(f"    [{idx}] {j.name} float: {getattr(j, 'float_value', None)}")
                elif hasattr(j, "int_value"):
                    print(f"    [{idx}] {j.name} int: {getattr(j, 'int_value', None)}")
                elif hasattr(j, "bool_value"):
                    print(f"    [{idx}] {j.name} bool: {getattr(j, 'bool_value', None)}")
                elif hasattr(j, "string_value"):
                    print(f"    [{idx}] {j.name} string: {getattr(j, 'string_value', None)}")
                else:
                    print(f"    [{idx}] {j.name} (unknown type)")
            print(f"[DEBUG] --- End parameter comparison for '{obj.name}' ---")
        self.report({'INFO'}, "Blendhog Parameter Transfer complete.")
        return {'FINISHED'}

class OBJECT_OT_open_parameters_json(bpy.types.Operator):
    bl_idname = "blendhog.open_parameters_json"
    bl_label = "Parameter Transfer: open parameters.json"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        addon_dir = os.path.dirname(__file__)
        path = os.path.join(addon_dir, "parameters.json")
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.call(["open", path])
            else:
                subprocess.call(["xdg-open", path])
        except Exception as e:
            self.report({'ERROR'}, f"Failed to open parameters.json: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}

class OBJECT_OT_toggle_display_json_parameters(bpy.types.Operator):
    bl_idname = "object.toggle_display_json_parameters"
    bl_label = "Toggle Display JSON Parameters"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Store selected objects
        selected_objects = context.selected_objects

        # Deselect all objects first
        bpy.ops.object.select_all(action='DESELECT')

        for obj in selected_objects:
            original_name = obj.name

            # Split name on dots to handle existing suffixes
            parts = original_name.split('.')
            first_part = parts[0]
            suffix_parts = parts[1:]  # e.g., ['001'] if name was "Gismo43.001"

            # Check if the first part ends with digits and insert a dot before them
            match = re.match(r"^(.+?)(\d+)$", first_part)
            if match:
                prefix, digits = match.groups()
                # Only insert dot if it's not already separated
                if not prefix.endswith("."):
                    new_first = f"{prefix}.{digits}"
                else:
                    new_first = first_part  # already has a dot
            else:
                new_first = first_part

            # Reassemble the full name with any suffixes preserved
            if suffix_parts:
                new_name = ".".join([new_first] + suffix_parts)
            else:
                new_name = new_first

            # Rename if changed
            if new_name != original_name:
                print(f"Renaming '{original_name}' to '{new_name}'")
                obj.name = new_name

            # Select the current (possibly renamed) object
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj

            # Check and update the property based on Set JSON Parameter Manually
            if hasattr(obj, "use_display_json_parameters"):
                obj.use_display_json_parameters = context.scene.show_json_parameters
                print(f"Updated {obj.name}: use_display_json_parameters set to {context.scene.show_json_parameters}")
            else:
                print(f"Skipped {obj.name}: use_display_json_parameters property not found")

            # Deselect the current object
            obj.select_set(False)

        # Reselect all originally selected objects
        for obj in selected_objects:
            obj.select_set(True)

        self.report({'INFO'}, "Set JSON Parameter Manually updated for selected objects.")
        return {'FINISHED'}

class VIEW3D_PT_blendhog_module(bpy.types.Panel):
    bl_label = "Blendhog Module"
    bl_idname = "VIEW3D_PT_blendhog_module"
    bl_category = "HE2 Tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    
    def draw(self, context):
        layout = self.layout
        obj = context.object
        layout.operator("object.generate_blendhog_gismo", text="Generate Blendhog Gismo")
        layout.operator("object.adjust_blendhog_scale", text="Adjust Gismo Scale")
        layout.operator("object.universal_parameter_transfer", text="Blendhog Parameter Transfer")
        layout.operator("blendhog.open_parameters_json")
        row = layout.row(align=True)
        row.prop(context.scene, "show_json_parameters")
        row.operator("object.toggle_display_json_parameters", text="Update JSON Parameter Manually")

def register():
    bpy.utils.register_class(OBJECT_OT_generate_blendhog_gismo)
    bpy.utils.register_class(OBJECT_OT_adjust_blendhog_scale)
    bpy.utils.register_class(OBJECT_OT_universal_parameter_transfer)
    bpy.utils.register_class(OBJECT_OT_toggle_display_json_parameters)
    bpy.utils.register_class(VIEW3D_PT_blendhog_module)
    bpy.utils.register_class(OBJECT_OT_open_parameters_json)
    bpy.types.Object.show_json_parameters = bpy.props.BoolProperty(
        name="True/False",
        default=False
    )
    bpy.types.Scene.show_json_parameters = bpy.props.BoolProperty(
        name="True/False",
        default=False
    )

def unregister():
    bpy.utils.unregister_class(VIEW3D_PT_blendhog_module)
    bpy.utils.unregister_class(OBJECT_OT_toggle_display_json_parameters)
    bpy.utils.unregister_class(OBJECT_OT_universal_parameter_transfer)
    bpy.utils.unregister_class(OBJECT_OT_adjust_blendhog_scale)
    bpy.utils.unregister_class(OBJECT_OT_generate_blendhog_gismo)
    bpy.utils.unregister_class(OBJECT_OT_open_parameters_json)
    del bpy.types.Object.show_json_parameters
    del bpy.types.Scene.show_json_parameters

if __name__ == "__main__":
    load_special_aliases()
    load_excluded_parameters()
    register()