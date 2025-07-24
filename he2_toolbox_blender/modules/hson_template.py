import bpy, json, os, re, uuid

def get_templates(self, context):
    items = []
    try:
        addon_root = __name__.split('.')[0]
        prefs = context.preferences.addons[addon_root].preferences
        addon_dir = os.path.dirname(__file__)
        dirs = [prefs.templates_path or os.path.join(addon_dir, "templates")]
        if prefs.debug_templates_path:
            dirs.append(prefs.debug_templates_path)
        seen = set()
        for templates_dir in dirs:
            if not os.path.isdir(templates_dir):
                continue
            for fn in os.listdir(templates_dir):
                if not fn.lower().endswith(".json") or fn in seen:
                    continue
                seen.add(fn)
                items.append((fn, fn, f"From {os.path.basename(templates_dir)}"))
    except Exception as e:
        pass
    return items

def strip_template_prefix(name):
    try:
        m = re.match(r"^[0-9A-Z]+_(.*)$", name)
        return m.group(1) if m else name
    except Exception:
        return name

def nest_parameters(flat_params):
    nested = {}
    try:
        for key, value in flat_params.items():
            parts = key.split(":")
            current = nested
            for i, part in enumerate(parts):
                m = re.match(r"^([a-zA-Z_]+)(\d+)$", part)
                if m:
                    base, idx = m.group(1), int(m.group(2))
                    if base not in current or not isinstance(current[base], list):
                        current[base] = []
                    current = current[base]
                    while len(current) <= idx:
                        current.append({})
                    if i == len(parts) - 1:
                        current[idx] = value
                    else:
                        current = current[idx]
                else:
                    if i == len(parts) - 1:
                        current[part] = value
                    else:
                        if part not in current or not isinstance(current[part], dict):
                            current[part] = {}
                        current = current[part]
        return nested
    except Exception as e:
        return nested

def update_hson_parameters(obj, params, types):
    try:
        nested_params = nest_parameters(params)
        obj.hson_parameters.clear()

        def process_nested_params(nested, parent_key=""):
            for key, value in nested.items():
                full_key = f"{parent_key}:{key}" if parent_key else key
                if isinstance(value, dict):
                    process_nested_params(value, full_key)
                elif isinstance(value, list) and all(isinstance(v, dict) for v in value):
                    for idx, sub_value in enumerate(value):
                        process_nested_params(sub_value, f"{full_key}[{idx}]")
                else:
                    if parent_key:
                        lookup_root = parent_key.replace("[", "").replace("]", "")
                        lookup_key = f"{lookup_root}:{key}"
                    else:
                        lookup_key = key
                    field_type = types.get(lookup_key, "")
                    ft_lower = field_type.lower()
                    if "::" in field_type:
                        resolved_type = "enum"
                    elif isinstance(value, list) and len(value) == 3 and all(isinstance(n, (int, float)) for n in value):
                        resolved_type = "vector"
                    elif isinstance(value, list):
                        resolved_type = "list"
                    elif ft_lower in ["int","int8","int16","int32","int64",
                                      "uint32","uint8","uint16","uint64"]:
                        resolved_type = "int"
                    elif ft_lower in ["float","float32"]:
                        resolved_type = "float"
                    elif "bool" in ft_lower:
                        resolved_type = "bool"
                    elif ft_lower == "string":
                        resolved_type = "list"
                        if not isinstance(value, list):
                            value = [value]
                    elif ft_lower == "vector3":
                        resolved_type = "vector"
                    else:
                        resolved_type = "list"
                    item = obj.hson_parameters.add()
                    item.name = f"{lookup_key}:{resolved_type}"
                    try:
                        if resolved_type == "list":
                            if isinstance(value, list) and value:
                                for element in value:
                                    li = item.list_value.add()
                                    li.liststring = str(element)
                                    if ft_lower == "object_reference":
                                        for cand in bpy.data.objects:
                                            if getattr(cand, "hson_uuid", None) == li.liststring:
                                                li.object_value = cand
                                                break
                            else:
                                li = item.list_value.add()
                                li.liststring = ""
                        elif resolved_type == "float":
                            item.float_value = float(value)
                        elif resolved_type == "int":
                            item.int_value = int(value)
                        elif resolved_type == "bool":
                            item.bool_value = bool(value)
                        elif resolved_type == "enum":
                            item.enum_items.clear()
                            item.string_value = "0" if value in ("", None) else str(value)
                        elif resolved_type == "vector":
                            item.vector_value = (
                                value if isinstance(value, (list, tuple)) and len(value) == 3
                                else (0.0, 0.0, 0.0)
                            )
                        else:
                            item.string_value = str(value)
                            if ft_lower == "object_reference":
                                for cand in bpy.data.objects:
                                    if getattr(cand, "hson_uuid", None) == item.string_value:
                                        item.object_value = cand
                                        break
                    except Exception:
                        pass
        process_nested_params(nested_params)
    except Exception:
        pass

