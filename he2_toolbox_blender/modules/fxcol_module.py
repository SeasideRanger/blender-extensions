import bpy
import json
import math
import mathutils
import os
import re
from collections import OrderedDict
from bpy.props import (
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty
)
from bpy.types import Operator, Panel, PropertyGroup
from bpy_extras.io_utils import ImportHelper, ExportHelper


# Update Callback: adjust object's dimensions based on FXCol properties (Only adjusts dimensions; no geometry change)
def update_dimensions(self, context):
    obj = context.object
    if not obj or not hasattr(obj, "fxcol"):
        return
    fx = obj.fxcol
    if fx.shape_type == 'CYLINDER':
        new_dimensions = (fx.radius * 2, fx.radius * 2, fx.halfHeight * 2)
    elif fx.shape_type in {'ISOTROPIC_OBB', 'ANISOTROPIC_OBB'}:
        # Use width for X, depth for Y, height for Z.
        new_dimensions = (fx.width, fx.depth, fx.height)
    else:
        new_dimensions = obj.dimensions
    obj.dimensions = new_dimensions

# Helper function to compute the Axis-Aligned Bounding Box (AABB) for an object
def compute_aabb(obj):
    size= obj.dimensions
    position= obj.location
    half = mathutils.Vector((size[0] / 2, size[1] / 2, size[2] / 2))
    aabb_min = position- half
    aabb_max = position+ half
    return aabb_min, aabb_max


# Custom Property Group (FXCol Properties)
class FXColProperties(PropertyGroup):
    # Enum for shape (non-editable in UI)
    shape_type: EnumProperty(
        name="Shape",
        description="Geometry shape (non-editable)",
        items=[
            ('CYLINDER', "Cylinder", ""),
            ('ISOTROPIC_OBB', "Isotropic OBB", ""),
            ('ANISOTROPIC_OBB', "Anisotropic OBB", "")
        ]
    )
    type_enum: EnumProperty(
        name="Parameter Type",
        description="Select the parameter type",
        items=[
            ('SCENE_PARAMETER_INDEX', "Scene Parameter", ""),
            ('LIGHT_PARAMETER_INDEX', "Light Parameter", "")
        ]
    )
    priority: IntProperty(
        name="Priority",
        default=0
    )
    # Cylinder properties
    radius: FloatProperty(
        name="Radius",
        default=1.0,
        update=update_dimensions
    )
    halfHeight: FloatProperty(
        name="Half Height",
        default=1.0,
        update=update_dimensions
    )
    cyl_border_thickness: FloatProperty(
        name="Border Thickness (Cylinder)",
        default=0.0
    )
    # Cube properties (for both OBB types)
    width: FloatProperty(
        name="Width",
        default=1.0,
        update=update_dimensions
    )
    height: FloatProperty(
        name="Height",
        default=1.0,
        update=update_dimensions
    )
    depth: FloatProperty(
        name="Depth",
        default=1.0,
        update=update_dimensions
    )
    cube_border_thickness: FloatProperty(
        name="Border Thickness (Cube)",
        default=0.0
    )
    # Additional for ANISOTROPIC_OBB
    maybe_width_and_height_border_thickness: FloatProperty(
        name="Maybe Width/Height Border Thickness",
        default=0.0
    )
    positive_depth_border_thickness: FloatProperty(
        name="Positive Depth Border Thickness",
        default=0.0
    )
    negative_depth_border_thickness: FloatProperty(
        name="Negative Depth Border Thickness",
        default=0.0
    )
    # Parameter properties
    sceneParameterIndex: IntProperty(
        name="Scene Parameter Index",
        default=0
    )
    interpolationTime: FloatProperty(
        name="Interpolation Time",
        default=0.0
    )
    lightParameterIndex: IntProperty(
        name="Light Parameter Index",
        default=0
    )
    # Store shape name from JSON
    original_name: StringProperty(
        name="Shape Name",
        default=""
    )

# Scene Property: Select Shape for new FX Collision (used in export UI)
bpy.types.Scene.fxcol_shape = EnumProperty(
    name="Select Shape",
    description="Select the collision shape to create",
    items=[
        ('CYLINDER', "Cylinder", ""),
        ('ISOTROPIC_OBB', "Isotropic OBB", ""),
        ('ANISOTROPIC_OBB', "Anisotropic OBB", "")
    ],
    default='ISOTROPIC_OBB'
)

