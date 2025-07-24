import bpy, json, os, re, mathutils
from bpy.props import StringProperty, CollectionProperty
from collections import OrderedDict
from . import hson_template

class OBJECT_OT_import_hson(bpy.types.Operator):
    bl_idname = "import_scene.hson"
    bl_label = "Import HSON Files"
    
    directory: StringProperty(name="Import Directory", subtype='DIR_PATH')
    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    
    _objects_to_process = []  # List of tuples: (obj_data, collection_name)
    _timer = None
    _batch_size = 25
    _template_data = None  # Loaded template data, if any
    _template_file = None  # Path to the template file
    _template_mod_time = 0  # Last modification time of the template file
    _asset_dict = None     # Dictionary mapping asset name -> blend filepath
    _imported_objects = {} # Dictionary mapping hson_uuid -> imported object

    def prepare_imports(self):
        self._objects_to_process.clear()
        for file_elem in self.files:
            filepath = os.path.join(self.directory, file_elem.name)
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
            except Exception as e:
                self.report({'WARNING'}, f"Failed to load {filepath}: {e}")
                continue
            collection_name = os.path.splitext(file_elem.name)[0]
            for obj_data in data.get("objects", []):
                self._objects_to_process.append((obj_data, collection_name))
    
    def build_asset_dict(self, context):
        asset_dict = {}
        asset_libraries = bpy.context.preferences.filepaths.asset_libraries
        for lib in asset_libraries:
            try:
                for root_dir, _, files_list in os.walk(lib.path):
                    for fn in files_list:
                        if fn.lower().endswith(".blend"):
                            blend_fp = os.path.join(root_dir, fn)
                            try:
                                with bpy.data.libraries.load(blend_fp, link=False) as (src, dst):
                                    for name in src.objects:
                                        asset_dict.setdefault(name, blend_fp)
                            except Exception:
                                pass
            except Exception:
                pass
        return asset_dict

    def create_collection(self, collection_name, context):
        if collection_name in bpy.data.collections:
            return bpy.data.collections[collection_name]
        else:
            coll = bpy.data.collections.new(collection_name)
            context.scene.collection.children.link(coll)
            return coll
    
    def convert_position(self, pos):
        return (pos[0], -pos[2], pos[1])
    
    def convert_scale(self, scale):
        return (scale[0], scale[2], scale[1])
    
    def convert_rotation(self, rot):
        q = mathutils.Quaternion((rot[3], rot[0], -rot[2], rot[1]))
        return q.to_euler('XYZ')

    def flatten_dict(self, d, parent_key="", sep=":"):
        items = {}
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.update(self.flatten_dict(v, new_key, sep=sep))
            else:
                items[new_key] = v
        return items

    def check_reload_template(self):
        if self._template_file and os.path.isfile(self._template_file):
            current_mtime = os.path.getmtime(self._template_file)
            if current_mtime > self._template_mod_time:
                try:
                    with open(self._template_file, "r") as f:
                        self._template_data = json.load(f)
                    self._template_mod_time = current_mtime
                except Exception as e:
                    self.report({'WARNING'}, f"Failed to reload template: {e}")

    def process_batch(self, context):
        count = 0
        while self._objects_to_process and count < self._batch_size:
            self.check_reload_template()
            def normalize_asset_type(value):
                return str(value).strip().lower()
            obj_data, collection_name = self._objects_to_process.pop(0)
            coll = self.create_collection(collection_name, context)
            if obj_data.get("type") == "PathNode":
                mesh = bpy.data.meshes.new(obj_data.get("type", "PathNode"))
                mesh.from_pydata([(0,0,0)], [], [])
                new_obj = bpy.data.objects.new(obj_data.get("name", "PathNode"), mesh)
                new_obj.location = (0,0,0)
            else:
                asset_name_base = obj_data.get("type")
                asset_type = None
                if "parameters" in obj_data and isinstance(obj_data["parameters"], dict) and "Type" in obj_data["parameters"]:
                    asset_type = obj_data["parameters"]["Type"]
                blend_filepath = None
                asset_name = asset_name_base  # default fallback

                if asset_type and self._asset_dict:
                    found_asset = False
                    asset_type_lower = str(asset_type).lower()
                    # Look for candidates that have a dot in their name and begin with asset_name_base + "."
                    for candidate, filepath in self._asset_dict.items():
                        if candidate.startswith(asset_name_base + "."):
                            try:
                                with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
                                    if candidate in data_from.objects:
                                        data_to.objects = [candidate]
                                    else:
                                        continue
                                candidate_obj = data_to.objects[0]
                                if "asset_type" in candidate_obj:
                                    candidate_type_normalized = normalize_asset_type(candidate_obj["asset_type"])
                                    expected_type_normalized = normalize_asset_type(asset_type)
                                    if candidate_type_normalized == expected_type_normalized:
                                        asset_name = candidate
                                        blend_filepath = filepath
                                        found_asset = True
                                        break
                                    else:
                                        bpy.data.objects.remove(candidate_obj, do_unlink=True)
                            except Exception as e:
                                self.report({'WARNING'}, f"Failed to load candidate asset '{candidate}': {e}")
                    if not found_asset:
                        if asset_name_base in self._asset_dict:
                            asset_name = asset_name_base
                            blend_filepath = self._asset_dict[asset_name_base]
                else:
                    if self._asset_dict and asset_name_base in self._asset_dict:
                        asset_name = asset_name_base
                        blend_filepath = self._asset_dict[asset_name_base]

                if blend_filepath:
                    try:
                        with bpy.data.libraries.load(blend_filepath, link=False) as (data_from, data_to):
                            if asset_name in data_from.objects:
                                data_to.objects = [asset_name]
                            else:
                                raise Exception("Asset not found in blend file")
                        appended_obj = data_to.objects[0]
                        appended_obj.name = obj_data.get("name", appended_obj.name)
                        new_obj = appended_obj.copy()
                        if appended_obj.data:
                            new_obj.data = appended_obj.data.copy()
                        if hasattr(new_obj, "asset_data") and new_obj.asset_data is not None:
                            new_obj.asset_data_clear()
                        try:
                            bpy.data.objects.remove(appended_obj, do_unlink=True)
                        except Exception as e:
                            pass
                    except Exception as e:
                        self.report({'WARNING'}, f"Failed to load asset '{asset_name}': {e}. Using cube placeholder instead.")
                        bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0, 0, 0))
                        new_obj = context.active_object
                        new_obj.name = obj_data.get("name", new_obj.name)
                else:
                    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0, 0, 0))
                    new_obj = context.active_object
                    new_obj.name = obj_data.get("name", new_obj.name)

            if new_obj.data:
                new_obj.data.name = obj_data.get("type", new_obj.name)

            new_obj.hson_uuid = obj_data.get("id", "")

            if new_obj.hson_uuid:
                new_obj["DataID"] = new_obj.hson_uuid.replace("{", "").replace("}", "")

            parent_id = obj_data.get("parentId")
            if parent_id:
                parent_obj = self._imported_objects.get(parent_id)
                if parent_obj:
                    new_obj.parent = parent_obj
                    new_obj.matrix_parent_inverse = parent_obj.matrix_world.inverted()
                else:
                    new_obj["hson_parentId"] = parent_id

            if "position" in obj_data:
                new_obj.location = self.convert_position(obj_data["position"])
            if "scale" in obj_data:
                new_obj.scale = self.convert_scale(obj_data["scale"])
            if "rotation" in obj_data:
                new_obj.rotation_mode = 'XYZ'
                new_obj.rotation_euler = self.convert_rotation(obj_data["rotation"])
            if new_obj.name not in coll.objects:
                coll.objects.link(new_obj)
            if new_obj.name in context.scene.collection.objects:
                context.scene.collection.objects.unlink(new_obj)
            flat_params = {}
            if "parameters" in obj_data:
                flat_params = self.flatten_dict(obj_data["parameters"])
            if "tags" in obj_data:
                flat_tags = self.flatten_dict(obj_data["tags"], parent_key="tags")
                flat_params.update(flat_tags)
            if self._template_data:
                success, message, types = hson_template.apply_hson_template_to_object(new_obj, self._template_data, imported_params=flat_params)
                if not success:
                    self.report({'WARNING'}, f"Template apply failed for {new_obj.name}: {message}")
            self._imported_objects[new_obj.hson_uuid] = new_obj
            count += 1
        for obj in list(self._imported_objects.values()):
            parent_id = obj.get("hson_parentId")
            if parent_id:
                parent_obj = self._imported_objects.get(parent_id)
                if parent_obj:
                    obj.parent = parent_obj
                    obj.matrix_parent_inverse = parent_obj.matrix_world.inverted()
                    del obj["hson_parentId"]

    def perform_parenting(self):
        for obj in self._imported_objects.values():
            parent_id = obj.get("parentId", None)
            if parent_id:
                parent_id = str(parent_id).strip()
                if parent_id in self._imported_objects:
                    parent_obj = self._imported_objects[parent_id]
                    obj.parent = parent_obj
                    obj.matrix_parent_inverse = parent_obj.matrix_world.inverted()
    
    def modal(self, context, event):
        if event.type == 'TIMER':
            self.process_batch(context)
            if not self._objects_to_process:
                context.window_manager.event_timer_remove(self._timer)
                self.perform_parenting()
                self.report({'INFO'}, "Import complete.")
                return {'FINISHED'}
        return {'PASS_THROUGH'}
    
    def execute(self, context):
        self._imported_objects = {}
        self.prepare_imports()
        if not self._objects_to_process:
            self.report({'WARNING'}, "No objects found in selected files.")
            return {'CANCELLED'}
        self._asset_dict = self.build_asset_dict(context)
        template_name = context.scene.hson_game_template
        if template_name:
            addon_root = __package__.split('.')[0]
            prefs = context.preferences.addons[addon_root].preferences
            addon_dir = os.path.dirname(__file__)
            dirs = [prefs.templates_path or os.path.join(addon_dir, "templates")]
            if prefs.debug_templates_path:
                dirs.append(prefs.debug_templates_path)
            self._template_file = None
            for d in dirs:
                candidate = os.path.join(d, template_name)
                if os.path.isfile(candidate):
                    self._template_file = candidate
                    break

            if self._template_file:
                try:
                    with open(self._template_file, "r") as f:
                        self._template_data = json.load(f)
                    self._template_mod_time = os.path.getmtime(self._template_file)
                except Exception as e:
                    self.report({'WARNING'}, f"Failed to load template: {e}")
            else:
                self.report({'WARNING'}, f"Template '{template_name}' not found in any folder.")
        context.window_manager.modal_handler_add(self)
        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        return {'RUNNING_MODAL'}
    
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

common_classes = [
    OBJECT_OT_import_hson
]

def register():
    for cls in common_classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(common_classes):
        bpy.utils.unregister_class(cls)