def make_enum_update_callback(lookup_key):
    def update(self, context):
        prop_name = f"hson_enum_{lookup_key.replace(':','_')}"
        new_val = getattr(self, prop_name)
        for param in self.hson_parameters:
            if param.name.startswith(f"{lookup_key}:enum"):
                param.string_value = new_val
                break
    return update

def register_enum_properties(obj, template_data, types, reporter=None):
    cls = obj.__class__

    for lookup_key, ftype in types.items():
        if "::" not in ftype:
            continue
        enum_def = (
            template_data["enums"].get(ftype)
            or template_data["enums"].get(ftype.split("::")[-1])
        )
        if not enum_def:
            continue
        items = [
            (key, info.get("name", key), info.get("description", ""))
            for key, info in enum_def["values"].items()
        ]
        valid_keys = {it[0] for it in items}
        stored = None
        for param in obj.hson_parameters:
            if param.name.startswith(f"{lookup_key}:enum"):
                stored = param.string_value
                break
        default_key = enum_def.get("default_key", items[0][0])
        if stored not in valid_keys:
            bad = stored
            stored = default_key
            if reporter:
                reporter(
                    {'WARNING'},
                    f"HSON enum '{lookup_key}' had invalid stored value '{bad}', resetting to '{stored}'"
                )
        prop_name = f"hson_enum_{lookup_key.replace(':','_')}"
        update_cb = make_enum_update_callback(lookup_key)
        setattr(
            cls,
            prop_name,
            bpy.props.EnumProperty(
                name=lookup_key,
                items=items,
                default=stored,
                update=update_cb,
            ),
        )
        setattr(obj, prop_name, stored)