# Helper formatting functions
def fmt3(value):
    return float(format(value, '.3f'))

def fmt6(value):
    # Rounds to 6 decimal places (for rotation values)
    return float(format(value, '.6f'))

def convert_position_from_json(pos):
    return mathutils.Vector((pos.get('x', 0), -pos.get('z', 0), pos.get('y', 0)))

def convert_rotation_from_json(rot_dict):
    # JSON quaternion is in XYZW order. mathutils.Quaternion expects (w, x, y, z).
    quat = mathutils.Quaternion((
        rot_dict.get('w', 1.0),
        rot_dict.get('x', 0.0),
        rot_dict.get('y', 0.0),
        rot_dict.get('z', 0.0)
    ))
    # Conversion quaternion: rotates 90° about X to change from JSON Y-up to Blender Z-up.
    conversion_quat = mathutils.Quaternion((1, 0, 0), math.radians(90))
    converted_quat = conversion_quat @ quat
    # Convert the resulting quaternion to Euler angles in XYZ order.
    return converted_quat.to_euler('XYZ')

def convert_rotation_export(euler):
    # Convert the object's Euler rotation to a quaternion.
    q = euler.to_quaternion()
    # Apply the inverse conversion: rotate -90° about the X-axis.
    conversion_quat = mathutils.Quaternion((1, 0, 0), -math.radians(90))
    original_quat = conversion_quat @ q
    return {
        'x': fmt6(original_quat.x),
        'y': fmt6(original_quat.y),
        'z': fmt6(original_quat.z),
        'w': fmt6(original_quat.w)
    }

# Get or Create FXCol Collection Based on File Name (for import)
def get_fxcol_collection_from_filepath(filepath):
    base = os.path.basename(filepath)
    coll_name, _ = os.path.splitext(base)
    if coll_name not in bpy.data.collections:
        coll = bpy.data.collections.new(coll_name)
        bpy.context.scene.collection.children.link(coll)
    else:
        coll = bpy.data.collections[coll_name]
    return coll

# Import Function: Create objects from JSON and set FXCol properties
def import_json(filepath):
    fxcol_coll = get_fxcol_collection_from_filepath(filepath)
    with open(filepath, 'r') as f:
        data = json.load(f)
    for shape_data in data['shapes']:
        shape_type = shape_data.get('shape', '')
        if shape_type == "CYLINDER":
            bpy.ops.mesh.primitive_cylinder_add(radius=1, depth=2)
        elif shape_type in ["ISOTROPIC_OBB", "ANISOTROPIC_OBB"]:
            bpy.ops.mesh.primitive_cube_add(size=2)
        else:
            continue
        obj = bpy.context.object
        obj.name = shape_data['name']
        obj.data.name = shape_data['name']
        fx = obj.fxcol
        fx.original_name = shape_data['name']
        if shape_type == "CYLINDER":
            fx.shape_type = 'CYLINDER'
            fx.radius = shape_data['extents'].get('radius', 1)
            fx.halfHeight = shape_data['extents'].get('halfHeight', 1)
            fx.cyl_border_thickness = shape_data['extents'].get('borderThickness', 0.0)
        elif shape_type == "ISOTROPIC_OBB":
            fx.shape_type = 'ISOTROPIC_OBB'
            fx.width = shape_data['extents'].get('width', 1)
            fx.height = shape_data['extents'].get('depth', 1) #height
            fx.depth = shape_data['extents'].get('height', 1) #depth
            fx.cube_border_thickness = shape_data['extents'].get('borderThickness', 0.0)
        elif shape_type == "ANISOTROPIC_OBB":
            fx.shape_type = 'ANISOTROPIC_OBB'
            fx.width = shape_data['extents'].get('width', 1)
            fx.height = shape_data['extents'].get('depth', 1) #height
            fx.depth = shape_data['extents'].get('height', 1) #depth
            fx.cube_border_thickness = shape_data['extents'].get('borderThickness', 0.0)
            fx.maybe_width_and_height_border_thickness = shape_data['extents'].get("maybeWidthAndHeightBorderThickness", 0.0)
            fx.positive_depth_border_thickness = shape_data['extents'].get("positiveDepthBorderThickness", 0.0)
            fx.negative_depth_border_thickness = shape_data['extents'].get("negativeDepthBorderThickness", 0.0)
        json_type = shape_data.get("type", "")
        if json_type == "SCENE_PARAMETER_INDEX":
            fx.type_enum = 'SCENE_PARAMETER_INDEX'
            params = shape_data.get("parameters", {})
            fx.sceneParameterIndex = params.get("sceneParameterIndex", 0)
            fx.interpolationTime = params.get("interpolationTime", 0.0)
        elif json_type == "LIGHT_PARAMETER_INDEX":
            fx.type_enum = 'LIGHT_PARAMETER_INDEX'
            params = shape_data.get("parameters", {})
            fx.lightParameterIndex = params.get("lightParameterIndex", 0)
        fx.priority = shape_data.get("priority", 0)
        obj.location = convert_position_from_json(shape_data['position'])
        obj.rotation_mode = 'XYZ'
        obj.rotation_euler = convert_rotation_from_json(shape_data['rotation'])
        update_dimensions(fx, bpy.context)
        if obj.name not in fxcol_coll.objects:
            fxcol_coll.objects.link(obj)
            try:
                bpy.context.scene.collection.objects.unlink(obj)
            except Exception:
                pass
    print("Import complete!")

