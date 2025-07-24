import bpy
import mathutils
import json
import math
import os


# PropertyGroup for SVCol parameters
class SVColProperties(bpy.types.PropertyGroup):
    priority: bpy.props.IntProperty(name="Priority", default=0)
    skySectorId: bpy.props.IntProperty(name="Sky Sector ID", default=0)
    sectorId_enabled: bpy.props.StringProperty(name="Sector ID (Enabled)", default="")
    sectorId_disabled: bpy.props.StringProperty(name="Sector ID (Disabled)", default="")
    width: bpy.props.FloatProperty(name="Width", default=1.0, update=lambda self, ctx: update_svcol_dimensions(self, ctx))
    height: bpy.props.FloatProperty(name="Height", default=1.0, update=lambda self, ctx: update_svcol_dimensions(self, ctx))
    depth: bpy.props.FloatProperty(name="Depth", default=1.0, update=lambda self, ctx: update_svcol_dimensions(self, ctx))
    collision_type: bpy.props.StringProperty(name="Collision Type", default="OBB", options={'HIDDEN'})

def update_svcol_dimensions(self, context):
    obj = context.object
    if obj and obj.type == 'MESH':
        obj.dimensions = (self.width, self.height, self.depth)

# Create collection from name.
def make_collection(coll_name):
    if coll_name in bpy.data.collections:
        return bpy.data.collections[coll_name]
    else:
        col = bpy.data.collections.new(coll_name)
        bpy.context.scene.collection.children.link(col)
        return col

# Assign custom property to mesh data.
def assign_custom_property(mesh, key, value):
    try:
        mesh[key] = int(value)
    except:
        mesh[key] = value

def convert_position_from_json(pos):
    return mathutils.Vector((
        pos.get('x', 0.0),
        -pos.get('z', 0.0),
        pos.get('y', 0.0)
    ))

def convert_rotation_from_json(rot_dict):
    q = mathutils.Quaternion((
        rot_dict.get("w", 1.0),
        rot_dict.get("x", 0.0),
        rot_dict.get("y", 0.0),
        rot_dict.get("z", 0.0)
    ))
    # Convert to Euler angles in XYZ order
    euler = q.to_euler("XYZ")
    # Rotate -90° about the Y axis.
    euler.y -= math.radians(90)
    return euler

def import_json(filepath):
    with open(filepath, 'r') as file:
        data = json.load(file)
    coll_name = data.get("name") or os.path.splitext(os.path.basename(filepath))[0]
    collection = make_collection(coll_name)
    
    shapes = data.get("shapes", [])
    for shape in shapes:
        if not isinstance(shape, dict):
            print("Skipping non-dictionary entry:", shape)
            continue
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object
        if cube.name not in collection.objects:
            collection.objects.link(cube)
        if cube.name in bpy.context.scene.collection.objects:
            bpy.context.scene.collection.objects.unlink(cube)
        cube.name = shape.get("name", "svShapeCube")
        cube.data.name = cube.name
        
        # Direct mapping for dimensions.
        scale = (
            shape.get("width", 1.0),
            shape.get("height", 1.0),
            shape.get("depth", 1.0)
        )
        cube.dimensions = scale
        cube.data.svcol_properties.width = scale[0]
        cube.data.svcol_properties.height = scale[1]
        cube.data.svcol_properties.depth = scale[2]
        
        pos = shape.get("position", {"x": 0.0, "y": 0.0, "z": 0.0})
        cube.location = convert_position_from_json(pos)
        
        rot = shape.get("rotation", {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0})
        cube.rotation_mode = 'XYZ'
        cube.rotation_euler = convert_rotation_from_json(rot)
        
        cube.data.svcol_properties.priority = shape.get("priority", 0)
        cube.data.svcol_properties.skySectorId = shape.get("skySectorId", 0)
        
        gs_filters = shape.get("groundSectorFilters", [])
        enabled = []
        disabled = []
        for gf in gs_filters:
            if isinstance(gf, dict):
                sid = gf.get("sectorId")
                if sid is not None:
                    if gf.get("enabled", False):
                        enabled.append(str(sid))
                    else:
                        disabled.append(str(sid))
        cube.data.svcol_properties.sectorId_enabled = ", ".join(enabled) if enabled else ""
        cube.data.svcol_properties.sectorId_disabled = ", ".join(disabled) if disabled else ""
        
        standard_keys = {"magic", "version", "shapeCount", "shapes", "name", "priority", "type",
                         "width", "height", "depth", "position", "rotation",
                         "aabbMin", "aabbMax", "skySectorId", "groundSectorFilterCount", "groundSectorFilters"}
        for key, value in shape.items():
            if key not in standard_keys:
                assign_custom_property(cube.data, key, value)
    print("Import complete.")