def apply_hson_template_to_object(obj, template_data, imported_params=None):
    def get_struct_fields(struct_name, template_data, _visited=None):
        if _visited is None:
            _visited = set()
        structs = template_data.get("structs", {})
        if struct_name in _visited:
            return []  # Prevents recursion
        _visited.add(struct_name)
        struct_def = structs.get(struct_name, {})
        fields = []
        parent = struct_def.get("parent")
        if parent:
            fields.extend(get_struct_fields(parent, template_data, _visited))
        fields.extend(struct_def.get("fields", []))
        return fields
    try:
        raw_type = obj.data.name if hasattr(obj, "data") and obj.data else obj.name
        object_type = raw_type.split('.')[0] if '.' in raw_type else raw_type
        objects_dict = template_data.get("objects", {})
        template_obj = objects_dict.get(object_type)
        if not template_obj:
            for key, val in objects_dict.items():
                if strip_template_prefix(key) == object_type:
                    template_obj = val
                    break
        if not template_obj:
            return False, "Template for object type not found.", {}
        object_struct = template_obj.get("struct")
        if not object_struct:
            return False, f"No struct defined for object type '{object_type}'."
        struct_def = template_data.get("structs", {}).get(object_struct)
        if not struct_def:
            return False, f"Struct '{object_struct}' not found in template."
        params, types = {}, {}
        params["tags:RangeSpawning:rangeIn"] = imported_params["tags:RangeSpawning:rangeIn"] if imported_params and "tags:RangeSpawning:rangeIn" in imported_params else 500.0
        types["tags:RangeSpawning:rangeIn"] = "float"
        params["tags:RangeSpawning:rangeOut"] = imported_params["tags:RangeSpawning:rangeOut"] if imported_params and "tags:RangeSpawning:rangeOut" in imported_params else 20.0
        types["tags:RangeSpawning:rangeOut"] = "float"

        recognized = {"int", "int8", "int16", "int32", "int64",
                      "uint32", "uint8", "uint16", "uint64",
                      "float", "float32", "bool", "string", "vector", "array", "vector3"}
        all_fields = get_struct_fields(object_struct, template_data)
        for field in all_fields:
            fname = field.get("name")
            ftype = field.get("type")
            if ftype and ftype.lower() == "object_reference":
                ftype = "string"
            ftype_lower = ftype.lower() if ftype else ""
            if ftype_lower == "array" and "subtype" in field:
                subtype = field.get("subtype")
                subtype_lower = subtype.lower()
                arr = template_obj.get(fname, [])
                if imported_params and fname in imported_params:
                    arr = imported_params[fname]
                size = field.get("array_size", 1)
                primitives = {"uint32", "uint8", "uint16", "uint64", "int", "float", "bool", "string", "vector3", "object_reference"}
                if subtype_lower in primitives:
                    new_val = arr if arr and len(arr) >= size else []
                    default_val = (0 if subtype_lower in {"int", "uint32", "uint8", "uint16", "uint64",
                                                          "int8", "int16", "int32", "int64"}
                                   else 0.0 if subtype_lower in {"float", "float32"}
                                   else (False if subtype_lower == "bool" else ""))
                    while len(new_val) < size:
                        new_val.append(default_val)
                    params[fname] = new_val
                    types[fname] = subtype
                else:
                    sub_struct_def = template_data.get("structs", {}).get(subtype)
                    if sub_struct_def:
                        for idx in range(size):
                            for sub_field in sub_struct_def.get("fields", []):
                                sub_fname = sub_field.get("name")
                                sub_ftype = sub_field.get("type")
                                sub_ftype_lower = sub_ftype.lower()
                                new_key = f"{fname}{idx}:{sub_fname}"
                                if isinstance(arr, list) and len(arr) > idx and isinstance(arr[idx], dict):
                                    new_val = arr[idx].get(sub_fname, "")
                                else:
                                    new_val = 0.0 if "float" in sub_ftype_lower else (0 if "int" in sub_ftype_lower else (False if "bool" in sub_ftype_lower else ""))
                                if imported_params and new_key in imported_params:
                                    new_val = imported_params[new_key]
                                if "::" in sub_ftype:
                                    new_val = "" if new_val in ["", None] else str(new_val)
                                if sub_ftype_lower == "string" and not isinstance(new_val, list):
                                    new_val = [new_val]
                                params[new_key] = new_val
                                types[new_key] = sub_ftype
                    else:
                        params[fname] = arr
                        types[fname] = "array"
            elif ftype_lower in recognized or "::" in ftype:
                default_val = template_obj.get(fname, 0.0 if "float" in ftype_lower 
                                               else (0 if "int" in ftype_lower 
                                                     else (False if "bool" in ftype_lower else "")))
                new_val = imported_params[fname] if (imported_params and fname in imported_params) else default_val
                if "::" in ftype:
                    enum_def = template_data.get("enums", {}).get(ftype)
                    if not enum_def:
                        fallback = ftype.split("::")[-1]
                        enum_def = template_data.get("enums", {}).get(fallback)
                    if enum_def:
                        ev = enum_def.get("values", {})
                        if new_val not in ev and str(new_val) not in ev:
                            for k, v in ev.items():
                                if str(v.get("value", "")) == str(new_val):
                                    new_val = k
                                    break
                        if new_val not in ev:
                            new_val = list(ev.keys())[0] if ev else ""
                if ftype_lower == "string" and not isinstance(new_val, list):
                    new_val = [new_val]
                params[fname] = new_val
                types[fname] = ftype
            else:
                sub_struct_def = template_data.get("structs", {}).get(ftype)
                if sub_struct_def:
                    parent = sub_struct_def.get("parent")
                    while parent:
                        parent_struct_def = template_data.get("structs", {}).get(parent)
                        if parent_struct_def:
                            sub_struct_def["fields"].extend(parent_struct_def.get("fields", []))
                            parent = parent_struct_def.get("parent")
                        else:
                            break
                    for sub_field in sub_struct_def.get("fields", []):
                        sub_fname = sub_field.get("name")
                        sub_ftype = sub_field.get("type")
                        sub_ftype_lower = sub_ftype.lower()
                        new_key = f"{fname}:{sub_fname}"
                        if fname in template_obj and isinstance(template_obj[fname], dict):
                            new_val = template_obj[fname].get(sub_fname, "")
                        else:
                            new_val = 0.0 if "float" in sub_ftype_lower else (0 if "int" in sub_ftype_lower else (False if "bool" in sub_ftype_lower else ""))
                        if imported_params and new_key in imported_params:
                            new_val = imported_params[new_key]
                        if "::" in sub_ftype:
                            new_val = "" if new_val in ["", None] else str(new_val)
                        if sub_ftype_lower == "string" and not isinstance(new_val, list):
                            new_val = [new_val]
                        params[new_key] = new_val
                        types[new_key] = sub_ftype
        update_hson_parameters(obj, params, types)
        register_enum_properties(obj, template_data, types)
        if not obj.hson_uuid:
            obj.hson_uuid = "{" + str(uuid.uuid4()).upper() + "}"
        for field_name, ftype in types.items():
            if "::" not in ftype:
                continue
            
            enum_def = (
                template_data.get("enums", {}).get(ftype)
                or template_data.get("enums", {}).get(ftype.split("::")[-1])
            )
            if not enum_def:
                continue

            ev = enum_def.get("values", {})
            for item in obj.hson_parameters:
                if item.name.startswith(f"{field_name}:enum"):
                    item.enum_items.clear()
                    selected_found = False
                    imported_val = str(item.string_value).strip().lower()
                    for enum_key, enum_info in ev.items():
                        enum_item = item.enum_items.add()
                        enum_item.value = enum_key
                        enum_item.name = enum_info.get("name", enum_key)
                        enum_item.description = enum_info.get("description", "")
                        enum_val = str(enum_info.get("value", "")).strip().lower()
                        if imported_val == enum_key.lower() or imported_val == enum_val:
                            enum_item.selected = True
                            selected_found = True
                    if not selected_found and len(item.enum_items) > 0:
                        item.enum_items[0].selected = True
        return True, f"Applied template '{template_data.get('format','')}' to object '{obj.name}'", types
    except Exception as e:
        return False, "hson_template error!", types