# Create FX Collision based on scene.fxcol_shape
class CREATE_OT_fx_collision(Operator):
    bl_idname = "object.create_fx_collision"
    bl_label = "Create FX Collision"
    bl_description = "Create a new collision object based on the selected shape"
    
    def execute(self, context):
        scene = context.scene
        position= scene.cursor.location.copy()
        rot = scene.cursor.rotation_euler.to_quaternion()
        shape = scene.fxcol_shape
        if shape == 'CYLINDER':
            shape_name = "fxShapeCapsule"
        else:
            shape_name = "fxShapeCube"
        # Create new geometry.
        if shape == 'CYLINDER':
            bpy.ops.mesh.primitive_cylinder_add(radius=1, depth=2, location=loc, rotation=rot.to_euler())
        else:
            bpy.ops.mesh.primitive_cube_add(size=2, location=loc, rotation=rot.to_euler())
        new_obj = context.object
        new_obj.name = shape_name
        new_obj.data.name = shape_name
        fx = new_obj.fxcol
        fx.shape_type = shape
        # Set default collision values (user can later adjust via FXCol Properties panel)
        if shape == 'CYLINDER':
            fx.radius = 1.0
            fx.halfHeight = 1.0
        else:
            fx.width = 1.0
            fx.height = 1.0
            fx.depth = 1.0
        update_dimensions(fx, context)
        self.report({'INFO'}, f"FX Collision object '{shape_name}' created.")
        return {'FINISHED'}