def export_rotation(obj):
    e = obj.rotation_euler.copy()
    # Rotate 90° about the Y axis.
    e.y += math.radians(90)
    # Convert the adjusted Euler angles to a quaternion.
    q = e.to_quaternion()
    # Return the quaternion as a dictionary in XYZW order.
    return {"x": q.x, "y": q.y, "z": q.z, "w": q.w}

def fmt10(value):
    return float(format(value, '.10f'))

def export_json(filepath):
    out_dir = os.path.dirname(filepath) or '.'
    for coll in bpy.data.collections:
        if ".svcol" not in coll.name.lower():
            continue
        shapes = []
        for obj in coll.objects:
            if obj.type != 'MESH' or not hasattr(obj.data, "svcol_properties"):
                continue
            name_val = obj.name.split('.')[0]
            exported_pos = {
                "x": fmt10(obj.location.x),
                "y": fmt10(obj.location.z),
                "z": fmt10(-obj.location.y)
            }
            exported_rot = export_rotation(obj)
            scale = obj.dimensions
            width  = fmt10(scale.x)
            height = fmt10(scale.y)
            depth  = fmt10(scale.z)
            aabbMin = {
                "x": fmt10(exported_pos["x"] - (width / 2)),
                "y": fmt10(exported_pos["y"] - (height / 2)),
                "z": fmt10(exported_pos["z"] - (depth / 2))
            }
            aabbMax = {
                "x": fmt10(exported_pos["x"] + (width / 2)),
                "y": fmt10(exported_pos["y"] + (height / 2)),
                "z": fmt10(exported_pos["z"] + (depth / 2))
            }
            sp = obj.data.svcol_properties
            priority = sp.priority
            skySectorId = sp.skySectorId
            enabled_str = sp.sectorId_enabled
            disabled_str = sp.sectorId_disabled
            groundSectorFilters = []
            if enabled_str:
                enabled_list = [int(x.strip()) for x in enabled_str.rstrip(',').split(',') if x.strip()]
                for sid in enabled_list:
                    groundSectorFilters.append({"sectorId": sid, "enabled": True})
            if disabled_str:
                disabled_list = [int(x.strip()) for x in disabled_str.rstrip(',').split(',') if x.strip()]
                for sid in disabled_list:
                    groundSectorFilters.append({"sectorId": sid, "enabled": False})
            groundSectorFilterCount = len(groundSectorFilters)
            additional_keys = {}
            standard_keys = {"priority", "skySectorId", "sectorId_enabled", "sectorId_disabled"}
            for key, val in obj.data.items():
                if key not in standard_keys and isinstance(val, (int, float, str, bool)):
                    additional_keys[key] = val
            shape_item = {
                "name": name_val,
                "priority": priority,
                "type": "OBB",
                "width": width,
                "height": height,
                "depth": depth,
                "position": exported_pos,
                "rotation": exported_rot,
                "aabbMin": aabbMin,
                "aabbMax": aabbMax,
                "skySectorId": skySectorId,
                "groundSectorFilterCount": groundSectorFilterCount,
                "groundSectorFilters": groundSectorFilters
            }
            shape_item.update(additional_keys)
            shapes.append(shape_item)
        if shapes:
            output = {
                "magic": 1398162255,
                "version": 1,
                "shapeCount": len(shapes),
                "shapes": shapes
            }
            out_path = os.path.join(out_dir, coll.name + ".json")
            with open(out_path, 'w') as file:
                json.dump(output, file, indent=4)
            print(f"Export complete. Saved to: {out_path}")
            