class OBJECT_OT_ApplyHSONTemplate(bpy.types.Operator):
    bl_idname = "object.apply_hson_template"
    bl_label = "Apply Template"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.object
        if not obj:
            self.report({'WARNING'}, "No active object.")
            return {'CANCELLED'}

        template_name = context.scene.hson_game_template
        if not template_name:
            self.report({'WARNING'}, "No template selected.")
            return {'CANCELLED'}

        addon_root = __package__.split('.')[0]
        prefs = context.preferences.addons[addon_root].preferences
        addon_dir = os.path.dirname(__file__)
        dirs = [prefs.templates_path or os.path.join(addon_dir, "templates")]
        if prefs.debug_templates_path:
            dirs.append(prefs.debug_templates_path)

        template_file = next(
            (os.path.join(d, template_name) for d in dirs if os.path.isfile(os.path.join(d, template_name))),
            None
        )
        if not template_file:
            self.report({'WARNING'}, f"Template '{template_name}' not found.")
            return {'CANCELLED'}

        try:
            template_data = json.load(open(template_file, 'r'))
        except Exception as e:
            self.report({'ERROR'}, f"Failed to load template: {e}")
            return {'CANCELLED'}

        try:
            success, message, types = apply_hson_template_to_object(obj, template_data)
        except ValueError:
            self.report({'ERROR'}, "Internal: apply_hson_template_to_object must return (success, message, types).")
            return {'CANCELLED'}

        if not success:
            self.report({'WARNING'}, message)
            return {'CANCELLED'}
        register_enum_properties(obj, template_data, types, reporter=self.report)
        self.report({'INFO'}, f"HSON parameters for '{obj.name}':")
        for param in obj.hson_parameters:
            name = param.name
            if name.endswith(":float"):
                val = param.float_value
            elif name.endswith(":bool"):
                val = param.bool_value
            elif name.endswith(":enum"):
                val = param.string_value
            elif name.endswith(":vector"):
                val = tuple(param.list_value)
            elif name.endswith(":list"):
                vals = []
                for li in param.list_value:
                    for attr in ("liststring","listint","listfloat"):
                        v = getattr(li, attr)
                        if v not in (None, "", 0.0, 0):
                            vals.append(v)
                            break
                val = vals
            else:
                val = "(unknown)"
            msg = f"  {name} = {val}"
            print(msg)
            self.report({'INFO'}, msg)

        self.report({'INFO'}, message)
        return {'FINISHED'}

classes = []

def register():
    bpy.utils.register_class(OBJECT_OT_ApplyHSONTemplate)

def unregister():
    bpy.utils.unregister_class(OBJECT_OT_ApplyHSONTemplate)