# Exports FXCol objects from collections with ".fxcol" in their names
def export_json(export_filepath):
    # Use the directory of the user-selected file (the chosen file name is ignored)
    export_folder = os.path.dirname(export_filepath)
    if not os.path.exists(export_folder):
        os.makedirs(export_folder, exist_ok=True)

    export_count = 0
    # Iterate over all collections whose name contains ".fxcol"
    for coll in bpy.data.collections:
        if ".fxcol" in coll.name:
            export_data = OrderedDict()
            shapes = []
            fx_objects = []
            # Gather FXCol objects in the collection
            for obj in coll.objects:
                if hasattr(obj, "fxcol"):
                    fx_objects.append(obj)
                    fx = obj.fxcol
                    shape_entry = OrderedDict()
                    # Add missing unk1 and unk2 keys.
                    shape_entry["name"] = obj.name
                    shape_entry["shape"] = fx.shape_type
                    shape_entry["type"] = fx.type_enum
                    shape_entry["unk1"] = 0
                    shape_entry["priority"] = fx.priority
                    # Build extents.
                    extents = OrderedDict()
                    if fx.shape_type == 'CYLINDER':
                        extents["radius"] = fmt3(fx.radius)
                        extents["halfHeight"] = fmt3(fx.halfHeight)
                        extents["borderThickness"] = fmt3(fx.cyl_border_thickness)
                    else:
                        # For OBB shapes: width = X, depth = Y, height = Z.
                        extents["depth"] = fmt3(fx.height) #depth
                        extents["width"] = fmt3(fx.width)
                        extents["height"] = fmt3(fx.depth) #height
                        if fx.shape_type == 'ISOTROPIC_OBB':
                            extents["borderThickness"] = fmt3(fx.cube_border_thickness)
                        if fx.shape_type == 'ANISOTROPIC_OBB':
                            extents["maybeWidthAndHeightBorderThickness"] = fmt3(fx.maybe_width_and_height_border_thickness)
                            extents["positiveDepthBorderThickness"] = fmt3(fx.positive_depth_border_thickness)
                            extents["negativeDepthBorderThickness"] = fmt3(fx.negative_depth_border_thickness)
                    shape_entry["extents"] = extents
                    # Build parameters.
                    params = OrderedDict()
                    if fx.type_enum == 'SCENE_PARAMETER_INDEX':
                        params["sceneParameterIndex"] = fx.sceneParameterIndex
                        params["interpolationTime"] = fmt3(fx.interpolationTime)
                    elif fx.type_enum == 'LIGHT_PARAMETER_INDEX':
                        params["lightParameterIndex"] = fx.lightParameterIndex
                    shape_entry["parameters"] = params
                    # Export position and rotation.
                    shape_entry["unk2"] = "none"
                    shape_entry["position"] = {
                        "x": fmt3(obj.location.x),
                        "y": fmt3(obj.location.z),
                        "z": fmt3(-obj.location.y)
                    }
                    shape_entry["rotation"] = convert_rotation_export(obj.rotation_euler)
                    shapes.append(shape_entry)
            export_data["magic"] = 1180189519
            export_data["version"] = 1
            export_data["shapeCount"] = len(shapes)
            export_data["shapes"] = shapes

            # --- Compute kdTree data ---
            groups = {}
            # Group objects by their dimensions (rounded to 3 decimals)
            for idx, obj in enumerate(fx_objects):
                size= obj.dimensions
                key = f"{fmt3(size[0])}_{fmt3(size[1])}_{fmt3(size[2])}"
                if key not in groups:
                    groups[key] = {"indices": [], "aabb_min": None, "aabb_max": None}
                groups[key]["indices"].append(idx)
                aabb_min, aabb_max = compute_aabb(obj)
                if groups[key]["aabb_min"] is None:
                    groups[key]["aabb_min"] = aabb_min.copy()
                    groups[key]["aabb_max"] = aabb_max.copy()
                else:
                    groups[key]["aabb_min"].x = min(groups[key]["aabb_min"].x, aabb_min.x)
                    groups[key]["aabb_min"].y = min(groups[key]["aabb_min"].y, aabb_min.y)
                    groups[key]["aabb_min"].z = min(groups[key]["aabb_min"].z, aabb_min.z)
                    groups[key]["aabb_max"].x = max(groups[key]["aabb_max"].x, aabb_max.x)
                    groups[key]["aabb_max"].y = max(groups[key]["aabb_max"].y, aabb_max.y)
                    groups[key]["aabb_max"].z = max(groups[key]["aabb_max"].z, aabb_max.z)
            kdTreeLeaves = []
            for group in groups.values():
                leaf = OrderedDict()
                leaf["shapeCount"] = len(group["indices"])
                leaf["shapeOffset"] = min(group["indices"])  # the first index in this group
                leaf["aabbMin"] = {
                    "x": fmt3(group["aabb_min"].x),
                    "y": fmt3(group["aabb_min"].y),
                    "z": fmt3(group["aabb_min"].z)
                }
                leaf["aabbMax"] = {
                    "x": fmt3(group["aabb_max"].x),
                    "y": fmt3(group["aabb_max"].y),
                    "z": fmt3(group["aabb_max"].z)
                }
                kdTreeLeaves.append(leaf)
            export_data["kdTreeLeafCount"] = len(kdTreeLeaves)
            export_data["kdTreeLeaves"] = kdTreeLeaves
            total_shapes = len(shapes)
            export_data["kdTreeNodeCount"] = total_shapes  # one dummy node per shape
            kdTreeNodes = []
            for _ in range(total_shapes):
                kdTreeNodes.append({
                    "deadZoneStartCoordOrLeafIndexAndNodeType": -1,
                    "deadZoneEndCoord": 0.0
                })
            export_data["kdTreeNodes"] = kdTreeNodes
            # Build export file path using the collection name.
            export_filename = coll.name + ".json"
            full_export_path = os.path.join(export_folder, export_filename)
            with open(full_export_path, 'w') as f:
                json.dump(export_data, f, indent=4)
            print(f"Export complete for collection '{coll.name}'! Saved to: {full_export_path}")
            export_count += 1
    if export_count == 0:
        print("No collections with '.fxcol' in the name were found to export.")