# Create Generic Cube
def create_generic_cube():
    bpy.ops.mesh.primitive_cube_add(size=100)
    cube = bpy.context.object
    cube.name = "svShapeCube"
    cube.data.name = cube.name
    return cube

# SVCol Panel
class SVColPanel(bpy.types.Panel):
    bl_label = "Sector Visibility Collision"
    bl_idname = "OBJECT_PT_svcol_import_export"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HE2 Tools'
    def draw(self, context):
        layout = self.layout
        layout.operator("object.import_json", text="Import SVCol")
        layout.operator("object.export_json", text="Export SVCol")
        layout.operator("object.create_generic_cube", text="Create svShape Cube")


# SVCol Properties Panel
class SVColPropertiesPanel(bpy.types.Panel):
    bl_label = "SVCol Properties"
    bl_idname = "OBJECT_PT_svcol_properties"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object"
    bl_options = {'DEFAULT_CLOSED'}
    def draw(self, context):
        layout = self.layout
        obj = context.object
        if not obj or obj.type != 'MESH':
            return
        layout.label(text=f"Object: {obj.name}")
        layout.label(text=f"Mesh: {obj.data.name}")
        layout.separator()
        layout.prop(obj.data.svcol_properties, "priority")
        layout.prop(obj.data.svcol_properties, "skySectorId")
        layout.prop(obj.data.svcol_properties, "sectorId_enabled")
        layout.prop(obj.data.svcol_properties, "sectorId_disabled")
        layout.separator()
        layout.label(text="Dimensions (linked):")
        layout.prop(obj.data.svcol_properties, "width")
        layout.prop(obj.data.svcol_properties, "depth")
        layout.prop(obj.data.svcol_properties, "height")
        layout.separator()
        layout.label(text="Collision Type: OBB")

class ImportJSONOperator(bpy.types.Operator):
    bl_idname = "object.import_json"
    bl_label = "Import SVCol"
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    def execute(self, context):
        import_json(self.filepath)
        return {'FINISHED'}
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class ExportJSONOperator(bpy.types.Operator):
    bl_idname = "object.export_json"
    bl_label = "Export SVCol"
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    def execute(self, context):
        export_json(self.filepath)
        return {'FINISHED'}
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class CreateGenericCubeOperator(bpy.types.Operator):
    bl_idname = "object.create_generic_cube"
    bl_label = "Create svShape Cube"
    def execute(self, context):
        create_generic_cube()
        return {'FINISHED'}

def register():
    bpy.utils.register_class(SVColProperties)
    bpy.types.Mesh.svcol_properties = bpy.props.PointerProperty(type=SVColProperties)
    bpy.utils.register_class(SVColPanel)
    bpy.utils.register_class(ImportJSONOperator)
    bpy.utils.register_class(ExportJSONOperator)
    bpy.utils.register_class(CreateGenericCubeOperator)
    bpy.utils.register_class(SVColPropertiesPanel)

def unregister():
    bpy.utils.unregister_class(SVColPropertiesPanel)
    bpy.utils.unregister_class(CreateGenericCubeOperator)
    bpy.utils.unregister_class(ExportJSONOperator)
    bpy.utils.unregister_class(ImportJSONOperator)
    bpy.utils.unregister_class(SVColPanel)
    del bpy.types.Mesh.svcol_properties
    bpy.utils.unregister_class(SVColProperties)

if __name__ == "__main__":
    register()