# New Export Operator: folder selector only (no file renaming)
class EXPORT_OT_json(Operator, ExportHelper):
    bl_idname = "export_scene.json"
    bl_label = "Export FXCol"
    # Use a regular file selector (not folder-only)
    filename_ext: StringProperty(default=".json", options={'HIDDEN'})
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})
    
    def execute(self, context):
        export_json(self.filepath)
        self.report({'INFO'}, "FXCol export complete.")
        return {'FINISHED'}

# UI Panel: FXCol Tools in 3D View
class VIEW3D_PT_fx_import_panel(Panel):
    bl_label = "FXCol Tools"
    bl_idname = "VIEW3D_PT_fx_import_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HE2 Tools'
    
    def draw(self, context):
        layout = self.layout
        layout.operator("import_scene.json", text="Import FXCol")
        layout.operator("export_scene.json", text="Export FXCol")
        layout.prop(context.scene, "fxcol_shape", text="Select Shape")
        layout.operator("object.create_fx_collision", text="Create FX Collision")


# UI Panel for FXCol Properties in Object Properties
class OBJECT_PT_fxcol_properties(Panel):
    bl_label = "FXCol Properties"
    bl_idname = "OBJECT_PT_fxcol_properties"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object"
    bl_options = {'DEFAULT_CLOSED'}
    
    def draw(self, context):
        layout = self.layout
        obj = context.object
        if not obj or not hasattr(obj, "fxcol"):
            layout.label(text="No FXCol properties found.")
            return
        fx = obj.fxcol
        # Display the Shape Name and type fields
        layout.prop(fx, "original_name")
        # Make shape_type non-editable by displaying it as a label.
        layout.label(text=f"Shape: {fx.shape_type}")
        layout.prop(fx, "type_enum")
        layout.prop(fx, "priority")
        if fx.shape_type == 'CYLINDER':
            layout.label(text="Cylinder Properties:")
            layout.prop(fx, "radius")
            layout.prop(fx, "halfHeight")
            layout.prop(fx, "cyl_border_thickness")
        elif fx.shape_type == 'ISOTROPIC_OBB':
            layout.label(text="Isotropic OBB Properties:")
            layout.prop(fx, "width")
            layout.prop(fx, "height")
            layout.prop(fx, "depth")
            layout.prop(fx, "cube_border_thickness")
        elif fx.shape_type == 'ANISOTROPIC_OBB':
            layout.label(text="Anisotropic OBB Properties:")
            layout.prop(fx, "width")
            layout.prop(fx, "height")
            layout.prop(fx, "depth")
            layout.prop(fx, "maybe_width_and_height_border_thickness")
            layout.prop(fx, "positive_depth_border_thickness")
            layout.prop(fx, "negative_depth_border_thickness")
        if fx.type_enum == 'SCENE_PARAMETER_INDEX':
            layout.label(text="Scene Parameters:")
            layout.prop(fx, "sceneParameterIndex")
            layout.prop(fx, "interpolationTime")
        elif fx.type_enum == 'LIGHT_PARAMETER_INDEX':
            layout.label(text="Light Parameters:")
            layout.prop(fx, "lightParameterIndex")

# Import Operator
class IMPORT_OT_json(Operator, ImportHelper):
    bl_idname = "import_scene.json"
    bl_label = "Import FXCol"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})
    
    def execute(self, context):
        import_json(self.filepath)
        return {'FINISHED'}

classes = [
    FXColProperties,
    OBJECT_PT_fxcol_properties,
    IMPORT_OT_json,
    EXPORT_OT_json,
    VIEW3D_PT_fx_import_panel,
    CREATE_OT_fx_collision
]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.fxcol = PointerProperty(type=FXColProperties)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.fxcol_shape
    del bpy.types.Object.fxcol

if __name__ == "__main__":
    register()
